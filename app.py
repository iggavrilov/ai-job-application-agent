import json
import hashlib
from pathlib import Path
from datetime import date
import pandas as pd
import streamlit as st
from sqlalchemy import select
from filelock import Timeout
from agent.db import database, JobRow, MatchRow, SearchRun, SearchDiagnostic, CandidateProfile, JobObservation, ManualImportLead, JobSourceStatus
from agent.models import Job, Match, Candidate
from agent.collectors import plain
from agent.discovery import discover
from agent.matching import region, ranking_key, evaluate
from agent.settings import rules, ROOT
from agent.registry import load_boards
from agent.source_registry import load_discovery_sources
from agent.manual_import import import_user_content
from agent.application_workflow import ApplicationPreparationError, _launch_from_dashboard, _launch_lever_from_dashboard, _matching_private_cv, _private_paths
from agent.application_submission import (
    ApplicationSubmissionError,
    approve_and_submit,
    load_application_review,
    reconcile_pending_submissions,
)

def _has_matching_private_cv(root, job_id, application_url):
    try:
        _matching_private_cv(root, job_id, application_url)
        return True
    except ApplicationPreparationError:
        return False


st.set_page_config(page_title='QA Job Agent',layout='wide')
st.title('QA Job Agent · MVP-01')
st.caption('Local discovery, review, and candidate-approved application preparation.')
try:
    boards, registry_errors = load_boards()
except Exception as exc:
    st.error(f'Cannot read board registry: {type(exc).__name__}. Check config/company_boards.yaml.')
    st.stop()
pending = sum(b['enabled'] and b['verification_status'] != 'verified' for b in boards)
if pending:
    st.warning(f'{pending} enabled employer boards await verification. Endpoint availability and Moldova hiring eligibility are separate checks.')
for error in registry_errors:
    st.warning(error)
try:
    search_config = rules()
except Exception as exc:
    st.error(f'Cannot read search rules: {type(exc).__name__}. Check config/search_rules.yaml.')
    st.stop()
use_ai = st.sidebar.checkbox('Use OpenAI when configured',value=False,help='Optional: sends structured professional profile and job text to OpenAI. Rules work without an API key.')
if st.button('Discover jobs now'):
    try:
        with st.spinner('Querying configured public employer boards…'):
            result = discover(use_ai=use_ai)
        st.write(result)
    except Timeout:
        st.info('A search is already running.')
    except Exception as exc:
        st.error(f'Search could not start: {type(exc).__name__}. Check local profile and configuration.')
try:
    factory = database()
except Exception as exc:
    st.error(f'Cannot open SQLite database: {type(exc).__name__}. Check DATABASE_URL and folder permissions.')
    st.stop()
sync_results = reconcile_pending_submissions(ROOT, factory)
if sync_results.get('Conflict', 0):
    st.warning(f"{sync_results['Conflict']} confirmed submission(s) need manual review-status reconciliation; existing manual choices were preserved.")
if sync_results.get('Unavailable', 0):
    st.warning(f"{sync_results['Unavailable']} confirmed submission status update(s) remain pending because local storage was unavailable.")
with factory() as session:
    runs = list(session.scalars(select(SearchRun).order_by(SearchRun.id.desc())))
    all_records = []
    for row in session.scalars(select(JobRow)):
        latest = session.scalar(select(MatchRow).where(MatchRow.job_id==row.id).order_by(MatchRow.id.desc()))
        if latest is None:
            continue
        job, match = Job.model_validate_json(row.payload), Match.model_validate_json(latest.payload)
        observations = [Job.model_validate_json(observation.payload) for observation in session.scalars(select(JobObservation).where(JobObservation.job_id==row.id))]
        if match.professional_score is None or match.version != 'mvp-01.6':
            snapshot = session.get(CandidateProfile,latest.candidate_id) if latest.candidate_id else None
            if snapshot:
                match = evaluate(job,Candidate.model_validate_json(snapshot.payload),search_config,False)
            else:
                match = match.model_copy(update={'in_main_recommendations':False,
                    'exclusion_reasons':['Historical match lacks a candidate snapshot; run discovery to reassess.']})
        application_url = str(job.employer_application_url or job.url)
        source_url = str(job.source_url or job.url)
        source_states = list(session.scalars(select(JobSourceStatus).where(JobSourceStatus.job_id==row.id)))
        if any(state.status == 'Observed in latest source feed' for state in source_states):
            availability = 'Observed in latest source feed'
        elif any(state.status == 'Expired per source' for state in source_states):
            availability = 'Expired per source evidence'
        elif any(state.status == 'Not observed in latest complete source scan' for state in source_states):
            availability = 'Not observed; closure unconfirmed'
        else:
            availability = 'Not checked'
        geographic = match.geographic_assessment
        can_prepare=(row.status=='Shortlisted' and job.platform.casefold() in {'greenhouse','lever'}
                     and _has_matching_private_cv(ROOT,row.id,str(job.url)))
        all_records.append({'ID':row.id,'Job title':job.title,'Company':job.company,'Location':job.location,'Remote status':job.remote,
          'Professional score':match.professional_score,'Professional fit':match.professional_fit or 'Needs Review',
          'Geographic eligibility':match.eligibility,'Eligibility assessment':geographic.classification,
          'Geographic reason':'; '.join(geographic.evidence) or match.geographic_reason,
          'Geographic priority':geographic.priority,'EU work authorization':geographic.eu_work_authorization,
          'US work authorization':geographic.us_work_authorization,
          'Remote permitted locations':', '.join(geographic.remote_permitted_locations) or 'Not stated',
          'Exclusion reasons':'; '.join(match.exclusion_reasons), 'Source postings':len(observations),
          'Recommendation':match.recommendation,'Salary':job.salary or 'Not published','Employment type':job.employment_type,
          'Publication date':job.published,'Availability':availability,'Prepare Application availability':'Available' if can_prepare else 'Not available',
          'Application link':application_url,'Source posting':source_url,
          'Source name':job.source_name or job.platform,'Source type':job.source_type,'Source':job.platform,
          'Last verified':job.source_checked_at or row.last_checked,'Discovered':row.first_discovered[:10],
          'Region':region(job.location + ' ' + job.title),'Last checked':row.last_checked[:19],
          'Review status':row.status,'_job':job,'_match':match,'_observations':observations})
    records = [record for record in all_records if record['_match'].in_main_recommendations]
    cols = st.columns(7)
    metrics = [('Total vacancies',len(records)),('New vacancies',sum(r['Review status']=='New' for r in records)),
      ('Applied',sum(r['Review status']=='Applied' for r in all_records)),
      ('Strong professional fit',sum(r['Professional fit']=='Strong Match' for r in records)),('Good professional fit',sum(r['Professional fit']=='Good Match' for r in records)),
      ('Geography needs review',sum(r['Eligibility assessment']=='Potentially Eligible — Needs Review' for r in records)),('Last successful search',next((r.finished[:19] for r in runs if r.status=='Success'),'Never'))]
    for col,(label,value) in zip(cols,metrics):
        col.metric(label,value)
    st.caption(f'Main recommendations: {len(records)}; excluded/audit records: {len(all_records)-len(records)}. These counts do not establish suitability or hiring eligibility.')
    with st.expander('Discovery quality and employer diversity', expanded=True):
        latest_diagnostic = session.scalar(select(SearchDiagnostic).order_by(SearchDiagnostic.id.desc()))
        discovery_quality = json.loads(latest_diagnostic.payload) if latest_diagnostic else {}
        totals = discovery_quality.get('totals', {})
        clarified = {'Potentially Eligible — Needs Review', 'Eligible If Relocating', 'Needs Review'}
        quality_metrics = [
            ('Postings fetched (latest run)', runs[0].discovered if runs else 0),
            ('Unique Software QA records', totals.get('unique_records', len(all_records))),
            ('Professionally suitable', totals.get('unique_main_professionally_suitable', sum(r['Professional fit'] in {'Strong Match', 'Good Match'} for r in records))),
            ('Geographically eligible', totals.get('unique_main_confirmed_geographically_eligible', sum(r['Eligibility assessment'] == 'Confirmed Eligible' for r in records))),
            ('Needs eligibility clarification', totals.get('unique_main_needs_eligibility_clarification', sum(r['Eligibility assessment'] in clarified for r in records))),
            ('Excluded / audit records', totals.get('unique_excluded_records', len(all_records) - len(records))),
            ('Employers represented', totals.get('unique_employers_represented', len({r['Company'] for r in records}))),
        ]
        quality_cols = st.columns(4)
        for col, (label, value) in zip(quality_cols, quality_metrics):
            col.metric(label, value)
        if discovery_quality:
            st.caption(f"Sources attempted: {totals.get('active_sources', 0)} · successful: {totals.get('committed_boards', 0)} · incomplete/failed: {totals.get('failed_boards', 0)}. Incomplete scans are not presented as complete coverage.")
        if records:
            employer_counts = pd.DataFrame([
                {'Employer': name, 'Recommended vacancies': count}
                for name, count in sorted(
                    __import__('collections').Counter(r['Company'] for r in records).items(),
                    key=lambda item: (-item[1], item[0].casefold()))
            ])
            st.caption('Employer concentration is shown for transparency; legitimate vacancies from the same employer remain visible.')
            st.table(employer_counts)
    view = st.sidebar.selectbox('Vacancy view',['Recommendations','Excluded / audit','All stored vacancies'])
    visible = records if view == 'Recommendations' else [r for r in all_records if not r['_match'].in_main_recommendations] if view == 'Excluded / audit' else all_records
    current_selection = st.session_state.get('selected_vacancy_id')
    if current_selection is not None and current_selection not in {record['ID'] for record in visible}:
        st.session_state.pop('selected_vacancy_id', None)
        st.info('The selected vacancy is no longer in the current filtered results. Select a row to view its details.')
    if visible:
        filtered = visible
        for field in ['Region','Location','Remote status','Availability','Eligibility assessment','Professional fit','Prepare Application availability','Source type','Recommendation','Source','Review status']:
            values = st.sidebar.multiselect(field,sorted({r[field] for r in visible}))
            if values:
                filtered = [r for r in filtered if r[field] in values]
        country = st.sidebar.text_input('Country/region (location text)')
        title = st.sidebar.text_input('Job title contains')
        minimum = st.sidebar.slider('Minimum professional score',0,100,0)
        after = st.sidebar.date_input('Discovered on or after',value=date(2020,1,1))
        filtered = [r for r in filtered if country.lower() in r['Location'].lower() and title.lower() in r['Job title'].lower() and (r['Professional score'] or 0)>=minimum and r['Discovered']>=after.isoformat()]
        order = st.sidebar.selectbox('Rank by',['Professional score','Geographic priority'])
        remote_preferred = search_config.get('remote_preferred', True)
        filtered.sort(key=lambda r:ranking_key(r,order,remote_preferred))
        if remote_preferred:
            st.caption('Default priority: confirmed Moldova remote access, possible Moldova remote access (Needs Review), Moldova, Romania, then all EU countries. Professional fit is shown separately.')
        table = pd.DataFrame([{k:v for k,v in r.items() if not k.startswith('_')} for r in filtered])
        # Streamlit reports selected row positions. Give each visible ID set its own
        # widget key, then immediately translate the event to the stable DB ID.
        # This prevents a stale row position from selecting a different job after
        # filters change, while session state preserves the ID across ordinary reruns.
        visible_ids = table['ID'].astype(int).tolist()
        table_key = 'vacancy-table-' + hashlib.sha256(
            json.dumps(visible_ids).encode('utf-8')
        ).hexdigest()[:12]
        table_event = st.dataframe(
            table, hide_index=True, height=420,
            column_config={'Application link':st.column_config.LinkColumn('Application URL'),
                           'Source posting':st.column_config.LinkColumn('Source posting')},
            use_container_width=True, key=table_key,
            on_select='rerun', selection_mode='single-row')
        selected_id = st.session_state.get('selected_vacancy_id')
        selected_rows = table_event.selection.rows
        if selected_rows:
            row_index = selected_rows[0]
            if 0 <= row_index < len(visible_ids):
                selected_id = visible_ids[row_index]
                st.session_state['selected_vacancy_id'] = selected_id
        filtered_by_id = {record['ID']:record for record in filtered}
        if selected_id is not None and selected_id not in filtered_by_id:
            st.session_state.pop('selected_vacancy_id', None)
            selected_id = None
            st.info('The selected vacancy is no longer in the current filtered results. Select a row to view its details.')
        if filtered:
            selected = filtered_by_id.get(selected_id)
            if selected is None:
                st.caption('Select a vacancy row above to review its details.')
            else:
                st.success(f"Selected vacancy #{selected['ID']}: {selected['Company']} — {selected['Job title']}")
                job, match = selected['_job'], selected['_match']
                with st.container(border=True):
                    st.markdown('### Job details')
                    st.caption(f"Selected vacancy ID: {selected['ID']}")
                    st.subheader(job.title)
                    st.write(f'**{job.company}** · {job.location} · {job.remote} · {job.employment_type}')
                    st.link_button('Open job posting',str(job.url),use_container_width=False)

                    st.markdown('#### Professional fit')
                    st.write(f"**{match.professional_fit or 'Needs Review'}** · Score: {match.professional_score if match.professional_score is not None else 'Not scored'}")
                    st.write(match.professional_reason or match.explanation)
                    st.write('**Matching skills:**',', '.join(match.matching_skills) or 'No explicit evidence detected')
                    st.write('**Skills to review:**',', '.join(match.missing_skills) or 'None identified; this is not proof all requirements are met')
                    if match.exclusion_reasons:
                        st.write('**Recommendation concerns:**','; '.join(match.exclusion_reasons))
                    st.caption('Evaluation method: '+match.method)

                    st.markdown('#### Geographic eligibility')
                    geographic = match.geographic_assessment
                    st.write(f'**{geographic.classification}**')
                    st.write(f'**EU work authorization:** {geographic.eu_work_authorization}')
                    st.write(f'**US work authorization:** {geographic.us_work_authorization}')
                    st.write(f'**Required residence:** {', '.join(geographic.required_residence_countries) or 'Not stated'}')
                    st.write(f'**Remote permitted locations:** {', '.join(geographic.remote_permitted_locations) or 'Not stated'}')
                    st.write(f'**Relocation:** {geographic.relocation_requirement}')
                    st.write(f'**Visa sponsorship:** {geographic.visa_sponsorship} · **Contractor:** {geographic.contractor_eligibility} · **EOR:** {geographic.employer_of_record_availability}')
                    st.write('**Evidence:** ' + ('; '.join(geographic.evidence) or match.geographic_reason))

                    st.markdown('#### Source and availability')
                    st.write(f'**Source:** {job.source_name or job.platform} ({job.source_type})')
                    st.write(f'**Last verified:** {job.source_checked_at or selected['Last checked']} · **Availability evidence:** {selected['Availability']}')
                    if job.source_evidence_url:
                        st.link_button('Open source verification evidence', str(job.source_evidence_url))
                    st.markdown('#### Requirements')
                    st.write(job.requirements or match.mandatory_requirements or 'No separate requirements were published.')
                    with st.expander('Full job description'):
                        st.text(plain(job.description) or 'No description was published.')

                    if selected['Review status'] == 'Shortlisted':
                        if match.geographic_assessment.classification == 'Confirmed Ineligible':
                            st.info('Application preparation is disabled because verified geographic evidence marks this vacancy Confirmed Ineligible.')
                        elif job.platform.casefold() == 'lever':
                            st.info('Supervised Lever preparation fills only unambiguous verified identity fields and a vacancy-matched private CV. Legal, salary, location and consent questions stay for manual review; Lever submission is disabled.')
                            if not _has_matching_private_cv(ROOT, selected['ID'], str(job.url)):
                                st.warning('A tailored private CV with application_metadata.json matching this vacancy ID and application URL is required before preparation.')
                            elif st.button('Prepare Application', key=f"prepare-lever-{selected['ID']}"):
                                try:
                                    _launch_lever_from_dashboard(selected['ID'])
                                    st.success('Visible Lever form preparation started. Final submission is disabled.')
                                except (ApplicationPreparationError, OSError) as exc:
                                    st.error(str(exc))
                        else:
                            if selected['ID'] != 4 or job.company.casefold() != 'bitdefender':
                                st.info('Greenhouse preparation fills only verified fields, attaches the vacancy-matched private PDF, and stops for unknown questions. Submission is disabled for this employer pending separate review and approval.')
                            if not _has_matching_private_cv(ROOT, selected['ID'], str(job.url)):
                                st.warning('A private tailored PDF with application_metadata.json matching this vacancy ID and application URL is required before preparation.')
                            if match.geographic_assessment.classification == 'Potentially Eligible — Needs Review':
                                st.warning('Geographic eligibility is Needs Review. Preparation is provisional; do not proceed until the employer confirms location and work-authorization requirements.')
                            current_app_state = {}
                            _, _, state_path = _private_paths(ROOT, selected['ID'])
                            if state_path.is_file():
                                try:
                                    current_app_state = json.loads(state_path.read_text(encoding='utf-8'))
                                except (OSError, json.JSONDecodeError):
                                    current_app_state = {}
                            submission_terminal = current_app_state.get('status') in {
                                'Awaiting approval', 'Ready for Approval', 'Approved', 'Submitting',
                                'Submitted', 'Submission Uncertain'
                            }
                            if submission_terminal:
                                st.info('This application already has a prepared or submission state. Review or reconcile it before preparing again.')
                            elif _has_matching_private_cv(ROOT, selected['ID'], str(job.url)) and st.button('Prepare Application', key=f"prepare-application-{selected['ID']}"):
                                try:
                                    _launch_from_dashboard(selected['ID'])
                                    if selected['ID'] == 4 and job.company.casefold() == 'bitdefender':
                                        st.success('Visible Greenhouse browser launched. The application remains separate from the vacancy review status.')
                                    else:
                                        st.success('Visible Greenhouse preparation launched. Submission is disabled for this employer.')
                                except (ApplicationPreparationError, OSError) as exc:
                                    st.error(str(exc))
                        _, preview_path, state_path = _private_paths(ROOT, selected['ID'])
                        if state_path.is_file():
                            try:
                                application_state, review_snapshot, review_hash = load_application_review(selected['ID'], ROOT)
                                st.markdown('#### Supervised application')
                                workflow_status = application_state.get('status', 'Unknown')
                                st.write(f"**Application workflow status:** {workflow_status}")
                                completed_fields = application_state.get('completed_fields', [])
                                candidate_fields = application_state.get('candidate_input_fields', [])
                                st.write('**Fields completed:** ' + (', '.join(completed_fields) if completed_fields else 'Preparation not yet complete'))
                                st.write('**Questions requiring candidate input:**')
                                for item in candidate_fields:
                                    st.write(f"- {item.get('label', 'Unlabeled question')}: {item.get('note', 'Review required')}")
                                if preview_path.is_file():
                                    preview = json.loads(preview_path.read_text(encoding='utf-8'))
                                    with st.expander('Open application preview'):
                                        st.json(preview)
                                        screenshot = preview.get('screenshot_path')
                                        if screenshot and Path(screenshot).is_file():
                                            st.image(screenshot, caption='Private application preview')
                                if workflow_status == 'Ready for Approval' and review_snapshot and review_hash:
                                    st.markdown('##### Review exact application data')
                                    st.write(f"**Employer:** {review_snapshot['employer']}")
                                    st.write(f"**Job title:** {review_snapshot['job_title']}")
                                    st.link_button('Open application URL', review_snapshot['application_url'])
                                    identity = review_snapshot['candidate']
                                    st.write(f"**Candidate:** {identity['name']} (preferred name: {identity['preferred_first_name'] or 'Not set'})")
                                    st.write(f"**Email / phone:** {identity['email']} · {identity['phone']}")
                                    st.write(f"**Current residence:** {identity['current_residence']}")
                                    form_data = review_snapshot['form_data']
                                    st.write(f"**Application location:** {form_data['location_city']}")
                                    st.write(f"**Salary expectation:** {form_data['salary_expectation']}")
                                    st.write(f"**Work authorization:** {form_data['work_authorization']}")
                                    st.write(f"**Recruitment data-processing consent:** {form_data['recruitment_data_processing_consent']}")
                                    cv_info = review_snapshot['attached_cv']
                                    st.write(f"**Attached CV:** {cv_info['filename']} · SHA-256 {cv_info['sha256'][:12]}")
                                    st.caption(f"Snapshot {review_hash[:16]} is bound to vacancy #{selected['ID']} and this exact application URL.")
                                    with st.expander('All verified form fields'):
                                        st.dataframe(review_snapshot.get('form_fields', []),
                                                     hide_index=True, use_container_width=True)
                                    with st.form(key=f"approve-submit-form-{selected['ID']}-{review_hash[:12]}"):
                                        explicitly_confirmed = st.checkbox(
                                            f"I reviewed this application for vacancy #{selected['ID']} at {review_snapshot['application_url']} and approve submitting the displayed information and CV."
                                        )
                                        submit_clicked = st.form_submit_button('Approve & Submit')
                                    if submit_clicked:
                                        if not explicitly_confirmed:
                                            st.error('Check the exact-application confirmation before approving submission.')
                                        else:
                                            try:
                                                final_state = approve_and_submit(
                                                    selected['ID'], review_hash,
                                                    candidate_confirmed=explicitly_confirmed,
                                                    root=ROOT, factory=factory)
                                                st.info(f"Application workflow state: {final_state['status']}")
                                            except (ApplicationSubmissionError, ApplicationPreparationError, OSError) as exc:
                                                st.error(str(exc))
                                            st.rerun()
                                elif workflow_status == 'Needs Review':
                                    st.warning('Resolve the listed candidate questions or rerun preparation after updating the private answers. Submission is unavailable.')
                                elif workflow_status == 'Submission Uncertain':
                                    st.error('The employer may have received this application. Check the employer site manually; automatic retry is blocked.')
                                elif workflow_status == 'Submission Failed':
                                    st.warning('Submission stopped before dispatch. Reprepare and review the exact application before another approval.')
                                elif workflow_status == 'Submitted':
                                    sync_status = application_state.get('review_status_sync', {}).get('status', 'Pending')
                                    if sync_status == 'Synced':
                                        st.success('The employer submission is confirmed and the vacancy review status is Applied.')
                                    elif sync_status == 'Conflict':
                                        st.warning('The employer submission is confirmed. A different manual vacancy review choice was preserved; reconcile it manually.')
                                    else:
                                        st.info('The employer submission is confirmed. The SQLite review status update is pending safe recovery; no submission retry will occur.')
                                elif workflow_status == 'Approved':
                                    st.warning('Approval was recorded, but no completed submission is recorded. Reprepare for a fresh review and approval.')
                                else:
                                    st.caption('Prepare the application to reach a reviewable state. Application state is separate from the vacancy review status.')
                            except (OSError, json.JSONDecodeError, ApplicationSubmissionError, ApplicationPreparationError):
                                st.warning('An application preparation session exists but its private preview cannot be read.')
                    elif selected['Review status'] == 'Applied':
                        _, _, state_path = _private_paths(ROOT, selected['ID'])
                        try:
                            applied_state = json.loads(state_path.read_text(encoding='utf-8'))
                        except (OSError, json.JSONDecodeError):
                            applied_state = {}
                        if applied_state.get('status') == 'Submitted':
                            st.markdown('#### Application workflow')
                            st.write('**Application workflow status:** Submitted')
                            sync_status = applied_state.get('review_status_sync', {}).get('status', 'Pending')
                            if sync_status == 'Synced':
                                st.success('Employer confirmation is verified; vacancy review status is Applied.')
                            elif sync_status == 'Conflict':
                                st.warning('Employer confirmation is verified; the previous manual review choice was preserved for reconciliation.')
                            else:
                                st.info('Employer confirmation is verified; SQLite status synchronization is pending recovery.')
                    if len(selected['_observations']) > 1:
                        st.markdown('#### Additional source postings')
                        for index, observation in enumerate(selected['_observations'],1):
                            st.link_button(f'Source posting {index}',str(observation.url),key=f"source-{selected['ID']}-{index}")

                    review_statuses = ['New','Reviewed','Shortlisted','Rejected','Applied']
                    if selected['Review status'] not in review_statuses:
                        review_statuses.append(selected['Review status'])  # Preserve legacy labels until the user changes them.
                    new_status = st.selectbox('Update review status',review_statuses,
                                              index=review_statuses.index(selected['Review status']),
                                              key=f"review-status-{selected['ID']}")
                    if st.button('Save review status',key=f"save-review-status-{selected['ID']}"):
                        session.get(JobRow,selected['ID']).status = new_status
                        session.commit()
                        st.rerun()
        else:
            st.info('No vacancies match the current filters.')
    else:
        st.info('No stored relevant vacancies in this view. Run discovery or inspect Excluded / audit for preserved reasons.')
    with st.expander('Manual LinkedIn lead import — user supplied only'):
        st.caption('Paste job alert text or one or more LinkedIn job URLs. No LinkedIn page is opened, fetched, or automated. Raw text and email content are not stored.')
        pasted_content = st.text_area('Job URLs or alert text', key='manual-linkedin-import-content')
        if st.button('Import user-provided leads', key='import-linkedin-leads'):
            leads = import_user_content(session, pasted_content)
            session.commit()
            st.success(f'Imported or refreshed {len(leads)} sanitized lead(s); all remain unverified until matched to an official employer posting.')
        imported = list(session.scalars(select(ManualImportLead).order_by(ManualImportLead.id.desc())))
        if imported:
            st.dataframe([{'ID':lead.id,'Title':lead.title,'Company':lead.company,'Location':lead.location,
                           'LinkedIn URL':lead.linkedin_url,'Employer application URL':lead.employer_application_url or 'Not supplied',
                           'Status':lead.status,'Possible verified ATS vacancy ID':lead.matched_job_id or 'No local exact match'}
                          for lead in imported], hide_index=True, use_container_width=True,
                         column_config={'LinkedIn URL':st.column_config.LinkColumn(),
                                        'Employer application URL':st.column_config.LinkColumn()})
        else:
            st.caption('No user-provided leads imported yet.')
    with st.expander('Discovery source registry and health'):
        try:
            configured_sources, source_errors = load_discovery_sources()
            latest = session.scalar(select(SearchDiagnostic).order_by(SearchDiagnostic.id.desc()))
            latest_diag = json.loads(latest.payload) if latest else {}
            latest_reports = latest_diag.get('boards', [])
            source_rows = []
            for item in configured_sources:
                source_reports = [report for report in latest_reports if report.get('platform') == item['id']]
                successful_report = next((report for report in reversed(source_reports) if report.get('committed')), None)
                latest_report = source_reports[-1] if source_reports else None
                source_rows.append({
                    'Source': item['name'], 'Type': item['source_type'], 'Coverage': ', '.join(item['coverage']),
                    'Status': item['status'], 'Official source': item['official_url'],
                    'Evidence': item.get('evidence_url') or item['official_url'],
                    'Access': item['permitted_access'], 'Rate limit': item['rate_limit'],
                    'Last successful check': (successful_report.get('source_checked_at') if successful_report else None) or item.get('last_successful_check') or 'Not checked',
                    'Last error': (latest_report.get('error') if latest_report and not latest_report.get('committed') else None) or item.get('last_error') or 'None recorded',
                })
            st.dataframe(pd.DataFrame(source_rows), hide_index=True, use_container_width=True,
                         column_config={'Official source':st.column_config.LinkColumn(), 'Evidence':st.column_config.LinkColumn()})
            for error in source_errors:
                st.warning(error)
        except Exception as exc:
            st.warning(f'Source registry could not be loaded: {type(exc).__name__}.')
    with st.expander('Search history'):
        st.dataframe([{'Run':r.id,'Started':r.started,'Finished':r.finished,'Status':r.status,'Fetched':r.discovered,'New relevant':r.new,'Errors':r.errors} for r in runs])
    with st.expander('Discovery diagnostics'):
        diagnostic = session.scalar(select(SearchDiagnostic).order_by(SearchDiagnostic.id.desc()))
        if diagnostic:
            st.json(json.loads(diagnostic.payload))
        else:
            st.caption('Run discovery to record source, filtering, eligibility and persistence counts.')

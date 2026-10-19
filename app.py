import json
import hashlib
from datetime import date
import pandas as pd
import streamlit as st
from sqlalchemy import select
from filelock import Timeout
from agent.db import database, JobRow, MatchRow, SearchRun, SearchDiagnostic, CandidateProfile, JobObservation
from agent.models import Job, Match, Candidate
from agent.collectors import plain
from agent.discovery import discover
from agent.matching import region, ranking_key, evaluate
from agent.settings import rules
from agent.registry import load_boards

st.set_page_config(page_title='QA Job Agent',layout='wide')
st.title('QA Job Agent · MVP-01')
st.caption('Local discovery and review. Eligibility requires evidence; application submission is unavailable.')
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
with factory() as session:
    runs = list(session.scalars(select(SearchRun).order_by(SearchRun.id.desc())))
    all_records = []
    for row in session.scalars(select(JobRow)):
        latest = session.scalar(select(MatchRow).where(MatchRow.job_id==row.id).order_by(MatchRow.id.desc()))
        if latest is None:
            continue
        job, match = Job.model_validate_json(row.payload), Match.model_validate_json(latest.payload)
        observations = [Job.model_validate_json(observation.payload) for observation in session.scalars(select(JobObservation).where(JobObservation.job_id==row.id))]
        if match.professional_score is None or match.version != 'mvp-01.5':
            snapshot = session.get(CandidateProfile,latest.candidate_id) if latest.candidate_id else None
            if snapshot:
                match = evaluate(job,Candidate.model_validate_json(snapshot.payload),search_config,False)
            else:
                match = match.model_copy(update={'in_main_recommendations':False,
                    'exclusion_reasons':['Historical match lacks a candidate snapshot; run discovery to reassess.']})
        all_records.append({'ID':row.id,'Job title':job.title,'Company':job.company,'Location':job.location,'Remote status':job.remote,
          'Professional score':match.professional_score,'Professional fit':match.professional_fit or 'Needs Review',
          'Geographic eligibility':match.eligibility,'Geographic reason':match.geographic_reason,
          'Geographic priority':match.geographic_priority,'Exclusion reasons':'; '.join(match.exclusion_reasons),
          'Source postings':len(observations),
          'Recommendation':match.recommendation,'Salary':job.salary or 'Not published',
          'Publication date':job.published,'Application link':str(job.url),'Source':job.platform,'Discovered':row.first_discovered[:10],
          'Region':region(job.location + ' ' + job.title),'Last checked':row.last_checked[:19],'Review status':row.status,'_job':job,'_match':match,'_observations':observations})
    records = [record for record in all_records if record['_match'].in_main_recommendations]
    cols = st.columns(6)
    metrics = [('Total vacancies',len(records)),('New vacancies',sum(r['Review status']=='New' for r in records)),
      ('Strong professional fit',sum(r['Professional fit']=='Strong Match' for r in records)),('Good professional fit',sum(r['Professional fit']=='Good Match' for r in records)),
      ('Geography needs review',sum(r['Geographic eligibility']=='Needs Review' for r in records)),('Last successful search',next((r.finished[:19] for r in runs if r.status=='Success'),'Never'))]
    for col,(label,value) in zip(cols,metrics):
        col.metric(label,value)
    st.caption(f'Main recommendations: {len(records)}; excluded/audit records: {len(all_records)-len(records)}. These counts do not establish suitability or hiring eligibility.')
    view = st.sidebar.selectbox('Vacancy view',['Recommendations','Excluded / audit','All stored vacancies'])
    visible = records if view == 'Recommendations' else [r for r in all_records if not r['_match'].in_main_recommendations] if view == 'Excluded / audit' else all_records
    current_selection = st.session_state.get('selected_vacancy_id')
    if current_selection is not None and current_selection not in {record['ID'] for record in visible}:
        st.session_state.pop('selected_vacancy_id', None)
        st.info('The selected vacancy is no longer in the current filtered results. Select a row to view its details.')
    if visible:
        filtered = visible
        for field in ['Region','Location','Remote status','Recommendation','Source','Review status']:
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
            column_config={'Application link':st.column_config.LinkColumn('Original posting')},
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
                    st.write(f'**{match.eligibility}** — {match.geographic_reason}')

                    st.markdown('#### Requirements')
                    st.write(job.requirements or match.mandatory_requirements or 'No separate requirements were published.')
                    with st.expander('Full job description'):
                        st.text(plain(job.description) or 'No description was published.')
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
    with st.expander('Search history'):
        st.dataframe([{'Run':r.id,'Started':r.started,'Finished':r.finished,'Status':r.status,'Fetched':r.discovered,'New relevant':r.new,'Errors':r.errors} for r in runs])
    with st.expander('Discovery diagnostics'):
        diagnostic = session.scalar(select(SearchDiagnostic).order_by(SearchDiagnostic.id.desc()))
        if diagnostic:
            st.json(json.loads(diagnostic.payload))
        else:
            st.caption('Run discovery to record source, filtering, eligibility and persistence counts.')

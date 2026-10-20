"""Streamlit rendering for the candidate-controlled manual application tracker."""
from __future__ import annotations

from datetime import date
import html

import pandas as pd
import streamlit as st
from sqlalchemy import select

from agent.db import JobRow, MatchRow, ManualVacancyLead
from agent.manual_applications import (
    LEAD_EVIDENCE_STATUSES, MANUAL_STATUSES, ImportRejected, add_manual_vacancy, import_vacancy,
    parse_bulk_urls, priority_group, record_for_job, set_manual_application,
)
from agent.models import Job, Match


def _country_city(location: str) -> tuple[str, str]:
    value = (location or '').strip()
    low = value.casefold()
    country_map = (
        ('Moldova', ('moldova', 'moldavia')), ('Romania', ('romania', 'românia')),
        ('Ukraine', ('ukraine', 'україна')), ('United Kingdom', ('united kingdom', 'uk')),
        ('United States', ('united states', 'usa', ' us ')),
        ('EU / Europe', ('european union', 'europe', 'eu-wide', 'eu remote')),
    )
    country = next((name for name, needles in country_map if any(x in low for x in needles)), 'Unknown')
    if value.casefold() in {'unknown', 'remote', 'worldwide', 'global'}:
        return country, 'Unknown'
    if country != 'Unknown':
        parts = [part.strip() for part in value.split(',') if part.strip()]
        city = parts[0] if len(parts) > 1 else 'Unknown'
        if city.casefold() in {'remote', 'europe', 'eu', 'worldwide'}:
            city = 'Unknown'
        return country, city
    return country, 'Unknown'


def _rows(session):
    output = []
    for row in session.scalars(select(JobRow).order_by(JobRow.id)):
        try:
            job = Job.model_validate_json(row.payload)
        except Exception:
            continue
        match_row = session.scalar(select(MatchRow).where(MatchRow.job_id == row.id).order_by(MatchRow.id.desc()))
        match = Match.model_validate_json(match_row.payload) if match_row else None
        tracking = record_for_job(session, row.id)
        lead = session.scalar(select(ManualVacancyLead).where(ManualVacancyLead.job_id == row.id))
        country, city = _country_city(job.location)
        assessment = match.geographic_assessment.classification if match else 'Unknown'
        geography_reason = '; '.join(match.geographic_assessment.evidence) if match else ''
        output.append({
            'ID': row.id, 'Company': job.company, 'Job title': job.title,
            'Country': country, 'City': city, 'Work arrangement': job.remote or 'Unknown',
            'Professional match': match.professional_fit if match and match.professional_fit else 'Unknown',
            'Match score': match.professional_score if match else None,
            'Geographic eligibility': assessment,
            'Geographic evidence': geography_reason or (match.geographic_reason if match else 'No assessment available'),
            'Listing source': job.source_name or job.platform or 'Unknown',
            'Source verification': lead.evidence_status if lead else ('Structured job posting' if job.source_type != 'manual-import' else 'Unclassified manual import'),
            'Application source': tracking.source if tracking else 'Not recorded',
            'Job URL': str(job.url), 'Date discovered': row.first_discovered[:10] if row.first_discovered else 'Unknown',
            'Date applied': tracking.applied_date if tracking and tracking.applied_date else '',
            'Application status': tracking.status if tracking else 'New',
            'Listing notes': lead.listing_notes if lead else '',
            'Notes': tracking.notes if tracking else '',
            '_priority': priority_group(job),
        })
    return output


def render_manual_tracker(factory):
    st.title('Manual Job Application Tracker')
    st.caption('Open vacancy pages, apply manually, and record progress here. No application is submitted by this tracker.')
    with factory() as session:
        rows = _rows(session)
        counts = {status: sum(row['Application status'] == status for row in rows) for status in MANUAL_STATUSES}
        metric_cols = st.columns(4)
        metric_cols[0].metric('Tracked vacancies', len(rows))
        metric_cols[1].metric('Manually applied', counts['Applied'])
        metric_cols[2].metric('Interviews', counts['Interview'])
        metric_cols[3].metric('Offers', counts['Offer'])

        with st.expander('Import individual or bulk vacancy URLs', expanded=False):
            st.caption('Paste one official individual vacancy URL per line. Supported hosts include Rabota.md, Delucru.md, Joblist.md, eJobs, BestJobs, Hipo, DevJob.ro, DOU, Djinni, Greenhouse, Lever, Workable, Workday, and selected remote boards; official employer pages are accepted when they publish individual JobPosting metadata. Public pages are checked against robots.txt; search/listing pages and pages without individual vacancy evidence are rejected. No retries are made. Jobs.md remains unsupported until its source is verified. LinkedIn is excluded.')
            bulk = st.text_area('Vacancy URLs', key='manual-tracker-import-urls', placeholder='https://official-employer.example/careers/job/123')
            if st.button('Import vacancy URLs', key='manual-tracker-import-submit'):
                try:
                    urls = parse_bulk_urls(bulk)
                    results, errors = [], []
                    with factory.begin() as write_session:
                        for url in urls:
                            try:
                                results.append(import_vacancy(write_session, url))
                            except ImportRejected as exc:
                                errors.append((url, str(exc)))
                    imported = sum(item['status'] == 'imported' for item in results)
                    duplicates = sum(item['status'] == 'duplicate' for item in results)
                    st.success(f'Added {imported} new vacancy/vacancies; {duplicates} duplicate(s) already existed; {len(errors)} URL(s) could not be imported.')
                    for url, reason in errors:
                        st.warning(f'{url}: {reason}')
                    if imported:
                        st.rerun()
                except ImportRejected as exc:
                    st.error(str(exc))

            st.divider()
            st.markdown('#### Manual entry when a page has no structured job metadata')
            st.caption('This does not fetch or scrape the page. Enter only details you checked yourself. Unverified entries stay visibly labeled; unknown fields can remain Unknown. LinkedIn and Jobs.md are excluded.')
            with st.form('manual-vacancy-entry-form'):
                employer_input = st.text_input('Employer', key='manual-entry-employer')
                title_input = st.text_input('Job title', key='manual-entry-title')
                url_input = st.text_input('Individual job URL', key='manual-entry-url', placeholder='https://employer.example/careers/job/123')
                country_input = st.text_input('Country', value='Unknown', key='manual-entry-country')
                city_input = st.text_input('City', value='Unknown', key='manual-entry-city')
                arrangement_input = st.selectbox('Work arrangement', ['Unknown', 'Remote', 'Hybrid', 'Onsite'], key='manual-entry-arrangement')
                source_input = st.text_input('Source', value='Unknown', key='manual-entry-source')
                evidence_input = st.selectbox('Source verification', list(LEAD_EVIDENCE_STATUSES), key='manual-entry-evidence')
                active_input = st.checkbox('I checked that this individual vacancy is currently active', key='manual-entry-active')
                official_input = st.checkbox('I checked this active vacancy on the employer’s official careers page', key='manual-entry-official')
                notes_input = st.text_area('Notes', key='manual-entry-notes')
                add_clicked = st.form_submit_button('Add manual vacancy')
                if add_clicked:
                    try:
                        with factory.begin() as write_session:
                            result = add_manual_vacancy(
                                write_session, employer=employer_input, title=title_input, url=url_input,
                                country=country_input, city=city_input, work_arrangement=arrangement_input,
                                source=source_input, notes=notes_input, evidence_status=evidence_input,
                                confirmed_active=active_input, confirmed_official=official_input,
                            )
                        if result['status'] == 'duplicate':
                            st.info(f"This URL already exists as vacancy #{result['job_id']}; no duplicate was added.")
                        else:
                            st.success(f"Added vacancy #{result['job_id']} as {result['evidence_status']}.")
                            st.rerun()
                    except ImportRejected as exc:
                        st.error(str(exc))

        countries = sorted({r['Country'] for r in rows})
        employers = sorted({r['Company'] for r in rows}, key=str.casefold)
        statuses = list(MANUAL_STATUSES)
        eligibility_values = sorted({r['Geographic eligibility'] for r in rows})
        f1, f2, f3, f4 = st.columns(4)
        with f1:
            search = st.text_input('Search title or company', key='manual-tracker-search').casefold()
            country = st.selectbox('Country', ['All'] + countries, key='manual-tracker-country')
        with f2:
            employer = st.selectbox('Employer', ['All'] + employers, key='manual-tracker-employer')
            arrangement = st.selectbox('Work arrangement', ['All', 'Remote', 'Hybrid', 'Onsite', 'Unknown'], key='manual-tracker-arrangement')
        with f3:
            eligibility = st.selectbox('Geographic eligibility', ['All'] + eligibility_values, key='manual-tracker-geography')
        with f4:
            status_filter = st.selectbox('Filter by application status', ['All'] + statuses, key='manual-tracker-status-filter')
            minimum_score = st.slider('Minimum professional score', 0, 100, 0, key='manual-tracker-score')
        sort_by = st.radio('Sort vacancies', ['Geographic priority + professional match', 'Professional match'], horizontal=True, key='manual-tracker-sort')
        filtered = [r for r in rows if
                    (not search or search in r['Company'].casefold() or search in r['Job title'].casefold()) and
                    (country == 'All' or r['Country'] == country) and
                    (employer == 'All' or r['Company'] == employer) and
                    (arrangement == 'All' or r['Work arrangement'] == arrangement) and
                    (eligibility == 'All' or r['Geographic eligibility'] == eligibility) and
                    (status_filter == 'All' or r['Application status'] == status_filter) and
                    ((r['Match score'] or 0) >= minimum_score)]
        if sort_by == 'Professional match':
            filtered.sort(key=lambda r: (-(r['Match score'] or -1), r['Company'].casefold(), r['ID']))
        else:
            filtered.sort(key=lambda r: (r['_priority'], -(r['Match score'] or -1), r['Company'].casefold(), r['ID']))
        st.caption('Geographic priority is a sorting aid; uncertain eligibility remains visible, and professional score is shown separately. Unknown fields are not inferred.')
        display_columns = ['ID', 'Company', 'Job title', 'Country', 'City', 'Work arrangement',
                           'Professional match', 'Match score', 'Geographic eligibility', 'Listing source', 'Source verification',
                           'Application source', 'Job URL', 'Date discovered', 'Date applied', 'Application status', 'Listing notes', 'Notes']
        st.dataframe(pd.DataFrame([{key: row[key] for key in display_columns} for row in filtered]),
                     hide_index=True, use_container_width=True,
                     column_config={'Job URL': st.column_config.LinkColumn('Job URL', validate='^https://.*$')},
                     height=430)
        csv = pd.DataFrame([{key: row[key] for key in display_columns} for row in filtered]).to_csv(index=False).encode('utf-8-sig')
        st.download_button('Export filtered vacancies as CSV', csv, 'manual_job_application_tracker.csv', 'text/csv', key='manual-tracker-csv')

        if not filtered:
            st.info('No vacancies match the current filters.')
            return
        options = {r['ID']: f"#{r['ID']} · {r['Company']} — {r['Job title']}" for r in filtered}
        remembered = st.session_state.get('manual_tracker_selected_id')
        if remembered not in options:
            if remembered is not None:
                st.session_state.pop('manual_tracker_selected_id', None)
                st.info('The selected vacancy is outside the current filters. Choose a visible vacancy to edit its tracker record.')
            remembered = next(iter(options))
        selected_id = st.selectbox('Select vacancy to update', list(options),
                                   format_func=lambda job_id: options[job_id], index=list(options).index(remembered),
                                   key='manual-tracker-selected-id')
        st.session_state['manual_tracker_selected_id'] = selected_id
        selected = next(row for row in filtered if row['ID'] == selected_id)
        with st.container(border=True):
            st.markdown(f"### [{html.escape(selected['Job title'])}]({selected['Job URL']})")
            st.write(f"**{selected['Company']}** · {selected['Country']} · {selected['City']} · {selected['Work arrangement']}")
            st.link_button('Open original vacancy page', selected['Job URL'])
            st.write(f"**Professional match:** {selected['Professional match']} · {selected['Match score'] if selected['Match score'] is not None else 'Not scored'}")
            st.write(f"**Geographic eligibility:** {selected['Geographic eligibility']}")
            if selected['Geographic evidence']:
                st.caption(selected['Geographic evidence'])
            st.write(f"**Listing source:** {selected['Listing source']} · **Application record source:** {selected['Application source']}")
            st.write(f"**Date discovered:** {selected['Date discovered']}")
            current_status = selected['Application status']
            new_status = st.selectbox('Application status', statuses, index=statuses.index(current_status) if current_status in statuses else 0,
                                      key=f'manual-status-{selected_id}')
            current_applied = date.fromisoformat(selected['Date applied']) if selected['Date applied'] else None
            applied_date = st.date_input('Date applied', value=current_applied, key=f'manual-applied-date-{selected_id}')
            notes = st.text_area('Notes', selected['Notes'], key=f'manual-notes-{selected_id}')
            save = st.button('Save application progress', key=f'manual-save-{selected_id}')
            if save:
                try:
                    with factory.begin() as write_session:
                        set_manual_application(write_session, selected_id, new_status,
                                               applied_date=applied_date.isoformat() if applied_date else None,
                                               notes=notes)
                    st.success('Manual application progress saved.')
                    st.rerun()
                except (ValueError, OSError):
                    st.error('The application progress could not be saved. Check the selected vacancy and date.')

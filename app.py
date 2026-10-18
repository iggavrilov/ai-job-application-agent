import json
from datetime import date
import pandas as pd
import streamlit as st
from sqlalchemy import select
from filelock import Timeout
from agent.db import database, JobRow, MatchRow, SearchRun
from agent.models import Job, Match
from agent.collectors import plain
from agent.discovery import discover
from agent.matching import region, ranking_key
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
    records = []
    for row in session.scalars(select(JobRow)):
        latest = session.scalar(select(MatchRow).where(MatchRow.job_id==row.id).order_by(MatchRow.id.desc()))
        if latest is None:
            continue
        job, match = Job.model_validate_json(row.payload), Match.model_validate_json(latest.payload)
        records.append({'ID':row.id,'Job title':job.title,'Company':job.company,'Location':job.location,'Remote status':job.remote,
          'Match score':match.score,'Recommendation':match.recommendation,'Salary':job.salary or 'Not published',
          'Publication date':job.published,'Application link':str(job.url),'Source':job.platform,'Discovered':row.first_discovered[:10],
          'Region':region(job.location),'Last checked':row.last_checked[:19],'Review status':row.status,'_job':job,'_match':match})
    cols = st.columns(6)
    metrics = [('Total vacancies',len(records)),('New vacancies',sum(r['Review status']=='New' for r in records)),
      ('Strong matches',sum(r['Recommendation']=='Strong Match' for r in records)),('Good matches',sum(r['Recommendation']=='Good Match' for r in records)),
      ('Needs review',sum(r['Recommendation']=='Needs Review' for r in records)),('Last successful search',next((r.finished[:19] for r in runs if r.status=='Success'),'Never'))]
    for col,(label,value) in zip(cols,metrics):
        col.metric(label,value)
    st.caption('Total vacancies counts stored relevant jobs; New means not yet reviewed. Search history also records all fetched board postings.')
    if records:
        filtered = records
        for field in ['Region','Location','Remote status','Recommendation','Source','Review status']:
            values = st.sidebar.multiselect(field,sorted({r[field] for r in records}))
            if values:
                filtered = [r for r in filtered if r[field] in values]
        country = st.sidebar.text_input('Country/region (location text)')
        title = st.sidebar.text_input('Job title contains')
        minimum = st.sidebar.slider('Minimum match score',0,100,0)
        after = st.sidebar.date_input('Discovered on or after',value=date(2020,1,1))
        filtered = [r for r in filtered if country.lower() in r['Location'].lower() and title.lower() in r['Job title'].lower() and r['Match score']>=minimum and r['Discovered']>=after.isoformat()]
        order = st.sidebar.selectbox('Rank by',['Match score','Geographic priority'])
        remote_preferred = rules().get('remote_preferred', True)
        filtered.sort(key=lambda r:ranking_key(r,order,remote_preferred))
        if remote_preferred:
            st.caption('Remote roles are prioritized within the selected ranking; geographic ranking keeps Moldova, Romania and EU/Europe first.')
        st.dataframe(pd.DataFrame([{k:v for k,v in r.items() if not k.startswith('_')} for r in filtered]),hide_index=True,
          column_config={'Application link':st.column_config.LinkColumn('Original posting')},use_container_width=True)
        if filtered:
            selected = st.selectbox('Job details',filtered,format_func=lambda r:f"{r['ID']}: {r['Company']} — {r['Job title']}")
            job, match = selected['_job'], selected['_match']
            st.subheader(job.title)
            st.write(f'{job.company} · {job.location} · {job.remote} · {job.employment_type}')
            st.write('Eligibility:',match.eligibility,'| Recommendation:',match.recommendation)
            st.write('Matching skills:',', '.join(match.matching_skills) or 'No explicit evidence detected')
            st.write('Missing skills:',', '.join(match.missing_skills) or 'None identified; this is not proof all requirements are met')
            st.write('Mandatory requirements:',match.mandatory_requirements or 'Not separately identified')
            st.write(match.explanation)
            st.caption('Evaluation method: '+match.method)
            st.write('Extracted requirements:',job.requirements)
            st.text(plain(job.description))
            st.link_button('Open Original Job Posting',str(job.url))
            for col,status in zip(st.columns(3),['Interested','Not Interested','Reviewed']):
                if col.button('Mark as '+status):
                    session.get(JobRow,selected['ID']).status = status
                    session.commit()
                    st.rerun()
    else:
        st.info('No stored relevant vacancies. Run discovery; live results will appear here.')
    with st.expander('Search history'):
        st.dataframe([{'Run':r.id,'Started':r.started,'Finished':r.finished,'Status':r.status,'Fetched':r.discovered,'New relevant':r.new,'Errors':r.errors} for r in runs])

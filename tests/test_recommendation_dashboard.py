from pathlib import Path
import json
from sqlalchemy import select
from streamlit.testing.v1 import AppTest
from agent.db import database, Company, Source, JobRow, MatchRow, CandidateProfile
from agent.discovery import upsert_job
from agent.models import Job
from agent.matching import evaluate
from agent.settings import rules


def seed(factory,candidate,legacy=False):
    with factory() as session:
        cp=CandidateProfile(payload=candidate.model_dump_json())
        company=Company(name='Synthetic');session.add_all([cp,company]);session.flush()
        source=Source(company_id=company.id,platform='lever',board='synthetic')
        session.add(source);session.flush()
        for name,location in [('local','Moldova'),('foreign','Baku')]:
            job=Job(title='QA Engineer',company='Synthetic',location=location,
                    remote='Remote' if name=='local' else 'Onsite',
                    description='Playwright JavaScript API testing SQL Drupal Regression testing',
                    url='https://example.org/'+name,platform='lever',source_id=name)
            row,_=upsert_job(session,job,source)
            result=evaluate(job,candidate,rules(),False)
            payload=result.model_dump()
            if legacy:
                payload={key:value for key,value in payload.items() if key not in (
                    'professional_score','professional_fit','professional_reason','geographic_reason',
                    'geographic_priority','in_main_recommendations','exclusion_reasons')}
                payload['version']='mvp-01.3'
            session.add(MatchRow(job_id=row.id,candidate_id=cp.id,payload=json.dumps(payload)))
        session.commit()


def test_main_list_excludes_geography_with_auditable_reason(tmp_path,monkeypatch,candidate):
    target='sqlite:///'+str(tmp_path/'dashboard.db')
    monkeypatch.setenv('DATABASE_URL',target)
    factory=database(target);seed(factory,candidate)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=30)
    assert not app.exception and app.metric[0].value=='1'
    assert app.dataframe[0].value['Location'].tolist()==['Moldova']
    assert next(widget for widget in app.selectbox if widget.label=='Rank by').value=='Geographic priority'
    next(widget for widget in app.selectbox if widget.label=='Vacancy view').select('Excluded / audit').run()
    assert not app.exception
    frame=app.dataframe[0].value
    assert frame['Location'].tolist()==['Baku']
    assert frame['Professional fit'].tolist()==['Strong Match']
    assert frame['Geographic eligibility'].tolist()==['Ineligible']
    assert 'outside' in frame.iloc[0]['Exclusion reasons']
    with factory() as session:
        assert len(list(session.scalars(select(JobRow))))==2


def test_legacy_snapshots_are_reassessed_without_private_profile_or_history_rewrite(tmp_path,monkeypatch,candidate):
    target='sqlite:///'+str(tmp_path/'legacy.db');monkeypatch.setenv('DATABASE_URL',target)
    factory=database(target);seed(factory,candidate,legacy=True)
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=30)
    assert not app.exception and app.metric[0].value=='1'
    assert app.dataframe[0].value['Location'].tolist()==['Moldova']
    with factory() as session:
        assert len(list(session.scalars(select(MatchRow))))==2
        assert all(json.loads(row.payload)['version']=='mvp-01.3' for row in session.scalars(select(MatchRow)))


def test_grouped_postings_expose_each_source_url(tmp_path,monkeypatch,candidate):
    from pydantic import HttpUrl
    target='sqlite:///'+str(tmp_path/'grouped.db');monkeypatch.setenv('DATABASE_URL',target)
    factory=database(target);seed(factory,candidate)
    with factory() as session:
        row=session.scalar(select(JobRow).order_by(JobRow.id))
        source=session.scalar(select(Source))
        alias=Job.model_validate_json(row.payload).model_copy(update={'source_id':'alias','url':HttpUrl('https://example.org/alias')})
        _,is_new=upsert_job(session,alias,source)
        assert not is_new
        session.commit()
    app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py')).run(timeout=30)
    assert not app.exception and app.metric[0].value=='1'
    assert app.dataframe[0].value.iloc[0]['Source postings']==2
    assert any('Grouped source postings' in caption.value for caption in app.caption)

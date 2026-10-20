"""Regression coverage for distinct openings and recovery of fuzzy groups."""
import json
import pytest
from pydantic import HttpUrl
from sqlalchemy import select
from agent.db import database, Company, Source, JobRow, JobObservation, MatchRow, CandidateProfile
from agent.discovery import upsert_job
from agent.models import Job
from agent.matching import evaluate, canonical, fingerprint
from agent.settings import rules
from agent.identity_repair import repair_grouped_jobs


def posting(number, location='Moldova', description='Playwright SQL'):
    return Job(title='QA Engineer',company='Synthetic',location=location,remote='Remote',
               description=description,url=f'https://example.org/jobs/{number}',
               platform='greenhouse',source_id=str(number))


def setup(session):
    company=Company(name='Synthetic');session.add(company);session.flush()
    source=Source(company_id=company.id,platform='greenhouse',board='synthetic')
    session.add(source);session.flush()
    return source


def test_identical_title_city_different_ids_and_urls_keep_independent_assessments(tmp_path,candidate):
    factory=database('sqlite:///'+str(tmp_path/'distinct.db'))
    with factory.begin() as session:
        source=setup(session)
        cp=CandidateProfile(payload=candidate.model_dump_json());session.add(cp);session.flush()
        jobs=[posting(1),posting(2,description='Playwright SQL. Work authorization required.')]
        ids=[]
        for job in jobs:
            row,new=upsert_job(session,job,source);assert new
            ids.append(row.id)
            result=evaluate(job,candidate,rules(),False)
            session.add(MatchRow(job_id=row.id,candidate_id=cp.id,payload=result.model_dump_json()))
        assert len(set(ids))==2
        for job,identity in zip(jobs,ids):
            row,new=upsert_job(session,job,source)
            assert not new and row.id==identity
    with factory() as session:
        matches=list(session.scalars(select(MatchRow).order_by(MatchRow.id)))
        assert [json.loads(m.payload)['eligibility'] for m in matches]==['Eligible','Needs Review']
        assert len(list(session.scalars(select(JobRow))))==2
        assert len(list(session.scalars(select(JobObservation))))==2


def seed_legacy(factory,candidate,snapshots=True):
    with factory.begin() as session:
        source=setup(session)
        cp=CandidateProfile(payload=candidate.model_dump_json());session.add(cp);session.flush()
        first=posting(1)
        second=posting(2,description='Playwright SQL. Work authorization required.')
        row=JobRow(source_id=source.id,identity='greenhouse:synthetic:1',
                   canonical_url=canonical(first.url),fingerprint=fingerprint(first),payload=second.model_dump_json(),status='Reviewed')
        session.add(row);session.flush()
        for job in (first,second):
            session.add(JobObservation(job_id=row.id,source_id=source.id,
                identity='greenhouse:synthetic:'+job.source_id,canonical_url=canonical(job.url),payload=job.model_dump_json()))
            payload=evaluate(job,candidate,rules(),False).model_dump()
            if snapshots: payload['job_snapshot']=job.model_dump(mode='json')
            session.add(MatchRow(job_id=row.id,candidate_id=cp.id,payload=json.dumps(payload)))
        session.flush()
        original_payloads=[m.payload for m in session.scalars(select(MatchRow).order_by(MatchRow.id))]
    return original_payloads


@pytest.mark.parametrize('snapshots',[True,False])
def test_database_open_repairs_existing_groups_without_losing_history(tmp_path,candidate,snapshots):
    url='sqlite:///'+str(tmp_path/'legacy.db')
    factory=database(url);original=seed_legacy(factory,candidate,snapshots)
    factory=database(url)  # Same upgrade path used by both discovery and dashboard.
    with factory() as session:
        rows=list(session.scalars(select(JobRow).order_by(JobRow.id)))
        assert len(rows)==2 and rows[0].status=='Reviewed' and rows[1].status=='New'
        assert [Job.model_validate_json(r.payload).source_id for r in rows]==['1','2']
        for row in rows:
            obs=session.scalar(select(JobObservation).where(JobObservation.job_id==row.id))
            assert obs.canonical_url==row.canonical_url
            latest=session.scalar(select(MatchRow).where(MatchRow.job_id==row.id).order_by(MatchRow.id.desc()))
            assert latest is not None
            payload=json.loads(latest.payload)
            assert payload['eligibility']==('Eligible' if row is rows[0] else 'Needs Review')
        assert [m.payload for m in session.scalars(select(MatchRow).order_by(MatchRow.id).limit(2))]==original
        assert repair_grouped_jobs(session)==0
    factory=database(url)
    with factory() as session:
        assert len(list(session.scalars(select(JobRow))))==2


def test_repair_rolls_back_atomically(tmp_path,candidate):
    factory=database('sqlite:///'+str(tmp_path/'rollback.db'));seed_legacy(factory,candidate)
    with pytest.raises(RuntimeError):
        with factory.begin() as session:
            assert repair_grouped_jobs(session)==1
            raise RuntimeError('abort')
    with factory() as session:
        assert len(list(session.scalars(select(JobRow))))==1
        assert {o.job_id for o in session.scalars(select(JobObservation))}=={1}


def test_dashboard_recovers_and_exposes_both_same_title_openings(tmp_path,candidate,monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    from tests.support import discovery_dashboard_app_test
    target='sqlite:///'+str(tmp_path/'dashboard-repair.db')
    factory=database(target);seed_legacy(factory,candidate)
    monkeypatch.setenv('DATABASE_URL',target)
    app=discovery_dashboard_app_test()
    assert not app.exception
    frame=app.dataframe[0].value
    assert len(frame)==2 and set(frame['Job title'])=={'QA Engineer'}
    assert set(frame['Application link'])=={'https://example.org/jobs/1','https://example.org/jobs/2'}
    assert set(frame['Geographic eligibility'])=={'Eligible','Needs Review'}
    assert len(set(frame['ID']))==2

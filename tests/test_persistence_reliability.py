"""Requires the full requirements.txt environment; not part of the stdlib offline subset."""
import json
import pytest
from pydantic import HttpUrl
from sqlalchemy import select
from agent.db import database, Company, Source, JobRow, JobObservation, MatchRow, SearchRun
from agent.models import Job
from agent.discovery import upsert_job


@pytest.fixture
def job():
    return Job(title='QA Engineer',company='Mock',location='Moldova',remote='Remote',description='Playwright SQL',
               url='https://example.org/1',platform='lever',source_id='mock-1')


def test_cross_source_identity_survives_changed_content(tmp_path,job):
    db=database('sqlite:///'+str(tmp_path/'alias.db'))
    with db() as session:
        company=Company(name='Mock');session.add(company);session.flush()
        a=Source(company_id=company.id,platform='lever',board='mock')
        b=Source(company_id=company.id,platform='ashby',board='mock')
        session.add_all([a,b]);session.flush()
        first,new=upsert_job(session,job,a)
        assert new
        other=job.model_copy(update={'platform':'ashby','source_id':'mock-2','url':HttpUrl('https://example.org/2')})
        merged,new=upsert_job(session,other,b)
        assert not new and merged.id==first.id
        changed=other.model_copy(update={'title':'Senior QA Engineer','location':'Remote','url':HttpUrl('https://example.org/changed')})
        merged,new=upsert_job(session,changed,b)
        assert not new and merged.id==first.id
        session.commit()
    with db() as session:
        assert len(list(session.scalars(select(JobRow))))==1
        assert len(list(session.scalars(select(JobObservation))))==2


def test_database_error_does_not_poison_next_board(tmp_path,job,monkeypatch):
    import agent.discovery as d
    db=database('sqlite:///'+str(tmp_path/'run.db'))
    monkeypatch.setattr(d,'database',lambda:db)
    monkeypatch.setattr(d,'load_boards',lambda:([
        {'company':'Mock','platform':'lever','board':'one','enabled':True},
        {'company':'Mock','platform':'lever','board':'two','enabled':True}],[]))
    monkeypatch.setattr(d,'collect',lambda *args:[job])
    original=d.upsert_job
    calls=[]
    def broken_once(session,posting,source):
        calls.append(source.board)
        if len(calls)==1:
            session.add(Company(name='Mock')) # Unique constraint failure invalidates this transaction.
            session.flush()
        return original(session,posting,source)
    monkeypatch.setattr(d,'upsert_job',broken_once)
    result=d.discover(False)
    assert result['status']=='Partial' and calls==['one','two']
    assert result['new_relevant']==1
    totals=result['diagnostics']['totals']
    assert totals['failed_boards']==1 and totals['committed_boards']==1
    assert totals['normalized']==2 and totals['rolled_back_normalized']==1
    assert totals['relevant']==1 and totals['new']==1
    assert totals['unique_records']==1
    with db() as session:
        assert len(list(session.scalars(select(JobRow))))==1
        match=session.scalar(select(MatchRow))
        assert json.loads(match.payload)['job_snapshot']['description']==job.description
        assert session.scalar(select(SearchRun)).status=='Partial'


def test_config_failure_is_recorded(tmp_path,monkeypatch):
    import agent.discovery as d
    db=database('sqlite:///'+str(tmp_path/'bad.db'))
    monkeypatch.setattr(d,'database',lambda:db)
    monkeypatch.setattr(d,'profile',lambda: (_ for _ in ()).throw(ValueError('private information must not appear in errors')))
    result=d.discover(False)
    assert result['status']=='Failed'
    assert 'private information' not in str(result['errors'])
    with db() as session:
        assert session.scalar(select(SearchRun)).finished

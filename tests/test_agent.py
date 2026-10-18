import json
from pathlib import Path
import httpx
import pytest
from pydantic import ValidationError, HttpUrl
from sqlalchemy import select
from agent.models import Candidate, Job, Assessment
from agent.settings import rules
from agent.collectors import normalize, collect
from agent.matching import relevant, geography, evaluate, score_parts, canonical
from agent.db import database, Company, Source, JobRow, MatchRow, SearchRun
from agent.discovery import upsert_job


@pytest.fixture
def job():
    return Job(title='Senior QA Automation Engineer',company='Acme',location='Remote - Moldova',remote='Remote',
      description='Playwright JavaScript API testing SQL Drupal Regression testing',url='https://example.org/jobs/1',platform='lever',source_id='1')


def test_profile(candidate):
    p=candidate
    assert bool(p.name.strip()) and p.work_authorization=='unknown' and not p.languages
    with pytest.raises(ValidationError):
        Candidate.model_validate(p.model_dump() | {'experience_years':-1})


@pytest.mark.parametrize('platform,item',[
 ('greenhouse',{'id':1,'title':'QA Engineer','location':{'name':'Remote'},'content':'Original <b>HTML</b>','absolute_url':'https://example.org/1'}),
 ('lever',{'id':'1','text':'QA Engineer','categories':{'location':'Moldova'},'description':'Original <b>HTML</b>','hostedUrl':'https://example.org/1','workplaceType':'remote'}),
 ('ashby',{'id':'1','title':'QA Engineer','location':'Moldova','descriptionHtml':'Original <b>HTML</b>','jobUrl':'https://example.org/1','isRemote':True})])
def test_normalize(platform,item):
    j=normalize(platform,'Acme',item)
    assert j.remote=='Remote' and 'Original <b>HTML</b>' in j.description
    assert j.salary is None and j.source_id=='1'


@pytest.mark.parametrize('title,expected',[('Senior Manual QA Engineer',True),('QA Automation Engineer',True),('Senior Software Developer',False),('DevOps Engineer',False),('QA Automation Architect',False),('API QA Engineer',True)])
def test_keywords(job,title,expected):
    assert relevant(job.model_copy(update={'title':title}),rules()) is expected


def test_duplicate_persistence(tmp_path,job):
    db=database('sqlite:///'+str(tmp_path/'test.db'))
    with db() as s:
        c=Company(name='Acme'); s.add(c); s.flush()
        source=Source(company_id=c.id,platform='lever',board='acme'); s.add(source); s.flush()
        row,new=upsert_job(s,job,source)
        assert new
        first=row.first_discovered
        for duplicate in [job,job.model_copy(update={'source_id':'2'})]:
            r,new=upsert_job(s,duplicate,source)
            assert not new and r.id==row.id and r.first_discovered==first
        distinct=job.model_copy(update={'source_id':'3','url':HttpUrl('https://elsewhere.org/2')})
        separate,new=upsert_job(s,distinct,source)
        assert new and separate.id!=row.id
        s.commit()
    with db() as s:
        assert len(list(s.scalars(select(JobRow))))==2


def test_canonical():
    assert canonical('https://EXAMPLE.org/jobs/1/?utm_source=x#top')=='https://example.org/jobs/1'
    assert canonical('https://example.org/?job=1')!=canonical('https://example.org/?job=2')


@pytest.mark.parametrize('location,description,remote,expected',[
 ('Moldova','QA role','Remote','Eligible'),
 ('Worldwide','QA role','Remote','Eligible'),
 ('Europe','QA role','Remote','Needs Review'),
 ('Romania','QA role','Remote','Needs Review'),
 ('Germany','Must have right to work in Germany','Remote','Needs Review'),
 ('United States','QA role','Remote','Ineligible'),
 ('Worldwide','Must be based in United States','Remote','Ineligible'),
 ('Moldova','QA role','Onsite','Needs Review'),
 ('Cyprus','QA role','Remote','Needs Review'),
])
def test_geography(job,location,description,remote,expected,candidate):
    assert geography(job.model_copy(update={'location':location,'description':description,'remote':remote}),candidate)[0]==expected


def test_unknown_work_authorization(job,candidate):
    j=job.model_copy(update={'description':job.description+'; Work authorization required'})
    m=evaluate(j,candidate,rules(),False)
    assert m.eligibility=='Needs Review' and m.recommendation=='Needs Review'


def test_salary_and_scoring(job,candidate):
    a=Assessment(technical=1,experience=1,additional=1,matching_skills=[],missing_skills=[],mandatory_requirements=[],uncertain_mandatory=False,explanation='test')
    assert score_parts(a,'Eligible',rules())==100
    m=evaluate(job,candidate,rules(),False)
    assert m.score==evaluate(job.model_copy(update={'salary':'1 EUR/year'}),candidate,rules(),False).score
    assert m.recommendation=='Strong Match'


def test_geo_cannot_be_overridden(job,candidate):
    m=evaluate(job.model_copy(update={'location':'United States'}),candidate,rules(),False)
    assert m.recommendation=='Not Recommended'


def test_uncertain_mandatory(job,candidate):
    m=evaluate(job.model_copy(update={'description':job.description+'; Fluent English required'}),candidate,rules(),False)
    assert m.recommendation=='Needs Review'


def test_ai_validation_and_fallback(job,monkeypatch,candidate):
    with pytest.raises(ValidationError):
        Assessment.model_validate_json('{"technical":101}')
    monkeypatch.setenv('OPENAI_API_KEY','test-not-real')
    import openai
    monkeypatch.setattr(openai,'OpenAI',lambda **kw: (_ for _ in ()).throw(RuntimeError('offline')))
    assert 'fallback' in evaluate(job,candidate,rules()).method


def test_collector_failure():
    def handler(request):
        return httpx.Response(404 if request.url.path=='/robots.txt' else 503)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            collect({'platform':'lever','board':'acme','company':'Acme'},client,0)


def test_robots_disallow():
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='User-agent: *\nDisallow: /'))) as client:
        with pytest.raises(RuntimeError,match='disallows'):
            collect({'platform':'lever','board':'acme','company':'Acme'},client,0)


def test_discovery_continues_and_history(tmp_path,job,monkeypatch):
    import agent.discovery as d
    from agent.settings import ROOT
    db=database('sqlite:///'+str(tmp_path/'runs.db'))
    monkeypatch.setattr(d,'database',lambda:db)
    calls=[]
    def fake(board,client,interval):
        calls.append(board['platform'])
        if len(calls)==1:
            raise RuntimeError('board offline')
        return [job]
    monkeypatch.setattr(d,'collect',fake)
    monkeypatch.setattr(d,'load_boards',lambda:([
      {'company':'Mock A','platform':'greenhouse','board':'mock-a','enabled':True},
      {'company':'Mock B','platform':'ashby','board':'mock-b','enabled':True},
      {'company':'Mock C','platform':'lever','board':'mock-c','enabled':True}],[]))
    result=d.discover(False)
    assert result['status']=='Partial' and len(calls)==3 and result['new_relevant']==1
    d.discover(False)
    with db() as s:
        assert len(list(s.scalars(select(SearchRun))))==2
        assert len(list(s.scalars(select(MatchRow))))==5


def test_overlapping_run():
    from filelock import FileLock, Timeout
    from agent.discovery import ROOT
    from agent.discovery import discover
    with FileLock(str(ROOT/'data/search.lock')):
        with pytest.raises(Timeout):
            discover(False)

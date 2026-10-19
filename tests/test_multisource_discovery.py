import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from agent.collectors import collect_public_source
from agent.db import database, Company, Source, JobRow, JobSourceStatus
from agent.discovery import upsert_job, record_complete_source_scan
from agent.eligibility import assess_geographic_eligibility
from agent.matching import evaluate, filter_reason
from agent.models import Job
from agent.source_registry import load_discovery_sources, active_api_sources
from agent.manual_import import import_user_content, parse_user_content
from agent.settings import rules


def source(id):
    return {'id':id, 'name':id.title(), 'status':'integrated', 'evidence_url':'https://provider.example/docs'}


def test_registry_has_no_duplicate_ids_and_integrations_have_evidence():
    sources, errors = load_discovery_sources()
    enabled, _ = active_api_sources()
    assert not errors
    assert len({item['id'] for item in sources}) == len(sources)
    assert {item['id'] for item in enabled} == {'arbeitnow','remoteok','himalayas'}
    assert all(item.get('evidence_url','').startswith('https://') for item in sources if item['status']=='integrated')
    assert any(item['id']=='working_nomads' and item['status']=='research_only' for item in sources)


def test_arbeitnow_paginates_and_keeps_provider_posting_url(monkeypatch):
    monkeypatch.setattr('agent.collectors.time.sleep', lambda *_: None)
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        if request.url.path == '/robots.txt': return httpx.Response(404)
        page=request.url.params.get('page','1')
        if page=='1':
            return httpx.Response(200,json={'data':[{'slug':'qa-1','title':'QA Engineer','company_name':'Acme','location':'Berlin','remote':True,'url':'https://www.arbeitnow.com/jobs/acme/qa-1','description':'Playwright'}],
                'links':{'next':'https://www.arbeitnow.nl/api/job-board-api?page=2'}})
        return httpx.Response(200,json={'data':[{'slug':'qa-2','title':'QA Analyst','company_name':'Acme','location':'Berlin','remote':True,'url':'https://www.arbeitnow.com/jobs/acme/qa-2','description':'API testing'}], 'links':{'next':None}})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=collect_public_source(source('arbeitnow'),client,interval=0,max_pages=10)
    assert result.pages_fetched==2 and result.pagination_complete
    assert len(result)==2
    assert result[0].source_url == result[0].url
    assert result[0].employer_application_url is None
    assert result[0].source_type=='aggregator'
    assert len([url for url in requests if '/job-board-api' in url])==2
    assert 'https://www.arbeitnow.nl/robots.txt' in requests
    assert requests.index('https://www.arbeitnow.nl/robots.txt') < next(i for i,url in enumerate(requests) if 'arbeitnow.nl/api/job-board-api' in url)


def test_himalayas_cursor_uses_required_application_link_and_country_restrictions(monkeypatch):
    monkeypatch.setattr('agent.collectors.time.sleep', lambda *_: None)
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        if request.url.path=='/robots.txt': return httpx.Response(200,text='User-agent: *\nAllow: /')
        if request.url.path!='/jobs/api': return httpx.Response(404)
        if request.url.params.get('cursor')=='cursor-2':
            return httpx.Response(200,json={'jobs':[{'guid':'j2','title':'QA Engineer','companyName':'EU Co','applicationLink':'https://himalayas.app/jobs/j2','locationRestrictions':[],'employmentType':'Contractor','description':'Test APIs','expiryDate':None}], 'totalCount':2})
        return httpx.Response(200,json={'jobs':[{'guid':'j1','title':'QA Analyst','companyName':'US Co','applicationLink':'https://himalayas.app/jobs/j1','locationRestrictions':[{'name':'United States'}],'employmentType':'Full Time','description':'QA test'}], 'nextCursor':'cursor-2','totalCount':2})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=collect_public_source(source('himalayas'),client,interval=0,max_pages=10)
    assert result.pages_fetched==2 and result.pagination_complete and len(result)==2
    assert str(result[0].url) == 'https://himalayas.app/jobs/j1'
    assert result[0].employer_application_url is None
    assert result[0].remote_permitted_locations==['United States']
    assert result[1].remote_permitted_locations==['Worldwide']
    assert sum('cursor=cursor-2' in url for url in requests)==1


def test_remoteok_deduplicates_provider_ids_and_preserves_original_apply_url(monkeypatch):
    monkeypatch.setattr('agent.collectors.time.sleep', lambda *_: None)
    posting={'id':42,'position':'QA Tester','company':'Example','location':'Remote','description':'Manual QA',
             'url':'https://remoteok.com/remote-jobs/42','apply_url':'https://careers.example/jobs/42','date':'2026-10-18'}
    def handler(request):
        if request.url.path=='/robots.txt': return httpx.Response(404)
        return httpx.Response(200,json=[{'legal':'metadata'},posting,posting])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=collect_public_source(source('remoteok'),client,interval=0)
    assert result.pages_fetched==1 and len(result)==1
    assert str(result[0].url)=='https://careers.example/jobs/42'
    assert result[0].employer_application_url==result[0].url
    assert str(result[0].source_url)=='https://remoteok.com/remote-jobs/42'


def test_public_feed_stops_on_429_without_retry(monkeypatch):
    monkeypatch.setattr('agent.collectors.time.sleep', lambda *_: None)
    api_calls=[]
    def handler(request):
        if request.url.path=='/robots.txt': return httpx.Response(404)
        api_calls.append(request.url)
        return httpx.Response(429,headers={'Retry-After':'120'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError,match='429') as limited:
            collect_public_source(source('remoteok'),client,interval=0)
    assert len(api_calls)==1
    assert limited.value.policy_http_status == 429


def test_robots_disallow_prevents_api_call(monkeypatch):
    monkeypatch.setattr('agent.collectors.time.sleep', lambda *_: None)
    requested=[]
    def handler(request):
        requested.append(request.url.path)
        return httpx.Response(200,text='User-agent: *\nDisallow: /')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(Exception,match='robots.txt disallows'):
            collect_public_source(source('remoteok'),client,interval=0)
    assert requested==['/robots.txt']


def test_expired_himalayas_job_is_counted_and_not_returned(monkeypatch):
    monkeypatch.setattr('agent.collectors.time.sleep', lambda *_: None)
    expired=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
    def handler(request):
        if request.url.path=='/robots.txt': return httpx.Response(404)
        return httpx.Response(200,json={'jobs':[{'guid':'old','title':'QA Engineer','companyName':'Old Co','applicationLink':'https://himalayas.app/jobs/old','locationRestrictions':[],'expiryDate':expired}], 'totalCount':1})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=collect_public_source(source('himalayas'),client,interval=0)
    assert len(result)==0 and result.expired_count==1 and len(result.expired_postings)==1


def test_lifecycle_marks_expiry_or_missing_without_deleting_or_changing_review_status(tmp_path, candidate):
    factory=database('sqlite:///'+str(tmp_path/'lifecycle.db'))
    job=Job(title='QA Engineer',company='Acme',location='Romania',remote='Remote',description='Playwright',
            url='https://example.org/qa',platform='lever',source_id='1')
    with factory.begin() as session:
        company=Company(name='Acme');session.add(company);session.flush()
        source_row=Source(company_id=company.id,platform='lever',board='acme');session.add(source_row);session.flush()
        row,_=upsert_job(session,job,source_row);row.status='Shortlisted'
        state=session.scalar(select(JobSourceStatus).where(JobSourceStatus.job_id==row.id))
        state.last_seen='2020-01-01T00:00:00+00:00'
        class Complete(list):
            pagination_complete=True
            expired_postings=[]
        record_complete_source_scan(session,{'board':'acme'},source_row.id,Complete(), '2025-01-01T00:00:00+00:00')
    with factory() as session:
        row=session.scalar(select(JobRow))
        state=session.scalar(select(JobSourceStatus))
        assert row.status=='Shortlisted'
        assert state.status=='Not observed in latest complete source scan'
        assert 'does not prove' in state.note


def test_confirmed_eu_rights_make_eu_onsite_a_relocation_option(candidate):
    eu_candidate=candidate.model_copy(update={'citizenship':'Romanian citizen and Romanian/EU passport holder.',
        'work_authorization':'Legally authorized to work in all EU member states without an EU work permit.'})
    job=Job(title='Senior QA Engineer',company='EU employer',location='Berlin, Germany',remote='Onsite',
            description='Playwright JavaScript API testing SQL Drupal Regression testing. You must be based in Germany.',
            url='https://example.org/qa',platform='greenhouse',source_id='de-qa')
    match=evaluate(job,eu_candidate,rules(),False)
    assert match.professional_fit=='Strong Match'
    assert match.geographic_assessment.classification=='Eligible If Relocating'
    assert match.in_main_recommendations
    assert 'not confirmed' in match.geographic_assessment.relocation_requirement


def test_moldova_remote_and_eu_remote_are_never_auto_confirmed(candidate):
    eu_candidate=candidate.model_copy(update={'citizenship':'Romanian citizen and Romanian/EU passport holder.',
        'work_authorization':'Legally authorized to work in all EU member states without an EU work permit.'})
    moldova=Job(title='QA Engineer',company='Moldova employer',location='Remote from Moldova',remote='Remote',
        description='QA Engineer; remote from Moldova',url='https://example.org/md',platform='lever',source_id='md')
    eu=Job(title='QA Engineer',company='EU employer',location='Remote EU',remote='Remote',
        description='Remote within EU member states only',url='https://example.org/eu',platform='lever',source_id='eu')
    assert assess_geographic_eligibility(moldova,eu_candidate).classification=='Potentially Eligible — Needs Review'
    assert assess_geographic_eligibility(eu,eu_candidate).classification=='Eligible If Relocating'


def test_us_work_authorization_is_not_inferred_and_international_contract_is_reviewable(candidate):
    job=Job(title='QA Engineer',company='US employer',location='United States',remote='Remote',
        description='Remote role. US citizen or US work authorization required. No visa sponsorship.',
        url='https://example.org/us',platform='greenhouse',source_id='us')
    assert assess_geographic_eligibility(job,candidate).classification=='Confirmed Ineligible'
    contractor=job.model_copy(update={'description':'International B2B contractor accepted; worldwide remote.'})
    result=evaluate(contractor,candidate,rules(),False)
    assert result.geographic_assessment.classification=='Potentially Eligible — Needs Review'
    assert result.in_main_recommendations


def test_manual_linkedin_import_is_offline_sanitized_and_idempotent(tmp_path):
    text='Title: Senior QA Engineer\nCompany: Example Corp\nLocation: Remote EU\nAlert email body with private sender address private@example.test https://www.linkedin.com/jobs/view/123456789/'
    leads=parse_user_content(text)
    assert len(leads)==1 and leads[0]['title']=='Senior QA Engineer'
    assert leads[0]['company']=='Example Corp' and leads[0]['status']=='Needs Official Verification'
    factory=database('sqlite:///'+str(tmp_path/'manual.db'))
    with factory.begin() as session:
        imported=import_user_content(session,text)
        assert len(imported)==1
        imported[0].status='Manually reconciled'
    with factory.begin() as session:
        again=import_user_content(session,text)
        assert len(again)==1 and again[0].status=='Manually reconciled'
        assert not hasattr(again[0],'raw_content')
    database_text=(tmp_path/'manual.db').read_bytes()
    assert b'private@example.test' not in database_text


def test_manual_linkedin_import_only_accepts_user_supplied_job_urls():
    text='Title: QA Analyst\nCompany: Acme\nhttps://www.linkedin.com/jobs/view/11111111/\nhttps://www.linkedin.com/jobs/view/22222222/\nhttps://linkedin.example/jobs/view/33333333/'
    assert len(parse_user_content(text))==2




def test_lever_adapter_only_fills_verified_labels_and_unique_resume(monkeypatch,tmp_path,candidate):
    from agent.application_workflow import LeverAdapter
    class Control:
        def __init__(self,label='',kind='text',meta=''):
            self.label=label; self.kind=kind; self.meta=meta; self.value=''; self.upload=[]
        def count(self): return 1
        def is_visible(self): return True
        def fill(self,value): self.value=value
        def input_value(self): return self.value
        def get_attribute(self,name): return {'type':self.kind,'name':self.meta,'id':self.meta,'aria-label':'','accept':'application/pdf' if self.kind=='file' else ''}.get(name)
        def set_input_files(self,path):
            self.upload=[{'name':Path(path).name,'size':Path(path).stat().st_size}]
        def evaluate(self,script):
            if self.kind=='file': return self.upload
            return ''
    class Label:
        def __init__(self,text): self.text=text
        def inner_text(self): return self.text
    class Collection:
        def __init__(self,items): self.items=items
        def count(self): return len(self.items)
        def nth(self,index): return self.items[index]
    class Page:
        def __init__(self):
            self.controls={'First Name':Control('First Name'),'Last Name':Control('Last Name'),
                'Email':Control('Email'),'Phone':Control('Phone')}
            self.file=Control(kind='file',meta='resume')
            self.labels=Collection([Label(name) for name in self.controls])
        def locator(self,selector):
            return self.labels if selector=='label' else Collection([self.file]) if selector=='input[type="file"]' else Collection([])
        def get_by_label(self,label,exact=True): return self.controls.get(label,Collection([]))
        def evaluate(self,script): return None
    page=Page()
    cv=tmp_path/'synthetic.pdf';cv.write_bytes(b'%PDF synthetic fixture')
    monkeypatch.setattr('agent.application_workflow._form_dom_snapshot',lambda page:{'forms':[{}],'fields':[
        {'label':'First Name','required':True,'value':page.controls['First Name'].value},
        {'label':'Last Name','required':True,'value':page.controls['Last Name'].value},
        {'label':'Email','required':True,'value':page.controls['Email'].value},
        {'label':'Phone','required':False,'value':page.controls['Phone'].value},
        {'label':'Location (City)','required':True,'value':''},
        {'label':'Work authorization','required':True,'value':''},
        {'label':'Recruitment consent','required':True,'value':''}]})
    person=candidate.model_copy(update={'name':'Test Candidate','email':'qa@example.test','telephone':'+10000000000'})
    results=LeverAdapter().fill(page,person,cv)
    assert page.controls['First Name'].value=='Test'
    assert page.controls['Last Name'].value=='Candidate'
    assert page.controls['Email'].value=='qa@example.test'
    assert page.controls['Phone'].value=='+10000000000'
    assert page.file.upload==[{'name':'synthetic.pdf','size':len(b'%PDF synthetic fixture')}]
    pending=[item.label for item in results if item.status=='Needs candidate input']
    assert {'Location (City)','Work authorization','Recruitment consent'} <= set(pending)
    assert not hasattr(page,'submitted')


def test_live_submission_source_guard_rejects_lever_even_if_state_exists(tmp_path,monkeypatch,candidate):
    from agent.application_submission import _load_current_sources, ApplicationSubmissionError
    from agent.models import Job
    job=Job(title='QA Engineer',company='Lever employer',location='Europe',remote='Remote',description='Playwright',
        url='https://jobs.lever.co/example/job-123',platform='lever',source_id='job-123')
    monkeypatch.setattr('agent.application_submission._reject_explicitly_ineligible',lambda *args: None)
    monkeypatch.setattr('agent.application_submission._load_shortlisted_job',lambda *args: job)
    with pytest.raises(ApplicationSubmissionError,match='Greenhouse pilot'):
        _load_current_sources(123,tmp_path)


@pytest.mark.parametrize('title',[
    'QA Engineer','Senior QA Engineer','Manual QA Engineer','Software Tester','Software Test Engineer',
    'QA Automation Engineer','Automation Test Engineer','Test Automation Engineer','SDET',
    'Software Development Engineer in Test','Quality Assurance Engineer','Quality Engineer',
    'Software Quality Engineer','QA Analyst','Test Analyst','Senior Test Engineer','QA Lead','Test Lead',
    'Web QA Engineer','API QA Engineer','Mobile QA Engineer'
])
def test_expanded_requested_title_coverage(title):
    job=Job(title=title,company='Synthetic employer',location='Remote Europe',remote='Remote',
        description='Playwright Selenium API testing JavaScript SQL regression automation web software testing',
        url='https://example.org/qa',platform='lever',source_id=title.replace(' ','-'))
    assert filter_reason(job,rules()) is None



def test_explicit_source_expiry_is_retained_without_changing_review_status(tmp_path):
    factory=database('sqlite:///'+str(tmp_path/'expired-state.db'))
    job=Job(title='QA Engineer',company='Arbeitnow employer',location='Berlin',remote='Remote',description='Playwright',
        url='https://www.arbeitnow.com/jobs/employer/qa-1',platform='arbeitnow',source_id='qa-1',
        source_url='https://www.arbeitnow.com/jobs/employer/qa-1',expires_at='2020-01-01T00:00:00+00:00')
    with factory.begin() as session:
        company=Company(name='Arbeitnow');session.add(company);session.flush()
        source_row=Source(company_id=company.id,platform='arbeitnow',board='arbeitnow');session.add(source_row);session.flush()
        row,_=upsert_job(session,job,source_row);row.status='Shortlisted'
        expired=job.model_copy(update={'expires_at':'2020-01-01T00:00:00+00:00'})
        class Complete(list):
            pagination_complete=True
            expired_postings=[expired]
        record_complete_source_scan(session,{'board':'arbeitnow'},source_row.id,Complete(),'2025-01-01T00:00:00+00:00')
    with factory() as session:
        row=session.scalar(select(JobRow))
        state=session.scalar(select(JobSourceStatus))
        assert row.status=='Shortlisted'
        assert state.status=='Expired per source'
        assert state.expires_at=='2020-01-01T00:00:00+00:00'
        assert 'passed' in state.note

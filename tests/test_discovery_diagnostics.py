import json
import httpx
import pytest
from sqlalchemy import select
from agent.collectors import CollectedJobs, collect, remote_status
from agent.db import database, JobRow, MatchRow, SearchDiagnostic
from agent.matching import filter_reason, geography, relevant
from agent.models import Job
from agent.settings import rules


def posting(title='QA Engineer', source_id='one', location='Europe'):
    return Job(title=title, company='Synthetic', source_id=source_id, platform='lever',
               location=location, remote='Remote', description='Playwright API testing SQL',
               url='https://example.org/jobs/' + source_id)


@pytest.mark.parametrize('title', ['Senior QA Engineer','Manual QA','QA Analyst','API QA',
    'Web QA','QA Automation Specialist','Automation QA Engineer','Test Automation Engineer',
    'Software Engineer in Test','Software Development Engineer in Test','SDET','QA-Engineer','Quality Engineer'])
def test_requested_software_qa_variants(title):
    assert relevant(posting(title),rules())


@pytest.mark.parametrize('title,description,reason', [
    ('Senior Software Developer','QA collaboration','excluded_title'),
    ('Product Designer','Playwright','no_qa_title_keyword'),
    ('Quality Engineer','Inspect factory parts and production welds','no_software_testing_evidence'),
    ('QA Automation Architect','Playwright','excluded_title'),
    ('Director of Quality Assurance','Playwright','excluded_title'),
    ('SDET','Expert Java programming required','advanced_programming_sdet'),
])
def test_rejection_reasons(title,description,reason):
    assert filter_reason(posting(title).model_copy(update={'description':description}),rules()) == reason


@pytest.mark.parametrize('location', ['Europe','Remote','unknown','Romania','United States / Poland','Worldwide / France'])
def test_unclear_or_alternative_target_locations_are_reviewable(candidate,location):
    assert geography(posting(location=location),candidate)[0] == 'Needs Review'


def test_title_hiring_scope_prevents_false_country_rejection(candidate):
    assert geography(posting('QA Engineer (Europe)',location='United States'),candidate)[0]=='Needs Review'


def test_anywhere_in_europe_is_not_worldwide_eligibility(candidate):
    assert geography(posting('QA Engineer (Anywhere in Europe)',location='Ukraine'),candidate)[0]=='Needs Review'


def test_home_based_is_remote():
    assert remote_status('Home based - EMEA') == 'Remote'
    assert remote_status('Home based - EMEA','onsite') == 'Onsite'
    assert remote_status('Office Based - Taipei') == 'Onsite'
    assert remote_status('Home Based - APAC; Office Based - Taipei') == 'Unknown'


def test_greenhouse_explicit_work_type_and_location_precedence():
    from agent.collectors import normalize
    item={'id':1,'title':'QA Engineer','location':{'name':'Romania'},
          'content':'<p>Work Type: Remote</p>','absolute_url':'https://example.org/job/1'}
    assert normalize('greenhouse','Synthetic',item).remote=='Remote'
    item['location']['name']='Office Based - Romania'
    assert normalize('greenhouse','Synthetic',item).remote=='Onsite'


def test_diagnostics_reconcile_persist_and_deduplicate(tmp_path, monkeypatch):
    import agent.discovery as discovery
    factory = database('sqlite:///' + str(tmp_path/'diagnostics.db'))
    monkeypatch.setattr(discovery,'database',lambda:factory)
    monkeypatch.setattr(discovery,'load_boards',lambda:([
        {'company':'Synthetic','platform':'lever','board':'synthetic','enabled':True}],[]))
    jobs = CollectedJobs()
    jobs.extend([posting(),posting('QA Analyst','two','Moldova'),posting('Product Designer','three')])
    jobs.published_count=4
    jobs.warnings=['One malformed synthetic posting']
    monkeypatch.setattr(discovery,'collect',lambda *args:jobs)
    first=discovery.discover(False)
    assert first['status']=='Partial'
    totals=first['diagnostics']['totals']
    assert totals['published']==totals['normalized']+totals['malformed']==4
    assert totals['normalized']==totals['relevant']+totals['title_rejected']==3
    assert totals['relevant']==totals['new']+totals['existing_or_merged']==2
    assert totals['unique_records']==totals['unique_main_records']==2
    assert totals['eligibility_Needs Review']==1
    second=discovery.discover(False)
    assert second['new_relevant']==0
    assert second['diagnostics']['totals']['existing_or_merged']==2
    with factory() as session:
        assert len(list(session.scalars(select(JobRow))))==2
        assert len(list(session.scalars(select(MatchRow))))==4
        reports=list(session.scalars(select(SearchDiagnostic).order_by(SearchDiagnostic.id)))
        assert len(reports)==2
        assert json.loads(reports[1].payload)==second['diagnostics']


def test_failed_collection_has_unknown_counts(tmp_path, monkeypatch):
    import agent.discovery as discovery
    factory=database('sqlite:///' + str(tmp_path/'failed.db'))
    monkeypatch.setattr(discovery,'database',lambda:factory)
    monkeypatch.setattr(discovery,'load_boards',lambda:([
        {'company':'Synthetic','platform':'lever','board':'synthetic','enabled':True}],[]))
    monkeypatch.setattr(discovery,'collect',lambda *args: (_ for _ in ()).throw(RuntimeError('unavailable')))
    result=discovery.discover(False)
    report=result['diagnostics']['boards'][0]
    assert result['status']=='Failed' and not report['committed']
    assert report['published'] is None and report['normalized'] is None


def test_redirect_policy_checked_before_target_request():
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        if request.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nDisallow: /') if request.url.host=='blocked.example.org' else httpx.Response(404)
        return httpx.Response(302,headers={'Location':'https://blocked.example.org/jobs'})
    with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
        with pytest.raises(RuntimeError,match='disallows'):
            collect({'platform':'lever','board':'synthetic','company':'Synthetic'},client,0)
    assert 'https://blocked.example.org/jobs' not in requests

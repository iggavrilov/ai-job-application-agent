import httpx
import pytest
from agent.source_verification import board_links, verify_employer


def test_exact_board_links_and_spoofed_hosts():
    content='''<a href="https://jobs.lever.co/example/123">role</a>
    <iframe src="//boards.greenhouse.io/embed/job_board?for=exact-id"></iframe>
    https://boards-api.greenhouse.io/v1/boards/api-token/jobs
    https://jobs.ashbyhq.com/SomeBoard/123
    https://jobs.lever.co.evil.example/spoof
    https://example.org/?next=guess
    '''
    assert board_links(content)==[('ashby','SomeBoard'),('greenhouse','api-token'),('greenhouse','exact-id'),('lever','example')]


@pytest.mark.parametrize('api_status,verified',[(200,True),(403,False)])
def test_official_link_and_api_both_required(api_status,verified):
    def handler(request):
        if request.url.path=='/robots.txt':return httpx.Response(404)
        if request.url.host=='employer.example':
            return httpx.Response(200,text='<a href="https://jobs.lever.co/exact">Open jobs</a>')
        return httpx.Response(api_status,json=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        results=verify_employer('Synthetic','https://employer.example/careers',client)
    entry,evidence=results[0]
    assert entry['enabled'] is verified
    assert entry['board']=='exact'
    assert evidence['status']==('verified' if verified else 'unavailable')
    assert entry['evidence_url']=='https://employer.example/careers'


def test_no_link_cannot_guess_board():
    with httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(404) if request.url.path=='/robots.txt' else httpx.Response(200,text='We are hiring'))) as client:
        assert verify_employer('Synthetic','https://employer.example/careers',client)==[]


def test_disallowed_employer_page_is_never_read():
    requests=[]
    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200,text='User-agent: *\nDisallow: /')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError,match='disallows'):
            verify_employer('Synthetic','https://employer.example/careers',client)
    assert requests==['/robots.txt']


def test_ats_link_alone_does_not_establish_ownership():
    with httpx.Client(transport=httpx.MockTransport(lambda request:pytest.fail('No request expected'))) as client:
        with pytest.raises(ValueError,match='ownership evidence'):
            verify_employer('Unverified name','https://jobs.lever.co/example',client)


def test_official_redirect_checks_destination_policy_first():
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        if request.url.path=='/robots.txt':
            return httpx.Response(200,text='User-agent: *\nDisallow: /') if request.url.host=='careers.employer.example' else httpx.Response(404)
        return httpx.Response(302,headers={'Location':'https://careers.employer.example/jobs'})
    with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
        with pytest.raises(RuntimeError,match='disallows'):
            verify_employer('Synthetic','https://employer.example/careers',client)
    assert 'https://careers.employer.example/jobs' not in requests


def test_official_subpage_records_observed_evidence():
    def handler(request):
        if request.url.path=='/robots.txt':return httpx.Response(404)
        if request.url.host=='employer.example':
            body='<a href="/careers/openings">Open roles</a>' if request.url.path=='/careers' else '<a href="https://jobs.lever.co/exact">Apply</a>'
            return httpx.Response(200,text=body)
        return httpx.Response(200,json=[])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        entry,evidence=verify_employer('Synthetic','https://employer.example/careers',client)[0]
    assert entry['enabled']
    assert evidence['observed_on']=='https://employer.example/careers/openings'


def test_unknown_ats_robots_policy_never_calls_job_api():
    requests=[]
    def handler(request):
        requests.append(str(request.url))
        if request.url.path=='/robots.txt':return httpx.Response(401 if request.url.host=='api.lever.co' else 404)
        return httpx.Response(200,text='<a href="https://jobs.lever.co/exact">Jobs</a>')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        entry,evidence=verify_employer('Synthetic','https://employer.example/careers',client)[0]
    assert not entry['enabled'] and evidence['policy_http_status']==401
    assert not any('/v0/postings/' in url for url in requests)

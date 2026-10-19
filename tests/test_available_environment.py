"""Offline subset runnable without pytest/SQLAlchemy/Streamlit.

Run with system Python: python -m unittest discover -s tests -p test_available_environment.py -v
Mock vacancies exist only in memory and are never written as live results.
"""
import os
import unittest
from unittest.mock import patch
import httpx
from pydantic import ValidationError
from agent.models import Candidate, Job, Assessment
from agent.settings import rules
from tests.support import candidate_fixture
from agent.collectors import collect, ENDPOINTS
from agent.matching import evaluate, geography, relevant, canonical, fingerprint, score_parts


class OfflineValidation(unittest.TestCase):
    def setUp(self):
        self.candidate = candidate_fixture()
        self.job = Job(title='Senior QA Automation Engineer', company='Mock employer',
          location='Remote - Moldova', remote='Remote',
          description='Playwright JavaScript API testing SQL Drupal Regression testing',
          url='https://example.org/jobs/1', platform='lever', source_id='1')

    def test_profile_validation(self):
        candidate = self.candidate
        self.assertTrue(candidate.name.strip())
        self.assertEqual(candidate.work_authorization, 'unknown')
        self.assertEqual(candidate.languages, [])
        with self.assertRaises(ValidationError):
            Candidate.model_validate(candidate.model_dump() | {'experience_years': -1})

    def test_mocked_greenhouse_lever_ashby(self):
        samples = {
          'greenhouse': {'id':1,'title':'QA Engineer','location':{'name':'Remote - Moldova'},'content':'Original <b>HTML</b>','absolute_url':'https://example.org/1'},
          'lever': {'id':'1','text':'QA Engineer','categories':{'location':'Moldova'},'description':'Original <b>HTML</b>','hostedUrl':'https://example.org/1','workplaceType':'remote'},
          'ashby': {'id':'1','title':'QA Engineer','location':'Moldova','descriptionHtml':'Original <b>HTML</b>','jobUrl':'https://example.org/1','isRemote':True},
        }
        for platform, sample in samples.items():
            with self.subTest(platform=platform):
                requests = []
                def handler(request):
                    requests.append(str(request.url))
                    if request.url.path == '/robots.txt':
                        return httpx.Response(404)
                    return httpx.Response(200, json=[sample] if platform == 'lever' else {'jobs':[sample]})
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    jobs = collect({'platform':platform,'board':'mock-board','company':'Mock employer'}, client, 0)
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0].remote, 'Remote')
                self.assertIn('Original <b>HTML</b>', jobs[0].description)
                self.assertIsNone(jobs[0].salary)
                self.assertEqual(requests[1], ENDPOINTS[platform].format(board='mock-board'))

    def test_keyword_filtering(self):
        for title, expected in [('Senior Manual QA Engineer',True),('QA Automation Engineer',True),('API QA Engineer',True),('DevOps Engineer',False),('Senior Software Developer',False),('QA Automation Architect',False)]:
            with self.subTest(title=title):
                self.assertEqual(relevant(self.job.model_copy(update={'title':title}),rules()),expected)

    def test_geographic_eligibility(self):
        for location, expected in [('Remote - Moldova','Eligible'),('Worldwide','Eligible'),('Europe','Needs Review'),('Romania','Needs Review'),('Cyprus','Needs Review'),('United States','Ineligible')]:
            with self.subTest(location=location):
                self.assertEqual(geography(self.job.model_copy(update={'location':location}),self.candidate)[0],expected)

    def test_unknown_work_authorization(self):
        match = evaluate(self.job.model_copy(update={'description':'Work authorization required'}), self.candidate, rules(),False)
        self.assertEqual(match.eligibility,'Needs Review')
        self.assertEqual(match.recommendation,'Needs Review')

    def test_scoring_and_missing_salary(self):
        assessment = Assessment(technical=1,experience=1,additional=1,matching_skills=[],missing_skills=[],mandatory_requirements=[],uncertain_mandatory=False,explanation='test')
        self.assertEqual(score_parts(assessment,'Eligible',rules()),100)
        match = evaluate(self.job,self.candidate,rules(),False)
        low_salary = evaluate(self.job.model_copy(update={'salary':'1 EUR/year'}),self.candidate,rules(),False)
        self.assertEqual(match.score,low_salary.score)
        self.assertEqual(match.recommendation,'Strong Match')

    def test_eligibility_gate(self):
        match = evaluate(self.job.model_copy(update={'location':'United States'}),self.candidate,rules(),False)
        self.assertEqual(match.recommendation,'Not Recommended')

    def test_uncertain_mandatory_requirements(self):
        match = evaluate(self.job.model_copy(update={'description':self.job.description+'; Fluent English required'}),self.candidate,rules(),False)
        self.assertEqual(match.recommendation,'Needs Review')

    def test_duplicate_keys(self):
        self.assertEqual(canonical('https://EXAMPLE.org/jobs/1/?utm_source=x#top'),'https://example.org/jobs/1')
        self.assertNotEqual(canonical('https://example.org/?job=1'),canonical('https://example.org/?job=2'))
        self.assertEqual(fingerprint(self.job),fingerprint(self.job.model_copy(update={'title':'SENIOR QA Automation Engineer','source_id':'other'})))

    def test_collector_http_errors(self):
        for platform in ENDPOINTS:
            for code in [429,503]:
                with self.subTest(platform=platform,code=code):
                    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(404 if r.url.path=='/robots.txt' else code))) as client:
                        if code == 429:
                            with self.assertRaises(RuntimeError) as limited:
                                collect({'platform':platform,'board':'mock','company':'Mock'},client,0)
                            self.assertEqual(limited.exception.policy_http_status,429)
                        else:
                            with self.assertRaises(httpx.HTTPStatusError):
                                collect({'platform':platform,'board':'mock','company':'Mock'},client,0)

    def test_robots_restrictions(self):
        with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text='User-agent: *\nDisallow: /'))) as client:
            with self.assertRaisesRegex(RuntimeError,'disallows'):
                collect({'platform':'lever','board':'mock','company':'Mock'},client,0)

    def test_ai_response_schema(self):
        for data in ['{}','{"technical":101}','not json']:
            with self.subTest(data=data), self.assertRaises(ValidationError):
                Assessment.model_validate_json(data)

    def test_no_api_key_rules(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':''}):
            self.assertEqual(evaluate(self.job,self.candidate,rules()).method,'rules')

    def test_missing_openai_fallback(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'mock-key'}), patch.dict('sys.modules',{'openai':None}):
            self.assertIn('AI fallback: ModuleNotFoundError',evaluate(self.job,self.candidate,rules()).method)


if __name__ == '__main__':
    unittest.main()

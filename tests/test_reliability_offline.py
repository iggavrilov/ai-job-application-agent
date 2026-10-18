"""Regression tests that use only dependencies available in the managed environment."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
from agent.collectors import collect, normalize, remote_status
from agent.matching import evaluate, region, mentions
from agent.registry import load_boards
from agent.settings import profile, rules
from agent.models import Job


class ReliabilityOffline(unittest.TestCase):
    def setUp(self):
        self.job = Job(title='QA Engineer',company='Mock',location='Worldwide',remote='Remote',
          description='Playwright JavaScript SQL API testing Regression testing Drupal',
          url='https://example.org/job',platform='ashby',source_id='mock')

    def test_requested_titles(self):
        from agent.matching import relevant
        titles = ['QA Engineer','Manual QA Engineer','Senior QA Engineer','Software QA Engineer','QA Analyst','Software Tester','API QA Engineer','Web QA Engineer','CMS QA Engineer','QA Automation Engineer','Playwright QA Engineer','Middle QA Engineer','Integration QA Engineer']
        for title in titles:
            with self.subTest(title=title):
                self.assertTrue(relevant(self.job.model_copy(update={'title':title}),rules()))

    def test_restrictive_remote_wording(self):
        for description in ['We work worldwide. EU only.', 'Remote worldwide; must be based in Germany.', 'Remote from anywhere; candidates from selected countries only.', 'Worldwide except Moldova.', 'Remote worldwide, work permit required.']:
            with self.subTest(description=description):
                result = evaluate(self.job.model_copy(update={'description':description}),profile(),rules(),False)
                self.assertNotEqual(result.eligibility,'Eligible')
                self.assertIn(result.recommendation,['Needs Review','Not Recommended'])

    def test_conflicting_global_location(self):
        result=evaluate(self.job.model_copy(update={'location':'Worldwide / Germany'}),profile(),rules(),False)
        self.assertEqual(result.eligibility,'Needs Review')

    def test_changed_candidate_location(self):
        p=profile().model_copy(update={'location':'unknown'})
        self.assertEqual(evaluate(self.job,p,rules(),False).eligibility,'Needs Review')

    def test_cyprus_is_eu(self):
        self.assertEqual(region('Cyprus'),region('France'))
        self.assertEqual(region('Moldova'),'Moldova')
        self.assertEqual(region('Romania'),'Romania')

    def test_remote_source_precedence(self):
        self.assertEqual(remote_status('Remote - EU','onsite'),'Onsite')
        self.assertEqual(remote_status('Remote - EU','hybrid'),'Hybrid')
        self.assertEqual(remote_status('EU',False),'Onsite')
        self.assertEqual(remote_status('EU',True),'Remote')

    def test_ashby_without_id(self):
        job=normalize('ashby','Mock',{'title':'QA Engineer','location':'Europe','jobUrl':'https://jobs.ashbyhq.com/mock/abc','descriptionPlain':'Raw description','isRemote':True})
        self.assertEqual(job.source_id,'https://jobs.ashbyhq.com/mock/abc')
        self.assertEqual(job.description,'Raw description')

    def test_malformed_posting_is_isolated(self):
        for platform in ['greenhouse','lever','ashby']:
            with self.subTest(platform=platform):
                samples={
                  'greenhouse':{'id':1,'title':'QA Engineer','absolute_url':'https://example.org/1'},
                  'lever':{'id':'1','text':'QA Engineer','hostedUrl':'https://example.org/1'},
                  'ashby':{'title':'QA Engineer','jobUrl':'https://example.org/1'},
                }
                def handler(r):
                    if r.url.path=='/robots.txt':
                        return httpx.Response(404)
                    items=[None,{},samples[platform]]
                    return httpx.Response(200,json=items if platform=='lever' else {'jobs':items})
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    result=collect({'platform':platform,'board':'mock','company':'Mock'},client,0)
                self.assertEqual(len(result),1)
                self.assertEqual(len(result.warnings),2)

    def test_registry_invalid_and_duplicate_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'boards.yaml'
            path.write_text(json.dumps({'boards':[
              {'company':'Mock','platform':'ashby','board':'mock'},
              {'company':'Duplicate','platform':'ashby','board':'mock'},
              {'company':'Bad','platform':'ashby','board':'../bad'},
              {'company':'Bad platform','platform':'linkedin','board':'mock'}]}),encoding='utf-8')
            valid, errors=load_boards(path)
            self.assertEqual(len(valid),1)
            self.assertEqual(len(errors),3)
            self.assertEqual(valid[0]['verification_status'],'pending')

    def test_registry_evidence_is_pending(self):
        boards,errors=load_boards()
        self.assertFalse(errors)
        self.assertEqual(boards,[])
        from agent.settings import ROOT
        pending,errors=load_boards(ROOT/'config/company_boards.unverified.yaml')
        self.assertFalse(errors)
        self.assertEqual(len(pending),3)
        self.assertTrue(all(b['verification_status']=='pending' and not b['enabled'] for b in pending))

    def test_skill_boundaries(self):
        self.assertFalse(mentions('JavaScript','Java'))
        self.assertFalse(mentions('NoSQL','SQL'))
        self.assertTrue(mentions('Required C++ development','C++'))
        result=evaluate(self.job.model_copy(update={'description':'C++ required'}),profile(),rules(),False)
        self.assertIn('C++',result.missing_skills)

    def test_openai_cannot_remove_uncertainty(self):
        payload={'technical':1,'experience':1,'additional':1,'matching_skills':['Playwright','Invented skill'],'missing_skills':[],
          'mandatory_requirements':[],'uncertain_mandatory':False,'explanation':'Mock response'}
        response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
        class Client:
            def __init__(self,**kwargs):
                self.chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:response))
            def __enter__(self):
                return self
            def __exit__(self,*args):
                pass
        with patch.dict(os.environ,{'OPENAI_API_KEY':'mock'}),patch.dict('sys.modules',{'openai':SimpleNamespace(OpenAI=Client)}):
            result=evaluate(self.job.model_copy(update={'description':'Fluent English required; Playwright'}),profile(),rules())
        self.assertEqual(result.recommendation,'Needs Review')
        self.assertNotIn('Invented skill',result.matching_skills)
        self.assertEqual(result.method,'openai+rules')

    def test_invalid_openai_response_falls_back(self):
        class Client:
            def __init__(self,**kw):
                response=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"technical":999}'))])
                self.chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw:response))
            def __enter__(self):
                return self
            def __exit__(self,*args):
                pass
        with patch.dict(os.environ,{'OPENAI_API_KEY':'mock'}),patch.dict('sys.modules',{'openai':SimpleNamespace(OpenAI=Client)}):
            result=evaluate(self.job,profile(),rules())
        self.assertIn('fallback: ValidationError',result.method)

    def test_salary_configuration_rejected(self):
        import yaml
        from agent.settings import ROOT
        config=rules() | {'minimum_salary':100}
        with patch.object(Path,'read_text',return_value=yaml.safe_dump(config)):
            with self.assertRaisesRegex(ValueError,'minimum_salary null'):
                rules()


    def test_language_proficiency_needs_review(self):
        for description in ['Fluent English', 'English C1', 'German native proficiency']:
            with self.subTest(description=description):
                result=evaluate(self.job.model_copy(update={'description':self.job.description+'; '+description}),profile(),rules(),False)
                self.assertEqual(result.recommendation,'Needs Review')

    def test_verified_registry_requires_evidence(self):
        from agent.registry import Board
        from pydantic import ValidationError
        data={'company':'Mock','platform':'lever','board':'mock','verification_status':'verified'}
        with self.assertRaises(ValidationError):
            Board.model_validate(data)
        verified=Board.model_validate(data | {'verified_at':'2026-10-16T09:00:00+00:00','evidence_url':'https://example.org/careers'})
        self.assertEqual(verified.verification_status,'verified')

    def test_encoded_html_is_readable(self):
        from agent.collectors import plain
        self.assertEqual(plain('&lt;p&gt;QA &amp; APIs&lt;/p&gt;'),'QA & APIs')


    def test_board_verification_continues_after_failure(self):
        import contextlib
        import io
        from agent.cli import verify_boards
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch('agent.cli.ROOT',root), patch('agent.collectors.collect',side_effect=[RuntimeError('offline'),[self.job],[self.job]]),contextlib.redirect_stdout(io.StringIO()):
                from agent.settings import ROOT
                code=verify_boards(ROOT/'config/company_boards.unverified.yaml',include_disabled=True)
            report=json.loads((root/'data/board_verification.json').read_text(encoding='utf-8'))
            self.assertEqual(code,1)
            self.assertEqual(len(report['results']),3)
            self.assertEqual(report['results'][0]['status'],'Unavailable')
            self.assertEqual(report['results'][1]['status'],'Accessible')
            self.assertTrue(all(r['ownership'].startswith('Requires manual') for r in report['results']))


if __name__=='__main__':
    unittest.main()

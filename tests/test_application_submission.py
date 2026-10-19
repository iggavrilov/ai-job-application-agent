import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import HttpUrl
from streamlit.testing.v1 import AppTest

from agent.application_submission import (
    APPLICATION_STATES,
    ApplicationSubmissionError,
    ReviewInvalidatedError,
    SubmissionUncertainError,
    _has_reliable_submission_confirmation,
    approve_and_submit,
    build_review_snapshot,
    load_application_review,
    review_fingerprint,
)
from agent.application_workflow import FieldResult, _private_paths


SYNTHETIC_URL = 'https://job-boards.eu.greenhouse.io/synthetic/jobs/123'


def _snapshot(job_id=7, url=SYNTHETIC_URL):
    return {
        'job_id': job_id,
        'employer': 'Synthetic Employer',
        'job_title': 'Synthetic QA Engineer',
        'application_url': url,
        'candidate': {
            'name': 'Synthetic Applicant',
            'preferred_first_name': 'Sample',
            'email': 'synthetic@example.invalid',
            'phone': '+999000000',
            'current_residence': 'Sample City, Exampleland',
        },
        'form_data': {
            'country': 'Exampleland +999',
            'location_city': 'Sample City, Exampleland',
            'salary_expectation': '5,200 TEST/month',
            'work_authorization': 'Yes',
            'recruitment_data_processing_consent': 'Acknowledge',
        },
        'attached_cv': {
            'filename': 'synthetic-cv.pdf',
            'size_bytes': 25,
            'sha256': 'a' * 64,
        },
        'form_fields': [{'id': 'synthetic-input', 'label': 'Synthetic field', 'value': 'Synthetic answer'}],
        'form_structure': [{'action': SYNTHETIC_URL, 'method': 'POST'}],
    }


def _write_ready_application(root, *, snapshot=None, status='Ready for Approval', source='source-v1'):
    snapshot = snapshot or _snapshot()
    preview_path, state_path = _private_paths(root, 7)[1:]
    review_hash = review_fingerprint(snapshot)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    preview_path.write_text(json.dumps({
        'job_id': 7,
        'application_url': snapshot['application_url'],
        'review_snapshot': snapshot,
        'review_fingerprint': review_hash,
    }), encoding='utf-8')
    state_path.write_text(json.dumps({
        'job_id': 7,
        'status': status,
        'application_url': snapshot['application_url'],
        'candidate_input_fields': [],
        'review_fingerprint': review_hash,
        'source_fingerprint': source,
    }), encoding='utf-8')
    return state_path, review_hash


def _patch_sources(monkeypatch, root, *, source='source-v1', url=SYNTHETIC_URL):
    import agent.application_submission as submission
    job = SimpleNamespace(company='Synthetic Employer', title='Synthetic QA Engineer', url=HttpUrl(url))
    cv = root / 'synthetic-cv.pdf'
    cv.write_bytes(b'%PDF-1.4 synthetic fixture')
    monkeypatch.setattr(submission, '_load_current_sources', lambda job_id, _root: (
        job, object(), object(), object(), cv, {}, source,
    ))
    return job, cv


def test_application_states_are_separate_named_workflow_states():
    assert APPLICATION_STATES == (
        'Prepared', 'Needs Review', 'Ready for Approval', 'Approved',
        'Submitting', 'Submitted', 'Submission Failed', 'Submission Uncertain',
    )


def test_review_snapshot_binds_candidate_answers_url_and_cv(tmp_path):
    cv = tmp_path / 'synthetic-cv.pdf'
    cv.write_bytes(b'%PDF-1.4 synthetic fixture')
    job = SimpleNamespace(company='Synthetic Employer', title='Synthetic QA Engineer', url=HttpUrl(SYNTHETIC_URL))
    results = [
        FieldResult('First name', 'Completed', 'Verified value: Synthetic'),
        FieldResult('Last name', 'Completed', 'Verified value: Applicant'),
        FieldResult('Preferred First Name', 'Completed', 'Verified value: Sample'),
        FieldResult('Email', 'Completed', 'Verified value: synthetic@example.invalid'),
        FieldResult('Phone', 'Completed', 'Verified value: +999 000 000'),
        FieldResult('Country', 'Completed', 'Verified value: Exampleland +999'),
        FieldResult('Location (City)', 'Completed', 'Verified value: Sample City, Exampleland'),
        FieldResult('Salary expectations', 'Completed', 'Verified value: 5,200 TEST/month'),
        FieldResult('Work authorization', 'Completed', 'Verified value: Yes'),
        FieldResult('Recruitment data-processing consent', 'Completed', 'Verified value: Acknowledge'),
        FieldResult('Resume/CV', 'Uploaded', f'Verified attached file: {cv.name} ({cv.stat().st_size} bytes)'),
    ]
    mapped = {
        'first_name': 'Synthetic', 'last_name': 'Applicant',
        'preferred_first_name': 'Sample', 'email': 'synthetic@example.invalid',
        'phone': '+999000000', 'country': 'Exampleland',
        'current_residence': 'Sample City, Exampleland',
        'salary_expectation': '5,200 TEST/month',
    }
    answers = SimpleNamespace(work_authorization='Yes',
                              recruitment_data_processing_consent='Acknowledge',
                              location_city='Sample City', salary_form_value='5,200 TEST/month')
    snapshot = build_review_snapshot(7, job, mapped,
                                     answers, cv, results,
                                     {'forms': [{'action': SYNTHETIC_URL, 'method': 'POST'}],
                                      'fields': [{'id': 'city', 'label': 'Location', 'value': 'Sample City'}]})
    assert snapshot['job_id'] == 7
    assert snapshot['application_url'] == SYNTHETIC_URL
    assert snapshot['candidate']['name'] == 'Synthetic Applicant'
    assert snapshot['form_data']['location_city'] == 'Sample City, Exampleland'
    assert snapshot['form_data']['work_authorization'] == 'Yes'
    assert snapshot['form_data']['recruitment_data_processing_consent'] == 'Acknowledge'
    assert snapshot['attached_cv']['sha256'] == __import__('hashlib').sha256(cv.read_bytes()).hexdigest()
    assert snapshot['form_fields'][0]['id'] == 'city'


def test_fingerprint_changes_when_any_reviewed_form_value_changes():
    initial = _snapshot()
    changed_city = _snapshot()
    changed_city['form_data']['location_city'] = 'Other Synthetic City'
    changed_url = _snapshot(url='https://job-boards.eu.greenhouse.io/synthetic/jobs/456')
    changed_cv = _snapshot()
    changed_cv['attached_cv']['sha256'] = 'b' * 64
    changed_form = _snapshot()
    changed_form['form_fields'][0]['value'] = 'Changed answer'
    assert len({review_fingerprint(initial), review_fingerprint(changed_city),
                review_fingerprint(changed_url), review_fingerprint(changed_cv),
                review_fingerprint(changed_form)}) == 5


def test_legacy_preparation_is_not_treated_as_approval(tmp_path):
    state_path, _ = _write_ready_application(tmp_path, status='Awaiting approval')
    state, snapshot, fingerprint = load_application_review(7, tmp_path)
    assert state['status'] == 'Needs Review'
    assert state['approval'] is None
    assert snapshot is None and fingerprint is None
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Needs Review'


def test_interrupted_submitting_state_becomes_uncertain_and_cannot_retry(tmp_path):
    state_path, fingerprint = _write_ready_application(tmp_path, status='Submitting')
    state, snapshot, current_fingerprint = load_application_review(7, tmp_path)
    assert state['status'] == 'Submission Uncertain'
    assert snapshot is None and current_fingerprint is None
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Submission Uncertain'
    with pytest.raises(ApplicationSubmissionError, match='fully prepared'):
        approve_and_submit(7, fingerprint, candidate_confirmed=True, root=tmp_path,
                           submitter=lambda *args: pytest.fail('uncertain applications must not retry'))


def test_unanswered_required_question_prevents_approval(tmp_path, monkeypatch):
    state_path, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)
    state = json.loads(state_path.read_text(encoding='utf-8'))
    state['candidate_input_fields'] = [{'label': 'Synthetic required question'}]
    state_path.write_text(json.dumps(state), encoding='utf-8')
    with pytest.raises(ApplicationSubmissionError, match='fully prepared'):
        approve_and_submit(7, fingerprint, candidate_confirmed=True, root=tmp_path,
                           submitter=lambda *args: pytest.fail('incomplete applications must not submit'))
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Needs Review'


def test_stale_candidate_answer_or_cv_source_invalidates_review(tmp_path, monkeypatch):
    state_path, _ = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path, source='changed-source')
    state, snapshot, fingerprint = load_application_review(7, tmp_path)
    assert state['status'] == 'Needs Review'
    assert state['approval'] is None
    assert snapshot is None and fingerprint is None
    assert 'changed' in state['state_note']
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Needs Review'


def test_submission_requires_fresh_confirmation_for_exact_snapshot(tmp_path, monkeypatch):
    _, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)
    calls = []
    with pytest.raises(ApplicationSubmissionError, match='Explicit confirmation'):
        approve_and_submit(7, fingerprint, candidate_confirmed=False, root=tmp_path,
                           submitter=lambda *args: calls.append(args))
    assert calls == []
    with pytest.raises(ApplicationSubmissionError, match='snapshot changed'):
        approve_and_submit(7, 'wrong-snapshot', candidate_confirmed=True, root=tmp_path,
                           submitter=lambda *args: calls.append(args))
    assert calls == []


def test_approval_is_bound_to_vacancy_url_and_single_attempt(tmp_path, monkeypatch):
    state_path, fingerprint = _write_ready_application(tmp_path)
    job, _ = _patch_sources(monkeypatch, tmp_path)
    calls = []

    def fake_submitter(job_id, submitted_job, candidate, preferences, answers, cv_path,
                       mapped, approved_fingerprint, root):
        calls.append((job_id, str(submitted_job.url), approved_fingerprint))
        return 'Submitted'

    state = approve_and_submit(7, fingerprint, candidate_confirmed=True,
                               root=tmp_path, submitter=fake_submitter)
    assert state['status'] == 'Submitted'
    assert calls == [(7, SYNTHETIC_URL, fingerprint)]
    assert state['approval']['job_id'] == 7
    assert state['approval']['application_url'] == SYNTHETIC_URL
    assert state['approval']['review_fingerprint'] == fingerprint
    assert state['approval']['fresh_candidate_confirmation'] is True
    with pytest.raises(ApplicationSubmissionError, match='fully prepared'):
        approve_and_submit(7, fingerprint, candidate_confirmed=True,
                           root=tmp_path, submitter=fake_submitter)
    assert len(calls) == 1
    assert job.company == 'Synthetic Employer'
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Submitted'


def test_concurrent_approval_attempts_cannot_submit_twice(tmp_path, monkeypatch):
    state_path, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)
    submitter_entered = threading.Event()
    release_submitter = threading.Event()
    first_result = []
    calls = []

    def submitter(*args):
        calls.append(args)
        submitter_entered.set()
        assert release_submitter.wait(timeout=5)
        return 'Submitted'

    first = threading.Thread(target=lambda: first_result.append(approve_and_submit(
        7, fingerprint, candidate_confirmed=True, root=tmp_path, submitter=submitter)))
    first.start()
    assert submitter_entered.wait(timeout=5)
    try:
        with pytest.raises(ApplicationSubmissionError, match='already active'):
            approve_and_submit(7, fingerprint, candidate_confirmed=True, root=tmp_path,
                               submitter=lambda *args: pytest.fail('parallel approval must not submit'))
    finally:
        release_submitter.set()
        first.join(timeout=5)
    assert not first.is_alive()
    assert len(calls) == 1
    assert first_result[0]['status'] == 'Submitted'
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Submitted'


def test_uncertain_submission_is_never_automatically_retried(tmp_path, monkeypatch):
    _, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)
    calls = []

    def uncertain(*args):
        calls.append(1)
        raise SubmissionUncertainError('Synthetic response was ambiguous.')

    state = approve_and_submit(7, fingerprint, candidate_confirmed=True,
                               root=tmp_path, submitter=uncertain)
    assert state['status'] == 'Submission Uncertain'
    with pytest.raises(ApplicationSubmissionError, match='fully prepared'):
        approve_and_submit(7, fingerprint, candidate_confirmed=True,
                           root=tmp_path, submitter=uncertain)
    assert calls == [1]


def test_confirmation_text_alone_does_not_mark_application_submitted():
    official = 'https://job-boards.eu.greenhouse.io/synthetic/confirmation'
    assert not _has_reliable_submission_confirmation(
        official, 'Thank you for applying', application_form_visible=True)
    assert not _has_reliable_submission_confirmation(
        official, 'Application is still being processed', application_form_visible=False)
    assert not _has_reliable_submission_confirmation(
        'https://example.invalid/confirmation', 'Thank you for applying',
        application_form_visible=False)
    assert _has_reliable_submission_confirmation(
        official, 'Thank you for applying', application_form_visible=False)


def test_delayed_or_ambiguous_response_stays_uncertain_and_cannot_retry(tmp_path, monkeypatch):
    _, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)
    calls = []

    def delayed_or_ambiguous(*args):
        calls.append('attempted')
        # A delayed response that has not produced the reliable confirmation
        # contract must never be translated into a successful application state.
        raise SubmissionUncertainError('Synthetic delayed Greenhouse response.')

    state = approve_and_submit(7, fingerprint, candidate_confirmed=True,
                               root=tmp_path, submitter=delayed_or_ambiguous)
    assert state['status'] == 'Submission Uncertain'
    with pytest.raises(ApplicationSubmissionError, match='fully prepared'):
        approve_and_submit(7, fingerprint, candidate_confirmed=True,
                           root=tmp_path, submitter=delayed_or_ambiguous)
    assert calls == ['attempted']


def test_browser_crash_after_submit_dispatch_is_uncertain_and_not_retried(tmp_path, monkeypatch):
    _, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)
    calls = []

    def crashed_browser(*args):
        calls.append('submit-clicked')
        raise SubmissionUncertainError('Synthetic browser crash after submit click.')

    state = approve_and_submit(7, fingerprint, candidate_confirmed=True,
                               root=tmp_path, submitter=crashed_browser)
    assert state['status'] == 'Submission Uncertain'
    with pytest.raises(ApplicationSubmissionError, match='fully prepared'):
        approve_and_submit(7, fingerprint, candidate_confirmed=True,
                           root=tmp_path, submitter=crashed_browser)
    assert calls == ['submit-clicked']


class _SyntheticSubmitControl:
    def __init__(self, page):
        self.page = page

    def count(self):
        return 1

    def is_visible(self):
        return True

    def is_enabled(self):
        return self.page.submit_enabled

    def click(self, timeout):
        self.page.clicks += 1
        if self.page.click_error:
            raise RuntimeError('synthetic browser crash after submit click')


class _SyntheticGreenhousePage:
    def __init__(self, *, initial_body='', confirmation_text='', form_visible=False,
                 click_error=False, final_url=SYNTHETIC_URL):
        self.initial_body = initial_body
        self.confirmation_text = confirmation_text
        self.form_visible = form_visible
        self.click_error = click_error
        self.url = final_url
        self.submit_enabled = False
        self.clicks = 0
        self.closed = False
        self.close_error = False
        self.delayed_response_error = False
        self.screenshots = []

    def add_init_script(self, script):
        assert 'jobAgentApprovedSubmission' in script

    def goto(self, url, wait_until, timeout):
        assert url == SYNTHETIC_URL
        assert wait_until == 'domcontentloaded'
        return SimpleNamespace(status=200)

    def wait_for_timeout(self, timeout):
        if timeout == 3000 and self.delayed_response_error:
            raise TimeoutError('synthetic delayed Greenhouse response')
        return None

    def evaluate(self, script):
        if 'window.__jobAgentApprovedSubmission = true' in script:
            self.submit_enabled = True
            return None
        if 'applicationFormVisible' in script:
            return {'text': self.confirmation_text,
                    'applicationFormVisible': self.form_visible}
        if 'document.body ? document.body.innerText' in script:
            return self.initial_body
        raise AssertionError('Unexpected synthetic page evaluation')

    def locator(self, selector):
        assert selector == 'button[type="submit"]'
        return _SyntheticSubmitControl(self)

    def screenshot(self, path, full_page):
        self.screenshots.append((path, full_page))


class _SyntheticBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self, accept_downloads):
        assert accept_downloads is False
        return self.page

    def close(self):
        if self.page.close_error:
            raise RuntimeError('synthetic browser cleanup failure')
        self.closed = True


class _SyntheticPlaywrightContext:
    def __init__(self, browser):
        self.browser = browser
        self.chromium = SimpleNamespace(launch=self._launch)

    def _launch(self, channel, headless):
        assert channel == 'msedge'
        assert headless is False
        return self.browser

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _run_synthetic_live_submit(monkeypatch, tmp_path, page, *, changed_snapshot=False):
    import playwright.sync_api
    import agent.application_submission as submission

    browser = _SyntheticBrowser(page)
    monkeypatch.setattr(playwright.sync_api, 'sync_playwright',
                        lambda: _SyntheticPlaywrightContext(browser))
    synthetic_results = [FieldResult('Synthetic required field', 'Completed',
                                     'Verified value: Synthetic answer')]
    monkeypatch.setitem(submission.ADAPTERS, 'greenhouse',
                        SimpleNamespace(fill=lambda *args: synthetic_results))
    monkeypatch.setattr(submission, '_reject_explicitly_ineligible', lambda *args: None)
    monkeypatch.setattr(submission, '_form_dom_snapshot', lambda page: {'forms': [], 'fields': []})
    state_path = tmp_path / 'private' / 'application_runs' / 'job-7' / 'application_state.json'
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'job_id': 7, 'status': 'Submitting'}), encoding='utf-8')
    monkeypatch.setattr(submission, '_private_paths',
                        lambda job_id, root: (state_path.parent,
                                              state_path.with_name('application_preview.json'),
                                              state_path))
    snap = _snapshot()
    if changed_snapshot:
        snap['form_data']['location_city'] = 'Changed Synthetic City'
    monkeypatch.setattr(submission, 'build_review_snapshot', lambda *args: snap)
    job = SimpleNamespace(platform='greenhouse', url=SYNTHETIC_URL)
    outcome = lambda: submission._submit_live_application(
        7, job, object(), object(), object(), tmp_path / 'synthetic-cv.pdf',
        {}, review_fingerprint(_snapshot()), tmp_path)
    return outcome, page, browser, state_path


def test_synthetic_greenhouse_changed_form_stops_before_submit(tmp_path, monkeypatch):
    page = _SyntheticGreenhousePage()
    run, page, browser, state_path = _run_synthetic_live_submit(
        monkeypatch, tmp_path, page, changed_snapshot=True)
    with pytest.raises(ReviewInvalidatedError, match='reviewed snapshot'):
        run()
    assert page.clicks == 0
    assert browser.closed
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Needs Review'


def test_synthetic_greenhouse_captcha_stops_before_submit(tmp_path, monkeypatch):
    page = _SyntheticGreenhousePage(initial_body='Please verify you are human')
    run, page, browser, _ = _run_synthetic_live_submit(monkeypatch, tmp_path, page)
    with pytest.raises(ApplicationSubmissionError, match='challenge appeared'):
        run()
    assert page.clicks == 0
    assert browser.closed


@pytest.mark.parametrize('challenge', [
    'Sign in to continue',
    'Authentication required',
    'Please verify your identity',
])
def test_synthetic_greenhouse_authentication_challenge_stops_before_submit(
        tmp_path, monkeypatch, challenge):
    page = _SyntheticGreenhousePage(initial_body=challenge)
    run, page, browser, _ = _run_synthetic_live_submit(monkeypatch, tmp_path, page)
    with pytest.raises(ApplicationSubmissionError, match='challenge appeared'):
        run()
    assert page.clicks == 0
    assert browser.closed


def test_synthetic_greenhouse_ambiguous_confirmation_is_uncertain(tmp_path, monkeypatch):
    page = _SyntheticGreenhousePage(confirmation_text='Thank you for applying', form_visible=True)
    run, page, browser, _ = _run_synthetic_live_submit(monkeypatch, tmp_path, page)
    with pytest.raises(SubmissionUncertainError, match='clear confirmation'):
        run()
    assert page.clicks == 1
    assert browser.closed


def test_synthetic_greenhouse_browser_crash_and_cleanup_failure_remain_uncertain(tmp_path, monkeypatch):
    page = _SyntheticGreenhousePage(click_error=True)
    page.close_error = True
    run, page, browser, _ = _run_synthetic_live_submit(monkeypatch, tmp_path, page)
    with pytest.raises(SubmissionUncertainError, match='may have reached'):
        run()
    assert page.clicks == 1


def test_synthetic_greenhouse_delayed_response_is_uncertain(tmp_path, monkeypatch):
    page = _SyntheticGreenhousePage()
    page.delayed_response_error = True
    run, page, browser, _ = _run_synthetic_live_submit(monkeypatch, tmp_path, page)
    with pytest.raises(SubmissionUncertainError, match='may have reached'):
        run()
    assert page.clicks == 1
    assert browser.closed


def test_synthetic_greenhouse_reliable_confirmation_returns_submitted(tmp_path, monkeypatch):
    page = _SyntheticGreenhousePage(confirmation_text='Thank you for applying',
                                    form_visible=False)
    run, page, browser, _ = _run_synthetic_live_submit(monkeypatch, tmp_path, page)
    assert run() == 'Submitted'
    assert page.clicks == 1
    assert browser.closed
    assert len(page.screenshots) == 1


def test_live_form_snapshot_mismatch_returns_to_needs_review(tmp_path, monkeypatch):
    state_path, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)

    def changed_live_form(*args):
        raise ReviewInvalidatedError('Synthetic live form changed.')

    state = approve_and_submit(7, fingerprint, candidate_confirmed=True,
                               root=tmp_path, submitter=changed_live_form)
    assert state['status'] == 'Needs Review'
    assert json.loads(state_path.read_text(encoding='utf-8'))['status'] == 'Needs Review'


def test_pre_dispatch_failure_is_recorded_without_changing_vacancy_review_status(
        tmp_path, monkeypatch):
    _, fingerprint = _write_ready_application(tmp_path)
    _patch_sources(monkeypatch, tmp_path)

    def failed(*args):
        raise RuntimeError('Synthetic before-dispatch failure')

    state = approve_and_submit(7, fingerprint, candidate_confirmed=True,
                               root=tmp_path, submitter=failed)
    assert state['status'] == 'Submission Failed'
    # Application state is stored in private application JSON, independently of
    # the jobs table and its New/Reviewed/Shortlisted/Rejected/Applied values.
    assert state['job_id'] == 7


def test_dashboard_shows_exact_snapshot_and_requires_current_confirmation(
        tmp_path, monkeypatch):
    import shutil
    from sqlalchemy import select
    from agent.db import Company, JobRow, MatchRow, Source, database
    from agent.models import Job, Match
    import agent.application_submission as submission
    import agent.application_workflow as workflow

    db_url = 'sqlite:///' + str(tmp_path / 'dashboard-submit.db')
    monkeypatch.setenv('DATABASE_URL', db_url)
    monkeypatch.setattr('agent.settings.ROOT', tmp_path)
    (tmp_path / 'config').mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / 'config' / 'search_rules.yaml',
                tmp_path / 'config' / 'search_rules.yaml')
    factory = database(db_url)
    job = Job(title='Synthetic QA Engineer', company='Synthetic Employer',
              location='Sample City, Exampleland', remote='Unknown',
              description='Synthetic public posting', requirements='Synthetic requirements',
              url=SYNTHETIC_URL, platform='greenhouse', source_id='123')
    match = Match(score=88, matching_skills=['Synthetic testing'], missing_skills=[],
                  mandatory_requirements=[], eligibility='Needs Review',
                  explanation='Synthetic match', recommendation='Good Match',
                  method='rules', professional_score=88, professional_fit='Good Match',
                  professional_reason='Synthetic match', geographic_reason='Synthetic review',
                  in_main_recommendations=True)
    with factory() as session:
        company = Company(name='Synthetic Employer')
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, platform='greenhouse', board='synthetic')
        session.add(source)
        session.flush()
        session.add(JobRow(id=4, source_id=source.id, identity='greenhouse:synthetic:123',
                           canonical_url=SYNTHETIC_URL, fingerprint='synthetic-fingerprint',
                           payload=job.model_dump_json(), status='Shortlisted'))
        session.flush()
        session.add(MatchRow(job_id=4, payload=match.model_dump_json(), version=match.version))
        session.commit()

    snap = _snapshot(4, SYNTHETIC_URL)
    state_path = tmp_path / 'private' / 'application_runs' / 'job-4' / 'application_state.json'
    preview_path = state_path.with_name('application_preview.json')
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({'job_id': 4, 'status': 'Ready for Approval',
                                      'completed_fields': ['all synthetic fields'],
                                      'candidate_input_fields': []}), encoding='utf-8')
    preview_path.write_text(json.dumps({'job_id': 4, 'review_snapshot': snap}), encoding='utf-8')
    submit_calls = []
    monkeypatch.setattr(submission, 'load_application_review',
                        lambda job_id, root: ({'status': 'Ready for Approval',
                                              'completed_fields': ['all synthetic fields'],
                                              'candidate_input_fields': []}, snap,
                                             review_fingerprint(snap)))
    monkeypatch.setattr(submission, 'approve_and_submit',
                        lambda *args, **kwargs: submit_calls.append((args, kwargs)) or {'status': 'Submitted'})
    monkeypatch.setattr(workflow, '_launch_from_dashboard', lambda job_id: None)

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
    assert not app.exception
    frame = app.dataframe[0].value
    row_index = int(frame.index[frame['ID'] == 4][0])
    app.session_state[app.dataframe[0].key] = {'selection': {'rows': [row_index], 'columns': [], 'cells': []}}
    app.run()
    assert not app.exception
    assert any('Synthetic Employer' in element.value for element in app.markdown)
    submit_button = next(button for button in app.button if button.label == 'Approve & Submit')
    submit_button.click().run()
    assert not app.exception
    assert not submit_calls
    assert any('confirmation' in error.value.lower() for error in app.error)
    confirmation = next(box for box in app.checkbox
                        if box.label.startswith('I reviewed this application for vacancy #4'))
    confirmation.check()
    submit_button = next(button for button in app.button if button.label == 'Approve & Submit')
    submit_button.click().run()
    assert not app.exception
    assert len(submit_calls) == 1
    args, kwargs = submit_calls[0]
    assert args[0] == 4
    assert args[1] == review_fingerprint(snap)
    assert kwargs['candidate_confirmed'] is True
    with factory() as session:
        status = session.get(JobRow, 4).status
    assert status == 'Shortlisted'

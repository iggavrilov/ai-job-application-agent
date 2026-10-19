"""Candidate-approved, single-attempt submission for prepared applications.

All application state is private JSON under ``private/application_runs`` and is
kept separate from the vacancy review status stored in SQLite.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from agent.application_workflow import (
    ADAPTERS,
    ApplicationPreparationError,
    _access_challenge_detected,
    _form_dom_snapshot,
    _load_profile,
    _load_shortlisted_job,
    _option_matches,
    _private_paths,
    _reject_explicitly_ineligible,
    _write_json,
    application_answers_for,
    load_application_preferences,
    map_verified_fields,
)
from agent.settings import ROOT, profile
from agent.db import JobRow
from agent.matching import canonical


APPLICATION_STATES = (
    'Prepared',
    'Needs Review',
    'Ready for Approval',
    'Approved',
    'Submitting',
    'Submitted',
    'Submission Failed',
    'Submission Uncertain',
)


class ApplicationSubmissionError(ValueError):
    """Raised when an application cannot safely proceed to submission."""


class SubmissionUncertainError(RuntimeError):
    """Raised when a submit attempt may have reached the employer."""


class ReviewInvalidatedError(ApplicationSubmissionError):
    """Raised when the live form no longer matches the reviewed snapshot."""


@dataclass(frozen=True)
class VerifiedSubmission:
    """Proof returned only after the official employer confirmation is verified."""
    job_id: int
    application_url: str
    review_fingerprint: str
    confirmation_url: str
    confirmed_at: str


def _canonical_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), default=str).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def review_fingerprint(snapshot: dict) -> str:
    return _canonical_hash(snapshot)


def _cv_path(job_id: int, root: Path) -> Path:
    if job_id != 4:
        raise ApplicationSubmissionError('Submission is currently supported only for the Bitdefender pilot.')
    return root / 'private' / 'application_pilot' / 'job-4-bitdefender-qa-engineer' / 'cv.pdf'


def _load_current_sources(job_id: int, root: Path) -> tuple[object, object, object, object, Path, dict, str]:
    root = root.resolve()
    _reject_explicitly_ineligible(job_id, root)
    job = _load_shortlisted_job(job_id, root)
    if job.platform.casefold() != 'greenhouse':
        raise ApplicationSubmissionError('Live submission is currently supported only for the Greenhouse pilot.')
    candidate = profile() if root == ROOT else _load_profile(root)
    preferences = load_application_preferences(root)
    answers = application_answers_for(job_id, job.company, str(job.url), preferences)
    if answers is None:
        raise ApplicationSubmissionError('No vacancy-specific candidate-confirmed answers are stored for this application.')
    cv_path = _cv_path(job_id, root)
    if not cv_path.is_file():
        raise ApplicationSubmissionError('The tailored CV is missing from the private application folder.')
    mapped = map_verified_fields(candidate, preferences)
    source_data = {
        'job_id': job_id,
        'employer': job.company,
        'job_title': job.title,
        'application_url': str(job.url),
        'candidate_profile': candidate.model_dump(mode='json'),
        'mapped_fields': mapped,
        'vacancy_answers': answers.model_dump(mode='json'),
        'cv_name': cv_path.name,
        'cv_size': cv_path.stat().st_size,
        'cv_sha256': hashlib.sha256(cv_path.read_bytes()).hexdigest(),
    }
    return job, candidate, preferences, answers, cv_path, mapped, _canonical_hash(source_data)


def build_review_snapshot(job_id: int, job, mapped: dict, answers, cv_path: Path,
                          results, form_dom_snapshot: dict) -> dict:
    """Build the employer-facing values verified in the prepared Greenhouse DOM."""
    verified = {}
    for result in results:
        if result.note.startswith('Verified value: '):
            verified[result.label] = result.note.removeprefix('Verified value: ')
        elif result.label == 'Resume/CV' and result.status == 'Uploaded':
            match = re.fullmatch(r'Verified attached file: (.+) \((\d+) bytes\)', result.note)
            if match:
                verified['Resume/CV'] = match.group(1)
    required = ('First name', 'Last name', 'Email', 'Phone', 'Country',
                'Location (City)', 'Work authorization',
                'Recruitment data-processing consent', 'Salary expectations', 'Resume/CV')
    missing = [label for label in required if not verified.get(label)]
    if mapped.get('preferred_first_name') and not verified.get('Preferred First Name'):
        missing.append('Preferred First Name')
    if missing:
        raise ApplicationSubmissionError('The prepared form lacks DOM-verified fields: ' + ', '.join(missing))
    if (verified['First name'] != mapped['first_name']
            or verified['Last name'] != mapped['last_name']
            or verified['Email'] != mapped['email']
            or not _option_matches(mapped['country'], verified['Country'])):
        raise ApplicationSubmissionError('Candidate identity or residence fields differ from the verified private profile.')
    if mapped.get('preferred_first_name') and verified['Preferred First Name'] != mapped['preferred_first_name']:
        raise ApplicationSubmissionError('Preferred first name differs from the confirmed private preference.')
    expected_phone = re.sub(r'\D', '', mapped['phone'])
    actual_phone = re.sub(r'\D', '', verified['Phone'])
    if not expected_phone or expected_phone != actual_phone:
        raise ApplicationSubmissionError('The form phone number differs from the verified private profile.')
    if not answers.work_authorization or verified['Work authorization'] != answers.work_authorization:
        raise ApplicationSubmissionError('Work authorization is not an exact candidate-confirmed form answer.')
    if (not answers.recruitment_data_processing_consent
            or verified['Recruitment data-processing consent'] != answers.recruitment_data_processing_consent):
        raise ApplicationSubmissionError('Recruitment consent is not an exact candidate-confirmed form answer.')
    if not answers.location_city or not _option_matches(answers.location_city, verified['Location (City)']):
        raise ApplicationSubmissionError('Application location is not an exact candidate-confirmed location.')
    expected_salary = answers.salary_form_value or mapped.get('salary_expectation')
    if not expected_salary or verified['Salary expectations'] != expected_salary:
        raise ApplicationSubmissionError('Salary expectation differs from the candidate-confirmed amount.')
    candidate_identity = {
        'name': verified['First name'] + ' ' + verified['Last name'],
        'preferred_first_name': verified.get('Preferred First Name', ''),
        'email': verified['Email'],
        'phone': verified['Phone'],
        'current_residence': mapped['current_residence'],
    }
    return {
        'job_id': job_id,
        'employer': job.company,
        'job_title': job.title,
        'application_url': str(job.url),
        'candidate': candidate_identity,
        'form_data': {
            'country': verified['Country'],
            'location_city': verified['Location (City)'],
            'salary_expectation': verified['Salary expectations'],
            'work_authorization': verified['Work authorization'],
            'recruitment_data_processing_consent': verified['Recruitment data-processing consent'],
        },
        'attached_cv': {
            'filename': verified['Resume/CV'],
            'size_bytes': cv_path.stat().st_size,
            'sha256': hashlib.sha256(cv_path.read_bytes()).hexdigest(),
        },
        'form_fields': form_dom_snapshot['fields'],
        'form_structure': form_dom_snapshot['forms'],
    }


def _application_files(job_id: int, root: Path) -> tuple[Path, Path]:
    _, preview_path, state_path = _private_paths(root, job_id)
    return preview_path, state_path


@contextmanager
def _exclusive_approval_attempt(job_id: int, root: Path):
    """Prevent concurrent dashboard sessions from consuming one approval twice."""
    _, state_path = _application_files(job_id, root)
    lock_path = state_path.with_name(state_path.name + '.approval-lock')
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ApplicationSubmissionError(
            'An approval attempt is already active for this application; do not start another one.') from None
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as lock_file:
            lock_file.write(str(os.getpid()))
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _load_state_and_preview(job_id: int, root: Path) -> tuple[dict, dict, Path]:
    preview_path, state_path = _application_files(job_id, root)
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        preview = json.loads(preview_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        raise ApplicationSubmissionError('A private, completed application preparation preview is required.') from None
    if state.get('job_id') != job_id or preview.get('job_id') != job_id:
        raise ApplicationSubmissionError('The private application preview belongs to a different vacancy.')
    return state, preview, state_path


def load_application_review(job_id: int, root: Path = ROOT) -> tuple[dict, dict | None, str | None]:
    """Load current review data and invalidate stale or legacy approvals."""
    state, preview, state_path = _load_state_and_preview(job_id, root)
    status = state.get('status')
    if status == 'Submitting':
        state['status'] = 'Submission Uncertain'
        state['approval'] = None
        state['state_note'] = 'The previous process stopped while submitting; check the employer site before any further action.'
        _write_json(state_path, state)
        return state, None, None
    if status == 'Awaiting approval':
        # Never interpret a phase-one pilot status as approval for a real submission.
        state['status'] = 'Needs Review'
        state['approval'] = None
        state['state_note'] = 'This preparation predates the approval workflow; prepare it again before reviewing.'
        _write_json(state_path, state)
        return state, None, None
    snapshot = preview.get('review_snapshot')
    if status not in {'Ready for Approval', 'Approved'} or not isinstance(snapshot, dict):
        return state, None, None
    try:
        *_, current_source_fingerprint = _load_current_sources(job_id, root)
    except (ApplicationPreparationError, ApplicationSubmissionError):
        current_source_fingerprint = None
    saved_review_fingerprint = state.get('review_fingerprint')
    if (current_source_fingerprint is None
            or current_source_fingerprint != state.get('source_fingerprint')
            or review_fingerprint(snapshot) != saved_review_fingerprint
            or preview.get('review_fingerprint') != saved_review_fingerprint):
        state['status'] = 'Needs Review'
        state['approval'] = None
        state['state_note'] = 'The candidate data, answers, CV, URL, or review snapshot changed; prepare and review again.'
        _write_json(state_path, state)
        return state, None, None
    if state.get('candidate_input_fields'):
        state['status'] = 'Needs Review'
        _write_json(state_path, state)
        return state, None, None
    return state, snapshot, saved_review_fingerprint


def _save_state(state_path: Path, state: dict, status: str, **updates) -> dict:
    if status not in APPLICATION_STATES:
        raise ApplicationSubmissionError('Invalid application workflow state.')
    state.update(updates)
    state['status'] = status
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    _write_json(state_path, state)
    return state


def approve_and_submit(job_id: int, reviewed_fingerprint: str, *,
                       candidate_confirmed: bool, root: Path = ROOT,
                       submitter: Callable | None = None, factory=None) -> dict:
    """Consume one fresh dashboard confirmation, serialized per application."""
    with _exclusive_approval_attempt(job_id, root):
        return _approve_and_submit_locked(
            job_id, reviewed_fingerprint, candidate_confirmed=candidate_confirmed,
            root=root, submitter=submitter, factory=factory)


def _approve_and_submit_locked(job_id: int, reviewed_fingerprint: str, *,
                               candidate_confirmed: bool, root: Path,
                               submitter: Callable | None, factory=None) -> dict:
    """Consume one fresh dashboard confirmation for the exact prepared snapshot."""
    if candidate_confirmed is not True:
        raise ApplicationSubmissionError('Explicit confirmation for this exact application is required.')
    state, snapshot, current_fingerprint = load_application_review(job_id, root)
    if state.get('status') != 'Ready for Approval' or snapshot is None or current_fingerprint is None:
        raise ApplicationSubmissionError('Only a fully prepared, current application can be approved.')
    if reviewed_fingerprint != current_fingerprint:
        raise ApplicationSubmissionError('The reviewed application snapshot changed; review the current data again.')
    if snapshot.get('job_id') != job_id:
        raise ApplicationSubmissionError('The approval does not match this vacancy.')
    try:
        job, candidate, preferences, answers, cv_path, mapped, source_fingerprint = _load_current_sources(job_id, root)
    except (ApplicationPreparationError, ApplicationSubmissionError) as exc:
        state['approval'] = None
        _save_state(_application_files(job_id, root)[1], state, 'Needs Review',
                    state_note='Application data could not be revalidated before approval.')
        raise ApplicationSubmissionError('The application could not be revalidated; prepare and review it again.') from exc
    if snapshot.get('application_url') != str(job.url) or source_fingerprint != state.get('source_fingerprint'):
        state['approval'] = None
        _save_state(_application_files(job_id, root)[1], state, 'Needs Review',
                    state_note='The application URL or source data changed before approval.')
        raise ApplicationSubmissionError('The application changed before approval; prepare and review it again.')
    approval = {
        'approval_id': str(uuid.uuid4()),
        'job_id': job_id,
        'application_url': str(job.url),
        'review_fingerprint': current_fingerprint,
        'approved_at': datetime.now(timezone.utc).isoformat(),
        'fresh_candidate_confirmation': True,
    }
    _, state_path = _application_files(job_id, root)
    state = _save_state(state_path, state, 'Approved', approval=approval)
    state = _save_state(state_path, state, 'Submitting')
    runner = submitter or _submit_live_application
    try:
        outcome = runner(job_id, job, candidate, preferences, answers, cv_path,
                         mapped, current_fingerprint, root)
    except ReviewInvalidatedError as exc:
        _, state_path = _application_files(job_id, root)
        state['approval'] = None
        return _save_state(state_path, state, 'Needs Review', state_note=str(exc))
    except SubmissionUncertainError as exc:
        return _save_state(state_path, state, 'Submission Uncertain', state_note=str(exc))
    except Exception as exc:
        return _save_state(state_path, state, 'Submission Failed',
                           state_note=f'Submission did not complete before a request was dispatched ({type(exc).__name__}).')
    if (isinstance(outcome, VerifiedSubmission)
            and outcome.job_id == job_id
            and outcome.application_url == str(job.url)
            and outcome.review_fingerprint == current_fingerprint
            and _has_reliable_submission_confirmation(outcome.confirmation_url,
                                                       'Thank you for applying', False)):
        evidence = {
            'job_id': job_id,
            'application_url': str(job.url),
            'review_fingerprint': current_fingerprint,
            'confirmation_url': outcome.confirmation_url,
            'confirmed_at': outcome.confirmed_at,
            'confirmation_verified': True,
        }
        submitted = _save_state(state_path, state, 'Submitted', submitted_at=outcome.confirmed_at,
                                submission_evidence=evidence,
                                review_status_sync={'status': 'Pending', 'desired_status': 'Applied'})
        if factory is not None:
            try:
                reconcile_confirmed_submission(job_id, root, factory)
                submitted = json.loads(state_path.read_text(encoding='utf-8'))
            except Exception as exc:
                # The employer result remains Submitted; only the local sync is
                # pending. Never translate a confirmed external result to Failed.
                submitted['review_status_sync']['last_error'] = type(exc).__name__
                _write_json(state_path, submitted)
        return submitted
    return _save_state(state_path, state, 'Submission Uncertain',
                       state_note='The employer did not provide a clear submission confirmation. Check the employer site; do not retry automatically.')


def _approval_guard_script() -> str:
    """Block form submits until this exact in-memory approval is armed once."""
    return """(() => {
      window.__jobAgentApprovedSubmission = false;
      const block = e => {
        if (window.__jobAgentApprovedSubmission === true) return;
        e.preventDefault(); e.stopImmediatePropagation(); return false;
      };
      document.addEventListener('submit', block, true);
      const disable = () => document.querySelectorAll('button[type=submit],input[type=submit]')
        .forEach(el => { if (!window.__jobAgentApprovedSubmission) el.disabled = true; });
      if (document.documentElement) {
        disable();
        new MutationObserver(disable).observe(document.documentElement, {childList:true,subtree:true,attributes:true});
      } else document.addEventListener('DOMContentLoaded', disable, {once:true});
    })();"""


def _has_reliable_submission_confirmation(final_url: str, page_text: str,
                                          application_form_visible: bool) -> bool:
    """Require an official-host confirmation page with the application form gone.

    Greenhouse can respond asynchronously or show generic success wording elsewhere
    on the page. If the form remains visible, text alone is not enough evidence;
    callers keep the attempt in Submission Uncertain instead.
    """
    parsed = urlparse(final_url)
    if (parsed.scheme != 'https'
            or parsed.hostname not in {'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'}
            or application_form_visible):
        return False
    return bool(re.search(
        r'\b(thank you for applying|application has been submitted|application was submitted|your application is submitted)\b',
        page_text,
        flags=re.IGNORECASE,
    ))


def _close_browser_safely(browser) -> None:
    """Best-effort cleanup must never replace the submission outcome."""
    try:
        browser.close()
    except Exception:
        pass


def reconcile_confirmed_submission(job_id: int, root: Path = ROOT, factory=None) -> str:
    """Idempotently sync verified submission evidence to SQLite review status.

    The private Submitted record is written before SQLite. If either process stops
    between writes, this method can safely finish the review-status transition;
    it never invokes a browser or retries an employer submission.
    """
    _, state_path = _application_files(job_id, root)
    try:
        state = json.loads(state_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return 'Unavailable'
    evidence = state.get('submission_evidence')
    sync = state.get('review_status_sync')
    if (state.get('status') != 'Submitted' or not isinstance(evidence, dict)
            or evidence.get('confirmation_verified') is not True
            or evidence.get('job_id') != job_id
            or evidence.get('review_fingerprint') != state.get('review_fingerprint')
            or evidence.get('application_url') != state.get('application_url')
            or not _has_reliable_submission_confirmation(
                str(evidence.get('confirmation_url', '')), 'Thank you for applying', False)
            or not isinstance(sync, dict)
            or sync.get('desired_status') != 'Applied'
            or sync.get('status') not in {'Pending', 'Synced'}):
        return 'Not eligible'
    if sync.get('status') == 'Synced':
        return 'Synced'
    if factory is None:
        from agent.db import database
        factory = database()
    with factory.begin() as session:
        row = session.get(JobRow, job_id)
        if row is None:
            result = 'Conflict'
        elif canonical(str(evidence.get('application_url', ''))) != row.canonical_url:
            result = 'Conflict'
        elif row.status == 'Applied':
            result = 'Synced'
        elif row.status == 'Shortlisted':
            # A conditional write protects a concurrent manual decision.
            updated = session.query(JobRow).filter(
                JobRow.id == job_id, JobRow.status == 'Shortlisted').update(
                    {JobRow.status: 'Applied'}, synchronize_session=False)
            result = 'Synced' if updated == 1 else 'Conflict'
        else:
            # Preserve manual changes such as Rejected or Reviewed.
            result = 'Conflict'
    state['review_status_sync'] = {
        **sync,
        'status': result,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }
    state['state_note'] = (None if result == 'Synced' else
                           'Employer submission is confirmed, but the existing vacancy review choice was preserved; reconcile manually.')
    _write_json(state_path, state)
    return result


def reconcile_pending_submissions(root: Path = ROOT, factory=None) -> dict[str, int]:
    """Recover pending SQLite review-status updates without browser activity."""
    counts = {'Synced': 0, 'Conflict': 0, 'Unavailable': 0, 'Not eligible': 0}
    runs = Path(root) / 'private' / 'application_runs'
    if not runs.is_dir():
        return counts
    for state_path in runs.glob('job-*/application_state.json'):
        try:
            state = json.loads(state_path.read_text(encoding='utf-8'))
            job_id = state.get('job_id')
            if not isinstance(job_id, int):
                continue
            status = reconcile_confirmed_submission(job_id, root, factory)
            counts[status] = counts.get(status, 0) + 1
        except Exception:
            # Keep the dashboard available and the durable sync marker pending;
            # the next rerun can retry this local-only database reconciliation.
            counts['Unavailable'] += 1
    return counts


def _submit_live_application(job_id: int, job, candidate, preferences, answers, cv_path: Path,
                             mapped: dict, reviewed_fingerprint: str, root: Path) -> VerifiedSubmission:
    from playwright.sync_api import sync_playwright

    from agent.application_submission import build_review_snapshot, review_fingerprint
    parsed = urlparse(str(job.url))
    if (job.platform.casefold() != 'greenhouse' or parsed.scheme != 'https'
            or parsed.hostname not in {'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'}):
        raise ApplicationSubmissionError('Submission is restricted to the official Greenhouse application URL.')
    _reject_explicitly_ineligible(job_id, root)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=False)
        page = browser.new_page(accept_downloads=False)
        page.add_init_script(_approval_guard_script())
        response = page.goto(str(job.url), wait_until='domcontentloaded', timeout=60_000)
        page.wait_for_timeout(1000)
        if response is None or response.status >= 400:
            _close_browser_safely(browser)
            raise ApplicationSubmissionError('The official application page did not load; no form was submitted.')
        if urlparse(page.url).hostname not in {'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'}:
            _close_browser_safely(browser)
            raise ApplicationSubmissionError('The application redirected outside the official Greenhouse hosts.')
        body = (page.evaluate("document.body ? document.body.innerText : ''") or '').casefold()
        if _access_challenge_detected(body):
            _close_browser_safely(browser)
            raise ApplicationSubmissionError('A security or authentication challenge appeared; no application was submitted.')
        try:
            results = ADAPTERS[job.platform.casefold()].fill(page, candidate, cv_path, preferences, answers)
            if any(result.status == 'Needs candidate input' for result in results):
                raise ApplicationSubmissionError('The live form still has unanswered fields; no application was submitted.')
            form_dom_snapshot = _form_dom_snapshot(page)
            fresh_snapshot = build_review_snapshot(job_id, job, mapped, answers, cv_path, results,
                                                  form_dom_snapshot)
            if review_fingerprint(fresh_snapshot) != reviewed_fingerprint:
                _, state_path = _application_files(job_id, root)
                state = json.loads(state_path.read_text(encoding='utf-8'))
                _save_state(state_path, state, 'Needs Review', approval=None,
                            state_note='The live form values differ from the approved snapshot; no application was submitted.')
                _close_browser_safely(browser)
                raise ReviewInvalidatedError('The live form differs from the reviewed snapshot; no application was submitted.')
            submit = page.locator('button[type="submit"]')
            if submit.count() != 1 or not submit.is_visible() or submit.is_enabled():
                raise ApplicationSubmissionError('A unique Greenhouse submit button is not safely guarded; no application was submitted.')
            # The browser submit guard remains armed until all live form values and
            # the CV hash match the fresh candidate approval above.
            page.evaluate("""() => {
              window.__jobAgentApprovedSubmission = true;
              document.querySelectorAll('button[type=submit],input[type=submit]').forEach(el => {
                el.disabled = false; el.removeAttribute('aria-disabled');
              });
            }""")
            if not submit.is_enabled():
                raise ApplicationSubmissionError('The approved submit control did not become enabled; no application was submitted.')
            submit_dispatched = True
            try:
                submit.click(timeout=10_000)
            except Exception as exc:
                raise SubmissionUncertainError(
                    f'The submit action may have reached the employer ({type(exc).__name__}); check the employer site before any retry.'
                ) from None
            page.wait_for_timeout(3000)
            confirmation = page.evaluate("""() => {
              const isVisible = element => {
                const style = getComputedStyle(element);
                const rect = element.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none'
                  && rect.width > 0 && rect.height > 0;
              };
              return {
                text: document.body ? document.body.innerText : '',
                applicationFormVisible: Array.from(document.forms).some(isVisible),
              };
            }""")
            if _has_reliable_submission_confirmation(
                    page.url, confirmation.get('text', ''),
                    bool(confirmation.get('applicationFormVisible'))):
                _, preview_path, _ = _private_paths(root, job_id)
                page.screenshot(path=str(preview_path.with_name('submission_confirmation.png')), full_page=True)
                _close_browser_safely(browser)
                return VerifiedSubmission(
                    job_id=job_id,
                    application_url=str(job.url),
                    review_fingerprint=reviewed_fingerprint,
                    confirmation_url=page.url,
                    confirmed_at=datetime.now(timezone.utc).isoformat(),
                )
            raise SubmissionUncertainError('The employer page did not show a clear confirmation after submission; check the employer site and do not retry automatically.')
        except (ApplicationSubmissionError, SubmissionUncertainError):
            _close_browser_safely(browser)
            raise
        except Exception as exc:
            _close_browser_safely(browser)
            if locals().get('submit_dispatched', False):
                raise SubmissionUncertainError(
                    f'The submit action may have reached the employer ({type(exc).__name__}); check the employer site before any retry.'
                ) from None
            raise ApplicationSubmissionError(
                f'The form could not be fully verified before submission ({type(exc).__name__}); no submission was attempted.'
            ) from None

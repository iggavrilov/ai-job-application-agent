"""Private supervised application preparation for employer-hosted forms.

Preparation may fill verified fields and attach a CV, but the browser receives a
hard submit-event guard. No application status or review decision is changed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import unicodedata
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sqlalchemy.engine import make_url

from agent.models import Candidate, Job
from agent.settings import ROOT, profile


class ApplicationPreparationError(ValueError):
    """Raised when a safe, verified preparation cannot be performed."""


@dataclass(frozen=True)
class FieldResult:
    label: str
    status: str
    note: str = ''


@dataclass(frozen=True)
class PreparationResult:
    job_id: int
    employer: str
    role: str
    application_url: str
    status: str
    completed_fields: tuple[str, ...]
    candidate_input_fields: tuple[str, ...]
    preview_path: str


class VacancyApplicationAnswers(BaseModel):
    """Answers confirmed for one specific vacancy and official application URL."""

    model_config = ConfigDict(extra='forbid')

    employer: str
    application_url: str
    location_city: str | None = None
    work_authorization: Literal['Yes', 'No'] | None = None
    recruitment_data_processing_consent: Literal['Acknowledge'] | None = None
    salary_form_value: str | None = None


class ApplicationPreferences(BaseModel):
    """Candidate-confirmed employer-form preferences, loaded only from private storage."""

    model_config = ConfigDict(extra='forbid')

    first_name: str | None = None
    last_name: str | None = None
    preferred_first_name: str | None = None
    current_residence_city: str | None = None
    current_residence_country: str | None = None
    preferred_job_location_romania: str | None = None
    preferred_job_location_moldova: str | None = None
    salary_expectation: str | None = None
    salary_expectation_ron: str | None = None
    salary_conversion_rate: str | None = None
    salary_conversion_date: str | None = None
    salary_conversion_source: str | None = None
    vacancy_answers: dict[str, VacancyApplicationAnswers] = Field(default_factory=dict)


REVIEW_FIELDS = (
    ('Are you legally authorized to work in the country where the role is based at?',
     'Requires the candidate to answer explicitly; do not infer remote or country eligibility.'),
    ('Recruitment data-processing consent',
     'Requires the candidate to review and decide consent.'),
)


def _db_path(root: Path) -> Path:
    url = make_url(os.getenv('DATABASE_URL', 'sqlite:///' + str(root / 'data' / 'jobs.db')))
    if url.get_backend_name() != 'sqlite' or not url.database:
        raise ApplicationPreparationError('Supervised preparation requires the local SQLite database.')
    path = Path(url.database)
    return path if path.is_absolute() else root / path


def _load_shortlisted_job(job_id: int, root: Path) -> Job:
    database_path = _db_path(root)
    uri = 'file:' + quote(database_path.as_posix(), safe=':/') + '?mode=ro'
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute('SELECT status, payload FROM jobs WHERE id=?', (job_id,)).fetchone()
    except sqlite3.Error:
        raise ApplicationPreparationError('Cannot read the local vacancy database.') from None
    if row is None:
        raise ApplicationPreparationError(f'Vacancy ID {job_id} was not found.')
    status, payload = row
    if status != 'Shortlisted':
        raise ApplicationPreparationError('Application preparation is available only for Shortlisted vacancies.')
    try:
        job = Job.model_validate_json(payload)
    except Exception:
        raise ApplicationPreparationError('The stored vacancy record is invalid.') from None
    if job.platform.casefold() != 'greenhouse':
        raise ApplicationPreparationError('Only Greenhouse applications are supported in this phase.')
    parsed = urlparse(str(job.url))
    if parsed.scheme != 'https' or parsed.hostname not in {'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'}:
        raise ApplicationPreparationError('The vacancy URL is outside the supported official Greenhouse hosts.')
    return job


def _reject_explicitly_ineligible(job_id: int, root: Path) -> None:
    """Allow Needs Review preparation, but never launch an explicitly ineligible role."""
    database_path = _db_path(root)
    uri = 'file:' + quote(database_path.as_posix(), safe=':/') + '?mode=ro'
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            row = connection.execute(
                'SELECT payload FROM job_matches WHERE job_id=? ORDER BY id DESC LIMIT 1',
                (job_id,)).fetchone()
    except sqlite3.Error:
        return  # Old databases may not have a match snapshot; retain unknown as Needs Review.
    if row:
        try:
            eligibility = json.loads(row[0]).get('eligibility')
        except (json.JSONDecodeError, AttributeError):
            eligibility = None
        if eligibility == 'Ineligible':
            raise ApplicationPreparationError('Application preparation is disabled for an explicitly ineligible vacancy.')


def _name_parts(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    if len(parts) < 2:
        raise ApplicationPreparationError('The verified profile must contain first and last name parts for this form.')
    return parts[0], ' '.join(parts[1:])


def _city_country(location: str) -> tuple[str, str]:
    parts = [part.strip() for part in location.split(',') if part.strip()]
    if len(parts) < 2:
        raise ApplicationPreparationError('The verified profile location must identify both city and country.')
    return parts[0], parts[-1]


def load_application_preferences(root: Path = ROOT) -> ApplicationPreferences:
    """Load explicit field preferences from the Git-ignored private directory."""
    path = root / 'private' / 'application_preferences.json'
    if not path.exists():
        return ApplicationPreferences()
    try:
        return ApplicationPreferences.model_validate_json(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValidationError):
        raise ApplicationPreparationError(
            'Private application preferences are unreadable or invalid; review application_preferences.json.'
        ) from None


def application_answers_for(job_id: int, employer: str, application_url: str,
                            preferences: ApplicationPreferences) -> VacancyApplicationAnswers | None:
    """Return answers only when vacancy, employer, and URL all match the private record."""
    answers = preferences.vacancy_answers.get(str(job_id))
    if answers is None:
        return None
    if answers.employer.casefold() != employer.casefold() or answers.application_url != application_url:
        raise ApplicationPreparationError('Private application answers do not match this vacancy and application URL.')
    return answers


def map_verified_fields(candidate: Candidate,
                        preferences: ApplicationPreferences | None = None) -> dict[str, str]:
    """Map candidate facts and confirmed preferences; legal answers remain excluded."""
    preferences = preferences or ApplicationPreferences()
    candidate_first, candidate_last = _name_parts(candidate.name)
    fallback_city, fallback_country = _city_country(candidate.location)
    first = (preferences.first_name or candidate_first).strip()
    last = (preferences.last_name or candidate_last).strip()
    city = (preferences.current_residence_city or fallback_city).strip()
    country = (preferences.current_residence_country or fallback_country).strip()
    required = {'name': first + ' ' + last, 'email': candidate.email,
                'telephone': candidate.telephone, 'city': city, 'country': country}
    if any(not value.strip() or value == 'unknown' for value in required.values()):
        raise ApplicationPreparationError('The private profile or preferences are missing a verified name, email, phone, or residence.')
    return {
        'first_name': first,
        'last_name': last,
        'preferred_first_name': (preferences.preferred_first_name or '').strip(),
        'email': candidate.email.strip(),
        'country': country,
        'phone': candidate.telephone.strip(),
        'current_residence_city': city,
        'current_residence': f'{city}, {country}',
        'preferred_job_location_romania': (preferences.preferred_job_location_romania or '').strip(),
        'preferred_job_location_moldova': (preferences.preferred_job_location_moldova or '').strip(),
        'salary_expectation': (preferences.salary_expectation or '').strip(),
    }


def location_value_for_label(label: str, mapped: dict[str, str]) -> tuple[str | None, str | None]:
    """Resolve location only when a form label clearly states whose location it requests."""
    normalized = ' '.join(label.casefold().replace('*', '').split())
    if 'preferred' in normalized and 'location' in normalized:
        if 'romania' in normalized:
            value = mapped.get('preferred_job_location_romania', '')
            return (value or None, None if value else 'Romania job-location preference is not configured.')
        if 'moldova' in normalized:
            value = mapped.get('preferred_job_location_moldova', '')
            return (value or None, None if value else 'Moldova job-location preference is not configured.')
        return None, 'The preferred job-location country is not specified.'
    if any(term in normalized for term in ('current city', 'residential city')):
        return mapped.get('current_residence_city') or None, None
    if any(term in normalized for term in ('current residence', 'residential address', 'current location')):
        return mapped.get('current_residence') or None, None
    if 'location' in normalized or normalized == 'city':
        return None, 'The generic location field is ambiguous; confirm whether it asks for residence or preferred job location.'
    return None, None


def _private_paths(root: Path, job_id: int) -> tuple[Path, Path, Path]:
    directory = root / 'private' / 'application_runs' / f'job-{job_id}'
    return directory, directory / 'application_preview.json', directory / 'application_state.json'


def _write_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _page_submit_guard_script() -> str:
    # Keep the final action unavailable even if employer markup changes after load.
    return """(() => {
      const block = e => { e.preventDefault(); e.stopImmediatePropagation(); return false; };
      document.addEventListener('submit', block, true);
      const disable = () => document.querySelectorAll('button[type=submit],input[type=submit]')
        .forEach(el => {
          if (!el.disabled) el.disabled = true;
          if (el.getAttribute('aria-disabled') !== 'true') el.setAttribute('aria-disabled','true');
        });
      const observe = () => {
        disable();
        new MutationObserver(disable).observe(document.documentElement, {childList:true,subtree:true,attributes:true});
      };
      if (document.documentElement) observe();
      else document.addEventListener('DOMContentLoaded', observe, {once:true});
    })();"""


def _fill_verified_text(target, locator_description: str, value: str) -> str:
    if target.count() != 1 or not target.is_visible():
        raise ApplicationPreparationError('A supported Greenhouse field is missing or ambiguous: ' + locator_description)
    fill_supported_control(target, 'text', value)
    try:
        actual = target.input_value()
        input_type = (target.get_attribute('type') or '').casefold()
    except Exception:
        raise ApplicationPreparationError('A supported Greenhouse text field could not be verified in the page DOM.') from None
    matches = actual == value
    if not matches and input_type == 'tel':
        expected_digits = re.sub(r'\D', '', value)
        actual_digits = re.sub(r'\D', '', actual)
        matches = bool(expected_digits) and expected_digits == actual_digits
    if not matches:
        raise ApplicationPreparationError('A supported Greenhouse text field did not retain its verified value (' + locator_description + ').')
    return actual


def _fill_label(page, locator: str, value: str) -> str:
    return _fill_verified_text(page.locator(locator), locator, value)


def _fill_location_fields(page, mapped: dict[str, str],
                         location_override: str | None = None) -> tuple[FieldResult, ...]:
    """Fill only location controls whose labels state residence or preferred country."""
    labels = page.locator('label')
    results: list[FieldResult] = []
    for index in range(labels.count()):
        label = labels.nth(index)
        label_text = ' '.join(label.inner_text().split())
        value, note = location_value_for_label(label_text, mapped)
        if value is None and note is None:
            continue
        if value is None and location_override and note and 'generic location field is ambiguous' in note:
            value, note = location_override, None
        display_label = label_text.rstrip('*') or 'Location field'
        field_id = label.get_attribute('for')
        if not field_id:
            results.append(FieldResult(display_label, 'Needs candidate input',
                                       note or 'The location field has no uniquely associated control.'))
            continue
        safe_id = field_id.replace('\\', '\\\\').replace('"', '\\"')
        selector = f'[id="{safe_id}"]'
        target = page.locator(selector)
        if value is None:
            results.append(FieldResult(display_label, 'Needs candidate input', note or 'Location meaning must be confirmed.'))
            continue
        try:
            kind = target.evaluate('(el) => ({tag:el.tagName.toLowerCase(), type:(el.getAttribute("type") || "").toLowerCase(), role:(el.getAttribute("role") || "")})')
        except Exception:
            kind = {}
        if kind.get('tag') not in {'input', 'textarea'} or (kind.get('tag') == 'input' and kind.get('type') not in {'', 'text'}):
            results.append(FieldResult(display_label, 'Needs candidate input',
                                       'The location control type is not supported for safe automatic entry.'))
            continue
        if kind.get('role') == 'combobox':
            verified_value = _select_react_option(page, selector, value)
        else:
            verified_value = _fill_verified_text(target, selector, value)
        results.append(FieldResult(display_label, 'Completed', 'Verified value: ' + verified_value))
    return tuple(results)


def _fold_option_text(value: str) -> str:
    decomposed = unicodedata.normalize('NFKD', value.casefold())
    return ''.join(ch for ch in decomposed if not unicodedata.combining(ch))


def _option_aliases(value: str) -> tuple[str, ...]:
    aliases = [value]
    if '(' in value and ')' in value:
        before, rest = value.split('(', 1)
        aliases.extend([before.strip(), rest.split(')', 1)[0].strip()])
    return tuple(alias for alias in aliases if alias)


def _option_matches(expected: str, offered: str) -> bool:
    folded_offer = _fold_option_text(offered)
    return any(_fold_option_text(alias) in folded_offer for alias in _option_aliases(expected))


def _select_react_option(page, selector: str, value: str) -> str:
    field = page.locator(selector)
    if field.count() != 1 or not field.is_visible():
        raise ApplicationPreparationError('A supported Greenhouse choice field is missing or ambiguous: ' + selector)
    field.locator('xpath=../../..').click()
    field.press_sequentially(value.split(' (', 1)[0])
    option_texts = page.get_by_role('option').all_inner_texts()
    matches = [text for text in option_texts if _option_matches(value, text)]
    if len(matches) != 1:
        raise ApplicationPreparationError('A supported Greenhouse choice could not be selected uniquely (' + selector + ').')
    option = page.get_by_role('option', name=matches[0], exact=True)
    if option.count() != 1 or not option.is_visible():
        raise ApplicationPreparationError('A supported Greenhouse choice could not be selected safely (' + selector + ').')
    option.click()
    selected = field.locator('xpath=../../..').locator('.select__single-value')
    if selected.count() != 1:
        raise ApplicationPreparationError('A selected Greenhouse choice could not be read back (' + selector + ').')
    actual = ' '.join(selected.inner_text().split())
    if not _option_matches(value, actual):
        raise ApplicationPreparationError('A Greenhouse choice did not retain its confirmed value (' + selector + ').')
    return actual


def _select_country(page, country: str) -> str:
    return _select_react_option(page, '#country', country)


def _fill_form(page, candidate: Candidate, cv_path: Path,
               preferences: ApplicationPreferences | None = None,
               answers: VacancyApplicationAnswers | None = None) -> tuple[FieldResult, ...]:
    values = map_verified_fields(candidate, preferences)
    mappings = (
        ('First name', '#first_name', values['first_name']),
        ('Last name', '#last_name', values['last_name']),
        ('Preferred First Name', '#preferred_name', values['preferred_first_name']),
        ('Email', '#email', values['email']),
    )
    completed: list[FieldResult] = []
    for label, selector, value in mappings:
        if label == 'Preferred First Name' and not value:
            continue
        _fill_label(page, selector, value)
        completed.append(FieldResult(label, 'Completed'))
    country_value = _select_country(page, values['country'])
    completed.append(FieldResult('Country', 'Completed', 'Verified value: ' + country_value))
    phone_value = _fill_label(page, '#phone', values['phone'])
    completed.append(FieldResult('Phone', 'Completed', 'Verified phone digits in live DOM'))

    completed.extend(_fill_location_fields(
        page, values, answers.location_city if answers else None))

    if answers and answers.work_authorization:
        verified = _select_react_option(page, '#question_9299067101', answers.work_authorization)
        completed.append(FieldResult('Work authorization', 'Completed', 'Verified value: ' + verified))
    if answers and answers.recruitment_data_processing_consent:
        verified = _select_react_option(page, '#question_9299069101',
                                        answers.recruitment_data_processing_consent)
        completed.append(FieldResult('Recruitment data-processing consent', 'Completed',
                                     'Verified value: ' + verified))

    salary = ((answers.salary_form_value if answers else None) or values['salary_expectation'])
    if salary:
        verified = _fill_label(page, '#question_9299068101', salary)
        completed.append(FieldResult('Salary expectations', 'Completed', 'Verified value: ' + verified))

    resume = page.locator('input[type="file"]#resume')
    if not cv_path.is_file() or resume.count() != 1:
        raise ApplicationPreparationError('The private tailored PDF or Greenhouse resume upload control is missing.')
    fill_supported_control(resume, 'file', cv_path)
    completed.append(FieldResult('Resume/CV', 'Uploaded'))
    return tuple(completed)


def fill_supported_control(locator, control_type: str, value: str | bool | Path) -> None:
    """Fill one explicitly mapped control type; never derive values from page prompts."""
    kind = control_type.casefold()
    if kind == 'text' and isinstance(value, str):
        locator.fill(value)
    elif kind == 'dropdown' and isinstance(value, str):
        locator.select_option(label=value)
    elif kind == 'radio' and isinstance(value, str):
        locator.check()
    elif kind == 'checkbox' and isinstance(value, bool):
        locator.check() if value else locator.uncheck()
    elif kind == 'file' and isinstance(value, Path):
        locator.set_input_files(str(value.resolve()))
    else:
        raise ApplicationPreparationError('Unsupported or mismatched application field control mapping.')


class ApplicationFormAdapter(Protocol):
    platform: str

    def fill(self, page, candidate: Candidate, cv_path: Path,
             preferences: ApplicationPreferences | None = None,
             answers: VacancyApplicationAnswers | None = None) -> tuple[FieldResult, ...]: ...


class GreenhouseAdapter:
    """Greenhouse implementation; Lever can be added without changing orchestration."""

    platform = 'greenhouse'

    def fill(self, page, candidate: Candidate, cv_path: Path,
             preferences: ApplicationPreferences | None = None,
             answers: VacancyApplicationAnswers | None = None) -> tuple[FieldResult, ...]:
        return _fill_form(page, candidate, cv_path, preferences, answers)


ADAPTERS: dict[str, ApplicationFormAdapter] = {'greenhouse': GreenhouseAdapter()}


def prepare_greenhouse(job_id: int, *, root: Path = ROOT, headed: bool = True,
                       hold_browser: bool = True) -> PreparationResult:
    """Fill supported verified fields, attach CV, and stop at the submit boundary."""
    root = root.resolve()
    job = _load_shortlisted_job(job_id, root)
    _reject_explicitly_ineligible(job_id, root)
    candidate = profile() if root == ROOT else _load_profile(root)
    preferences = load_application_preferences(root)
    cv_path = root / 'private' / 'application_pilot' / f'job-{job_id}-bitdefender-qa-engineer' / 'cv.pdf'
    # Job 4 is the only supported pilot, and its database URL is checked against metadata below.
    if job_id != 4 or job.company.casefold() != 'bitdefender':
        raise ApplicationPreparationError('The supervised live pilot currently supports Bitdefender vacancy ID 4 only.')
    metadata_path = cv_path.with_name('application_metadata.json')
    try:
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        raise ApplicationPreparationError('Private application metadata is missing or invalid.') from None
    if metadata.get('job_id') != job_id or metadata.get('application_url') != str(job.url):
        raise ApplicationPreparationError('The private application metadata does not match the selected vacancy URL.')
    application_answers = application_answers_for(job_id, job.company, str(job.url), preferences)
    directory, preview_path, state_path = _private_paths(root, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    preview_image = directory / 'application_preview.png'
    fields_for_review = [
        FieldResult(label, 'Needs candidate input', reason)
        for label, reason in REVIEW_FIELDS
        if not application_answers or (
            (label.startswith('Are you legally') and not application_answers.work_authorization)
            or (label == 'Recruitment data-processing consent'
                and not application_answers.recruitment_data_processing_consent))
    ]
    _write_json(state_path, {'status': 'Starting', 'job_id': job_id, 'completed_fields': [],
                             'candidate_input_fields': [asdict(item) for item in fields_for_review],
                             'application_url': str(job.url), 'preview_path': str(preview_path)})
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ApplicationPreparationError('Playwright is not installed. Install project requirements before launching.') from None

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=not headed)
        page = browser.new_page(accept_downloads=False)
        page.add_init_script(_page_submit_guard_script())
        response = page.goto(str(job.url), wait_until='domcontentloaded', timeout=60_000)
        page.wait_for_timeout(1000)
        if response is None or response.status >= 400:
            browser.close()
            raise ApplicationPreparationError('The official Greenhouse page did not load successfully.')
        if urlparse(page.url).hostname not in {'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'}:
            browser.close()
            raise ApplicationPreparationError('The application page redirected outside the supported Greenhouse hosts.')
        body = (page.evaluate("document.body ? document.body.innerText : ''") or '').casefold()
        if not body.strip():
            browser.close()
            raise ApplicationPreparationError('The official page loaded without readable form content; preparation stopped.')
        if any(term in body for term in ('captcha', 'verify you are human', 'access denied', 'security check')):
            browser.close()
            raise ApplicationPreparationError('A security challenge is visible. Preparation stopped; complete it manually if appropriate.')
        try:
            adapter = ADAPTERS[job.platform.casefold()]
            results = adapter.fill(page, candidate, cv_path, preferences, application_answers)
            completed = tuple(item for item in results if item.status in {'Completed', 'Uploaded'})
            location_reviews = tuple(item for item in results if item.status == 'Needs candidate input')
        except Exception:
            # Never leave a partially filled session marked ready for review as complete.
            browser.close()
            raise
        page.wait_for_timeout(1500)
        # Ensure submit remains blocked after site-side DOM changes and confirm the button state.
        page.evaluate(_page_submit_guard_script())
        page.screenshot(path=str(preview_image), full_page=True)
        result = PreparationResult(job_id=job_id, employer=job.company, role=job.title,
            application_url=str(job.url), status='Awaiting approval',
            completed_fields=tuple(item.label for item in completed),
            candidate_input_fields=tuple(item.label for item in (*location_reviews, *fields_for_review)),
            preview_path=str(preview_path))
        _write_json(preview_path, {
            'job_id': job_id, 'employer': job.company, 'role': job.title,
            'application_url': str(job.url), 'status': result.status,
            'completed_fields': [asdict(item) for item in completed],
            'candidate_input_fields': [asdict(item) for item in (*location_reviews, *fields_for_review)],
            'screenshot_path': str(preview_image),
            'final_submission': 'Blocked; no application was submitted.',
        })
        _write_json(state_path, {'status': result.status, 'job_id': job_id,
            'completed_fields': list(result.completed_fields),
            'candidate_input_fields': [asdict(item) for item in (*location_reviews, *fields_for_review)],
            'application_url': str(job.url), 'preview_path': str(preview_path),
            'browser_open': bool(hold_browser)})
        if hold_browser:
            while not page.is_closed():
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    break
        browser.close()
        _write_json(state_path, {'status': 'Awaiting approval', 'job_id': job_id,
            'completed_fields': list(result.completed_fields),
            'candidate_input_fields': [asdict(item) for item in (*location_reviews, *fields_for_review)],
            'application_url': str(job.url), 'preview_path': str(preview_path),
            'browser_open': False})
        return result


def _load_profile(root: Path) -> Candidate:
    from pydantic import ValidationError
    path = root / 'candidate_profile.json'
    try:
        return Candidate.model_validate_json(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValidationError):
        raise ApplicationPreparationError('The private candidate profile is missing, unreadable, or invalid.') from None


def _launch_from_dashboard(job_id: int, root: Path = ROOT) -> None:
    job = _load_shortlisted_job(job_id, root)
    _reject_explicitly_ineligible(job_id, root)
    if job_id != 4 or job.company.casefold() != 'bitdefender':
        raise ApplicationPreparationError('Supervised preparation currently supports Bitdefender vacancy ID 4 only.')
    directory, preview_path, state_path = _private_paths(root, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    _write_json(state_path, {'status': 'Launching browser', 'job_id': job_id,
        'completed_fields': [], 'candidate_input_fields': [
            {'label': label, 'status': 'Needs candidate input', 'note': note}
            for label, note in REVIEW_FIELDS],
        'application_url': str(job.url), 'preview_path': str(preview_path), 'browser_open': True})
    subprocess.Popen([sys.executable, '-m', 'agent.application_workflow', '--job-id', str(job_id), '--headed'],
                     cwd=str(root), close_fds=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='Prepare a Shortlisted Greenhouse application for supervised review.')
    parser.add_argument('--job-id', type=int, required=True)
    parser.add_argument('--headed', action='store_true', help='Open the employer form in a visible Edge window (default).')
    parser.add_argument('--headless', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = prepare_greenhouse(args.job_id, headed=not args.headless, hold_browser=True)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    except Exception as exc:
        _, _, state_path = _private_paths(ROOT, args.job_id)
        if state_path.parent.is_dir():
            _write_json(state_path, {'status': 'Needs attention', 'job_id': args.job_id,
                'completed_fields': [], 'candidate_input_fields': [
                    {'label': label, 'status': 'Needs candidate input', 'note': note}
                    for label, note in REVIEW_FIELDS], 'browser_open': False,
                'error_type': type(exc).__name__})
        message = str(exc) if isinstance(exc, (ApplicationPreparationError, OSError)) else (
            f'Unexpected preparation error ({type(exc).__name__}); inspect this local terminal.')
        parser.error(message)


if __name__ == '__main__':
    main()

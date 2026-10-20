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
import httpx
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import quote, urlparse, urljoin

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


class GeographicSearchPreferences(BaseModel):
    """Private candidate-approved search priorities, separate from vacancy answers."""

    model_config = ConfigDict(extra='forbid')

    preferred_work_arrangement: Literal['Remote while residing in current country'] = 'Remote while residing in current country'
    willing_to_relocate: bool = False
    relocation_scope: list[str] = Field(default_factory=list)
    relocation_only_for_attractive_offer: bool = True
    eu_work_authorization_confirmed: bool = False
    us_work_authorization: Literal['Unknown', 'Confirmed', 'Not authorized'] = 'Unknown'
    priority_order: list[Literal[
        'confirmed_remote_from_moldova',
        'international_remote_moldova_unconfirmed',
        'moldova_based',
        'remote_requiring_romania_or_eu_residence',
        'eu_hybrid_or_onsite',
        'us_international_remote_or_b2b',
    ]] = Field(default_factory=list)


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
    geographic_search: GeographicSearchPreferences | None = None
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
    platform = job.platform.casefold()
    parsed = urlparse(str(job.url))
    allowed_hosts = ({'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'} if platform == 'greenhouse'
                     else {'jobs.lever.co'} if platform == 'lever' else set())
    if parsed.scheme != 'https' or parsed.hostname not in allowed_hosts:
        raise ApplicationPreparationError('The vacancy URL is outside the supported official Greenhouse/Lever hosts.')
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
            payload = json.loads(row[0])
            geographic = payload.get('geographic_assessment') or {}
            eligibility = geographic.get('classification')
            if not eligibility:
                eligibility = 'Confirmed Ineligible' if payload.get('eligibility') == 'Ineligible' else None
        except (json.JSONDecodeError, AttributeError):
            eligibility = None
        if eligibility == 'Confirmed Ineligible':
            raise ApplicationPreparationError('Application preparation is disabled for an explicitly ineligible vacancy.')


def _access_challenge_detected(page_text: str) -> bool:
    """Stop on bot protection or an authentication gate; never work around it."""
    text = page_text.casefold()
    return any(phrase in text for phrase in (
        'captcha', 'verify you are human', 'access denied', 'security check',
        'sign in to continue', 'log in to continue', 'authentication required',
        'login required', 'verify your identity', 'multi-factor authentication',
    ))


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
        value = mapped.get('current_residence_city') or None
        return value, None if value else 'Verified current city is unavailable.'
    if any(term in normalized for term in ('current residence', 'residential address', 'current location')):
        value = mapped.get('current_residence') or None
        return value, None if value else 'Verified current residence is unavailable.'
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


def _route_application_request(route, navigation_hosts: set[str]) -> None:
    """Block LinkedIn requests entirely and navigation away from the approved ATS."""
    request = route.request
    host = (urlparse(request.url).hostname or '').casefold()
    if host == 'linkedin.com' or host.endswith('.linkedin.com'):
        route.abort()
    elif request.is_navigation_request() and host not in navigation_hosts:
        route.abort()
    else:
        route.continue_()


def _form_dom_snapshot(page) -> dict:
    """Read visible form structure and values without collecting hidden tokens."""
    try:
        snapshot = page.evaluate("""() => {
          const formDetails = Array.from(document.forms).map(form => ({
            action: new URL(form.action || location.href, location.href).href,
            method: (form.method || 'get').toUpperCase(),
            submitControls: Array.from(form.querySelectorAll('button[type="submit"],input[type="submit"]'))
              .map(el => ({tag:el.tagName.toLowerCase(),type:el.type || '',label:(el.innerText || el.value || '').trim()})),
          }));
          const fields = Array.from(document.querySelectorAll('form input, form textarea, form select'))
            .filter(el => el.type !== 'hidden' && !el.disabled)
            .map(el => {
              const label = Array.from(el.labels || []).map(item => (item.innerText || '').trim()).filter(Boolean).join(' / ')
                || el.getAttribute('aria-label') || el.name || el.id || 'Unlabeled field';
              let value;
              if (el.type === 'file') value = Array.from(el.files || []).map(file => ({name:file.name,size:file.size}));
              else if (el.type === 'checkbox' || el.type === 'radio') value = {checked:!!el.checked,value:el.value || ''};
              else if (el.tagName === 'SELECT') value = Array.from(el.selectedOptions).map(option => option.text.trim());
              else if (el.getAttribute('role') === 'combobox') value = el.closest('.select__container')?.querySelector('.select__single-value')?.innerText.trim() || el.value || '';
              else value = el.value || '';
              return {
                tag:el.tagName.toLowerCase(), type:(el.type || '').toLowerCase(), id:el.id || '',
                name:el.name || '', label, required:!!el.required || el.getAttribute('aria-required') === 'true', value,
              };
            });
          return {forms:formDetails, fields};
        }""")
    except Exception:
        raise ApplicationPreparationError('The Greenhouse form structure and current values could not be verified.') from None
    if not snapshot or not snapshot.get('forms') or not snapshot.get('fields'):
        raise ApplicationPreparationError('The official Greenhouse form could not be captured for exact review.')
    return snapshot


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
    try:
        page.get_by_role('option').wait_for(state='visible', timeout=5000)
    except Exception:
        # Continue to the unique-match check below; a missing suggestion remains
        # a safe failure and is never replaced with guessed text.
        pass
    option_texts = page.get_by_role('option').all_inner_texts()
    matches = [text for text in option_texts if _option_matches(value, text)]
    if len(matches) > 1:
        # Prefer a uniquely identifying alternate label, such as the English
        # alias in "București (Bucharest)", when the local city name also
        # occurs in smaller places returned by Greenhouse.
        for alias in _option_aliases(value):
            alias_folded = _fold_option_text(alias)
            narrowed = [text for text in option_texts if alias_folded in _fold_option_text(text)]
            if len(narrowed) == 1:
                matches = narrowed
                break
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
    selected_matches = _option_matches(value, actual)
    if (not selected_matches and selector == '#country'
            and re.fullmatch(r'\+\d{1,4}', actual)
            and _fold_option_text(actual) in _fold_option_text(matches[0])):
        # Greenhouse's phone-country control displays the dial code after selecting
        # an option such as "Moldova +373", not the country name itself.
        selected_matches = True
    if not selected_matches:
        raise ApplicationPreparationError('A Greenhouse choice did not retain its confirmed value (' + selector + ').')
    return matches[0] if selector == '#country' else actual


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
        verified = _fill_label(page, selector, value)
        completed.append(FieldResult(label, 'Completed', 'Verified value: ' + verified))
    country_value = _select_country(page, values['country'])
    completed.append(FieldResult('Country', 'Completed', 'Verified value: ' + country_value))
    phone_value = _fill_label(page, '#phone', values['phone'])
    completed.append(FieldResult('Phone', 'Completed', 'Verified value: ' + phone_value))

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
    try:
        attached = resume.evaluate('(el) => Array.from(el.files || []).map(file => ({name:file.name,size:file.size}))')
    except Exception:
        raise ApplicationPreparationError('The Greenhouse CV attachment could not be verified in the live DOM.') from None
    if (len(attached) != 1 or attached[0].get('name') != cv_path.name
            or attached[0].get('size') != cv_path.stat().st_size):
        raise ApplicationPreparationError('The selected CV is not attached to the Greenhouse form.')
    completed.append(FieldResult('Resume/CV', 'Uploaded',
                                 f"Verified attached file: {attached[0]['name']} ({attached[0]['size']} bytes)"))
    try:
        unfilled_required = page.evaluate("""() => Array.from(document.querySelectorAll('form [required], form [aria-required="true"]'))
          .filter(el => {
            if (el.type === 'hidden' || el.disabled) return false;
            if (el.type === 'radio') return !document.querySelector(`form input[type="radio"][name="${CSS.escape(el.name)}"]:checked`);
            if (el.type === 'checkbox') return !el.checked;
            if (el.type === 'file') return !(el.files && el.files.length);
            if (el.getAttribute('role') === 'combobox') return !el.closest('.select__container')?.querySelector('.select__single-value');
            return !String(el.value || '').trim();
          })
          .map(el => {
            const label = el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            return (label?.innerText || el.getAttribute('aria-label') || el.name || el.id || 'Unlabeled required field').trim();
          });""")
    except Exception:
        raise ApplicationPreparationError('Required Greenhouse fields could not be verified in the live DOM.') from None
    known_labels = {item.label.casefold() for item in completed}
    for label in unfilled_required:
        if label.casefold() not in known_labels:
            completed.append(FieldResult(label, 'Needs candidate input',
                                         'Required field remains empty or is not supported by the verified field mapping.'))
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
    """Greenhouse implementation with vacancy-specific confirmed answers."""

    platform = 'greenhouse'

    def fill(self, page, candidate: Candidate, cv_path: Path,
             preferences: ApplicationPreferences | None = None,
             answers: VacancyApplicationAnswers | None = None) -> tuple[FieldResult, ...]:
        return _fill_form(page, candidate, cv_path, preferences, answers)


class LeverAdapter:
    """Fill only uniquely labeled identity fields and an unambiguous resume upload."""

    platform = 'lever'

    def fill(self, page, candidate: Candidate, cv_path: Path,
             preferences: ApplicationPreferences | None = None,
             answers: VacancyApplicationAnswers | None = None) -> tuple[FieldResult, ...]:
        preferences = preferences or ApplicationPreferences()
        parts = _name_parts(candidate.name)
        try:
            mapped = map_verified_fields(candidate, preferences)
        except ApplicationPreparationError as exc:
            if 'location must identify both city and country' not in str(exc):
                raise
            # Lever identity fields remain useful when the profile contains only
            # a country-level location. Never invent a city to satisfy mapping.
            city = (preferences.current_residence_city or '').strip()
            country = (preferences.current_residence_country or '').strip()
            if not country and ',' in candidate.location:
                _, country = _city_country(candidate.location)
            mapped = {
                'current_residence_city': city,
                'current_residence': f'{city}, {country}' if city and country else '',
                'country': country,
                'preferred_job_location_romania': (preferences.preferred_job_location_romania or '').strip(),
                'preferred_job_location_moldova': (preferences.preferred_job_location_moldova or '').strip(),
            }
        verified = {
            'name': candidate.name,
            'full name': candidate.name,
            'first name': parts[0],
            'last name': parts[1],
            'preferred first name': (preferences.preferred_first_name or '').strip(),
            'email': candidate.email if candidate.email != 'unknown' else '',
            'phone': candidate.telephone if candidate.telephone != 'unknown' else '',
            'phone number': candidate.telephone if candidate.telephone != 'unknown' else '',
        }
        results=[]
        labels=page.locator('label')
        for index in range(labels.count()):
            label=labels.nth(index)
            label_text=' '.join(label.inner_text().split()).strip().rstrip('*').strip()
            key=label_text.casefold()
            value=verified.get(key)
            location_note = None
            if key not in verified or not value:
                value, location_note = location_value_for_label(label_text, mapped)
                normalized = ' '.join(key.split())
                if value is None and location_note is None and (
                    normalized in {'current country', 'country of residence', 'residential country'}
                    or ('country' in normalized and any(token in normalized for token in ('current', 'residen')))
                ):
                    value = mapped.get('country') or None
                    location_note = None if value else 'Verified current country is unavailable.'
                if value is None and location_note is None:
                    # Unsupported optional fields are skipped; the later required-field
                    # scan will flag them without attempting a guess.
                    continue
                if value is None:
                    results.append(FieldResult(label_text, 'Needs candidate input',
                        location_note or 'Location meaning is unclear; no value was entered.'))
                    continue
            locator=page.get_by_label(label_text, exact=True)
            if locator.count()!=1 or not locator.is_visible():
                results.append(FieldResult(label_text,'Needs candidate input','Field is missing or ambiguous; no value was entered.'))
                continue
            actual=_fill_verified_text(locator,'Lever '+label_text,value)
            results.append(FieldResult(label_text,'Completed','Verified value: '+actual))
        file_inputs=page.locator('input[type="file"]')
        file_count=file_inputs.count()
        resume_matches=[]
        for index in range(file_count):
            control=file_inputs.nth(index)
            metadata=' '.join(str(control.get_attribute(name) or '') for name in ('name','id','aria-label','accept')).casefold()
            label_metadata=control.evaluate("el => Array.from(el.labels || []).map(x => x.innerText || '').join(' ').toLowerCase()")
            metadata+=' '+str(label_metadata or '')
            if re.search(r'\b(resume|cv|curriculum vitae)\b',metadata):
                resume_matches.append(control)
        chosen=resume_matches[0] if len(resume_matches)==1 else file_inputs.nth(0) if file_count==1 else None
        if chosen is not None and cv_path.is_file():
            fill_supported_control(chosen,'file',cv_path)
            attached=chosen.evaluate('(el) => Array.from(el.files || []).map(file => ({name:file.name,size:file.size}))')
            if len(attached)!=1 or attached[0].get('name')!=cv_path.name or attached[0].get('size')!=cv_path.stat().st_size:
                raise ApplicationPreparationError('The Lever CV attachment could not be verified in the live DOM.')
            results.append(FieldResult('Resume/CV','Uploaded',f"Verified attached file: {attached[0]['name']} ({attached[0]['size']} bytes)"))
        elif file_count:
            results.append(FieldResult('Resume/CV','Needs candidate input','Resume upload control is ambiguous; no file was attached.'))
        else:
            results.append(FieldResult('Resume/CV','Needs candidate input','No resume upload control was found.'))
        # Never infer residence, work authorization, salary, legal declarations or consent.
        snapshot=_form_dom_snapshot(page)
        mapped_labels={item.label.casefold() for item in results}
        for field in snapshot['fields']:
            label=field.get('label') or 'Unlabeled required field'
            key=' '.join(label.casefold().split())
            if key in mapped_labels:
                continue
            if field.get('required') or re.search(r'\b(location|city|authorization|work eligibility|visa|salary|consent|privacy|legal|citizenship)\b',key):
                results.append(FieldResult(label,'Needs candidate input','Unanswered field; Lever preparation does not infer or select this value.'))
        return tuple(results)


def _matching_private_cv(root: Path, job_id: int, application_url: str) -> Path:
    private=root/'private'
    for metadata_path in private.rglob('application_metadata.json') if private.exists() else []:
        try:
            metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):
            continue
        if metadata.get('job_id')==job_id and metadata.get('application_url')==application_url:
            for filename in ('cv.pdf','tailored_cv.pdf','application_cv.pdf'):
                candidate_path=metadata_path.parent/filename
                try:
                    candidate_path.resolve().relative_to(private.resolve())
                except (OSError, ValueError):
                    continue
                if candidate_path.is_file() and candidate_path.suffix.casefold()=='.pdf':
                    return candidate_path
    raise ApplicationPreparationError('A private tailored CV with matching vacancy and application-URL metadata is required.')


ADAPTERS: dict[str, ApplicationFormAdapter] = {'greenhouse': GreenhouseAdapter(), 'lever': LeverAdapter()}


def prepare_greenhouse(job_id: int, *, root: Path = ROOT, headed: bool = True,
                       hold_browser: bool = True) -> PreparationResult:
    """Prepare any shortlisted official Greenhouse vacancy and stop before submission."""
    root = root.resolve()
    job = _load_shortlisted_job(job_id, root)
    _reject_explicitly_ineligible(job_id, root)
    candidate = profile() if root == ROOT else _load_profile(root)
    preferences = load_application_preferences(root)
    if job.platform.casefold() != 'greenhouse':
        raise ApplicationPreparationError('Greenhouse preparation requires an official Greenhouse vacancy.')
    cv_path = _matching_private_cv(root, job_id, str(job.url))
    application_answers = application_answers_for(job_id, job.company, str(job.url), preferences)
    directory, preview_path, state_path = _private_paths(root, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    if state_path.is_file():
        try:
            previous_state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            previous_state = {}
        if previous_state.get('status') in {
            'Awaiting approval', 'Ready for Approval', 'Approved', 'Submitting', 'Submitted',
            'Submission Uncertain',
        }:
            raise ApplicationPreparationError(
                'This application has an approval or submission record. Reconcile its state before preparing again.'
            )
    preview_image = directory / 'application_preview.png'
    fields_for_review = [
        FieldResult(label, 'Needs candidate input', reason)
        for label, reason in REVIEW_FIELDS
        if not application_answers or (
            (label.startswith('Are you legally') and not application_answers.work_authorization)
            or (label == 'Recruitment data-processing consent'
                and not application_answers.recruitment_data_processing_consent))
    ]
    _write_json(state_path, {'status': 'Prepared', 'job_id': job_id, 'completed_fields': [],
                             'candidate_input_fields': [asdict(item) for item in fields_for_review],
                             'application_url': str(job.url), 'preview_path': str(preview_path)})
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ApplicationPreparationError('Playwright is not installed. Install project requirements before launching.') from None

    from agent.collectors import check_robots
    allowed_hosts = {'job-boards.eu.greenhouse.io', 'boards.greenhouse.io'}
    final_url = str(job.url)
    with httpx.Client(timeout=30, headers={'User-Agent': 'QAJobAgent/0.1 (supervised application preparation)'},
                      follow_redirects=False) as client:
        for _ in range(5):
            check_robots(client, final_url, interval=1)
            preflight = client.get(final_url, follow_redirects=False)
            if preflight.is_redirect:
                target = urlparse(urljoin(final_url, preflight.headers.get('location', '')))
                if target.scheme != 'https' or target.hostname not in allowed_hosts:
                    raise ApplicationPreparationError('Greenhouse redirected outside its official hosts; preparation stopped.')
                final_url = target.geturl()
                continue
            if preflight.status_code >= 400:
                raise ApplicationPreparationError(f'The official Greenhouse page returned HTTP {preflight.status_code}; preparation stopped.')
            break
        else:
            raise ApplicationPreparationError('Too many redirects on the official Greenhouse application page.')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=not headed)
        page = browser.new_page(accept_downloads=False)
        page.add_init_script(_page_submit_guard_script())
        page.route('**/*', lambda route: _route_application_request(route, allowed_hosts))
        browser_response = page.goto(final_url, wait_until='domcontentloaded', timeout=60_000)
        page.wait_for_timeout(1000)
        if browser_response is None or browser_response.status >= 400:
            browser.close()
            raise ApplicationPreparationError('The official Greenhouse page did not load successfully.')
        if urlparse(page.url).hostname not in allowed_hosts:
            browser.close()
            raise ApplicationPreparationError('The application page redirected outside the supported Greenhouse hosts.')
        body = (page.evaluate("document.body ? document.body.innerText : ''") or '').casefold()
        if not body.strip():
            browser.close()
            raise ApplicationPreparationError('The official page loaded without readable form content; preparation stopped.')
        if _access_challenge_detected(body):
            browser.close()
            raise ApplicationPreparationError('A security or authentication challenge is visible. Preparation stopped; resolve it manually if appropriate.')
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
        submit_controls = page.locator('button[type="submit"],input[type="submit"]')
        for index in range(submit_controls.count()):
            if submit_controls.nth(index).is_enabled():
                browser.close()
                raise ApplicationPreparationError('The final submission control could not be disabled; preparation stopped.')
        page.screenshot(path=str(preview_image), full_page=True)
        candidate_input_fields = (*location_reviews, *fields_for_review)
        review_snapshot = None
        review_hash = None
        source_hash = None
        pilot_submission_supported = job_id == 4 and job.company.casefold() == 'bitdefender'
        if pilot_submission_supported and not candidate_input_fields:
            from agent.application_submission import (
                _load_current_sources,
                build_review_snapshot,
                review_fingerprint,
            )
            _, _, _, current_answers, current_cv, mapped, source_hash = _load_current_sources(job_id, root)
            review_snapshot = build_review_snapshot(
                job_id, job, mapped, current_answers, current_cv, results,
                _form_dom_snapshot(page))
            review_hash = review_fingerprint(review_snapshot)
        # New employers receive preparation only. Their form snapshots are not
        # eligible for the existing candidate-approved submission state machine.
        application_state = ('Needs Review' if candidate_input_fields else 'Ready for Approval') \
            if pilot_submission_supported else ('Needs Review' if candidate_input_fields else 'Prepared')
        result = PreparationResult(job_id=job_id, employer=job.company, role=job.title,
            application_url=str(job.url), status=application_state,
            completed_fields=tuple(item.label for item in completed),
            candidate_input_fields=tuple(item.label for item in candidate_input_fields),
            preview_path=str(preview_path))
        _write_json(preview_path, {
            'job_id': job_id, 'employer': job.company, 'role': job.title,
            'application_url': str(job.url), 'status': result.status,
            'completed_fields': [asdict(item) for item in completed],
            'candidate_input_fields': [asdict(item) for item in candidate_input_fields],
            'review_snapshot': review_snapshot,
            'review_fingerprint': review_hash,
            'screenshot_path': str(preview_image),
            'submission_enabled': pilot_submission_supported,
            'final_submission': ('Not attempted; explicit approval for this exact snapshot is required.'
                if application_state == 'Ready for Approval' else 'Blocked until required candidate review is complete.')
                if pilot_submission_supported else 'Not attempted; submission is not enabled for this employer.',
        })
        _write_json(state_path, {'status': result.status, 'job_id': job_id,
            'completed_fields': list(result.completed_fields),
            'candidate_input_fields': [asdict(item) for item in candidate_input_fields],
            'application_url': str(job.url), 'preview_path': str(preview_path),
            'review_fingerprint': review_hash,
            'source_fingerprint': source_hash,
            'submission_enabled': pilot_submission_supported,
            'browser_open': bool(hold_browser)})
        if hold_browser:
            while not page.is_closed():
                try:
                    page.wait_for_timeout(500)
                except Exception:
                    break
        browser.close()
        _write_json(state_path, {'status': result.status, 'job_id': job_id,
            'completed_fields': list(result.completed_fields),
            'candidate_input_fields': [asdict(item) for item in candidate_input_fields],
            'application_url': str(job.url), 'preview_path': str(preview_path),
            'review_fingerprint': review_hash,
            'source_fingerprint': source_hash,
            'submission_enabled': pilot_submission_supported,
            'browser_open': False})
        return result



def prepare_lever(job_id: int, *, root: Path = ROOT, headed: bool = True,
                  hold_browser: bool = True) -> PreparationResult:
    """Prepare supported Lever identity fields/CV only; legal and submission actions remain manual."""
    root=root.resolve()
    job=_load_shortlisted_job(job_id,root)
    if job.platform.casefold()!='lever':
        raise ApplicationPreparationError('Lever preparation requires a vacancy from the official Lever postings platform.')
    candidate=profile() if root==ROOT else _load_profile(root)
    cv_path=_matching_private_cv(root,job_id,str(job.url))
    directory,preview_path,state_path=_private_paths(root,job_id)
    directory.mkdir(parents=True,exist_ok=True)
    if state_path.is_file():
        try: state=json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError): state={}
        if state.get('status') in {
            'Awaiting approval', 'Ready for Approval', 'Approved', 'Submitting', 'Submitted',
            'Submission Uncertain',
        }:
            raise ApplicationPreparationError('A submission record exists; preparation is blocked pending manual reconciliation.')
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ApplicationPreparationError('Playwright is not installed. Install project requirements before launching.') from None
    from agent.collectors import check_robots
    with httpx.Client(timeout=30,headers={'User-Agent':'QAJobAgent/0.1 (supervised application preparation)'},follow_redirects=False) as client:
        final_url=str(job.url)
        for _ in range(5):
            check_robots(client,final_url,interval=1)
            response=client.get(final_url,follow_redirects=False)
            if response.is_redirect:
                target=urlparse(urljoin(final_url,response.headers.get('location','')))
                if target.scheme!='https' or target.hostname!='jobs.lever.co':
                    raise ApplicationPreparationError('Lever redirected outside its official host; preparation stopped.')
                final_url=target.geturl()
                continue
            if response.status_code>=400:
                raise ApplicationPreparationError(f'The official Lever page returned HTTP {response.status_code}; preparation stopped.')
            break
        else:
            raise ApplicationPreparationError('Too many redirects on the official Lever application page.')
    preview_image=directory/'application_preview.png'
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(channel='msedge',headless=not headed)
        page=browser.new_page(accept_downloads=False)
        page.add_init_script(_page_submit_guard_script())
        page.route('**/*',lambda route: _route_application_request(route, {'jobs.lever.co'}))
        response=page.goto(final_url,wait_until='domcontentloaded',timeout=60_000)
        page.wait_for_timeout(1000)
        if response is None or response.status>=400 or urlparse(page.url).hostname!='jobs.lever.co':
            browser.close()
            raise ApplicationPreparationError('The official Lever form did not load on its approved host.')
        body=(page.evaluate('document.body ? document.body.innerText : ""') or '').casefold()
        if not body.strip() or _access_challenge_detected(body):
            browser.close()
            raise ApplicationPreparationError('A security challenge or unreadable page is present; preparation stopped.')
        results=ADAPTERS['lever'].fill(page,candidate,cv_path,load_application_preferences(root),None)
        page.evaluate(_page_submit_guard_script())
        submit_controls=page.locator('button[type="submit"],input[type="submit"]')
        for index in range(submit_controls.count()):
            if submit_controls.nth(index).is_enabled():
                browser.close()
                raise ApplicationPreparationError('The final submission control could not be disabled; preparation stopped.')
        snapshot=_form_dom_snapshot(page)
        page.screenshot(path=str(preview_image),full_page=True)
        completed=tuple(item for item in results if item.status in {'Completed','Uploaded'})
        reviews=tuple(item for item in results if item.status=='Needs candidate input')
        status='Needs Review' if reviews else 'Prepared'
        _write_json(preview_path,{'job_id':job_id,'employer':job.company,'role':job.title,'application_url':str(job.url),
            'status':status,'completed_fields':[asdict(item) for item in completed],
            'candidate_input_fields':[asdict(item) for item in reviews],'form_snapshot':snapshot,
            'screenshot_path':str(preview_image),'final_submission':'Not attempted; Lever submission is not enabled.'})
        _write_json(state_path,{'status':status,'job_id':job_id,'application_url':str(job.url),
            'completed_fields':[item.label for item in completed],
            'candidate_input_fields':[asdict(item) for item in reviews],'preview_path':str(preview_path),
            'browser_open':bool(hold_browser),'submission_enabled':False})
        if hold_browser:
            while not page.is_closed():
                try: page.wait_for_timeout(500)
                except Exception: break
        browser.close()
        _write_json(state_path,{'status':status,'job_id':job_id,'application_url':str(job.url),
            'completed_fields':[item.label for item in completed],
            'candidate_input_fields':[asdict(item) for item in reviews],'preview_path':str(preview_path),
            'browser_open':False,'submission_enabled':False})
        return PreparationResult(job_id=job_id,employer=job.company,role=job.title,application_url=str(job.url),
            status=status,completed_fields=tuple(item.label for item in completed),
            candidate_input_fields=tuple(item.label for item in reviews),preview_path=str(preview_path))


def _launch_lever_from_dashboard(job_id: int, root: Path = ROOT) -> None:
    job=_load_shortlisted_job(job_id,root)
    if job.platform.casefold()!='lever':
        raise ApplicationPreparationError('Selected vacancy is not an official Lever posting.')
    _matching_private_cv(root,job_id,str(job.url))
    directory,preview_path,state_path=_private_paths(root,job_id)
    directory.mkdir(parents=True,exist_ok=True)
    if state_path.is_file():
        try:
            previous=json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):
            previous={}
        if previous.get('status') in {
            'Awaiting approval','Ready for Approval','Approved','Submitting','Submitted','Submission Uncertain',
        }:
            raise ApplicationPreparationError('An existing approval or submission record must be reconciled before preparing again.')
    _write_json(state_path,{'status':'Prepared','job_id':job_id,'completed_fields':[],
        'candidate_input_fields':[],'application_url':str(job.url),'preview_path':str(preview_path),
        'browser_open':True,'submission_enabled':False})
    subprocess.Popen([sys.executable,'-m','agent.application_workflow','--job-id',str(job_id),
        '--platform','lever','--headed'],cwd=str(root),close_fds=True)

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
    if job.platform.casefold() != 'greenhouse':
        raise ApplicationPreparationError('Selected vacancy is not an official Greenhouse posting.')
    _matching_private_cv(root, job_id, str(job.url))
    directory, preview_path, state_path = _private_paths(root, job_id)
    directory.mkdir(parents=True, exist_ok=True)
    if state_path.is_file():
        try:
            old_state = json.loads(state_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            old_state = {}
        if old_state.get('status') in {
            'Awaiting approval', 'Ready for Approval', 'Approved', 'Submitting', 'Submitted',
            'Submission Uncertain',
        }:
            raise ApplicationPreparationError(
                'This application has an approval or submission record. Reconcile its state before preparing again.'
            )
    _write_json(state_path, {'status': 'Prepared', 'job_id': job_id,
        'completed_fields': [], 'candidate_input_fields': [
            {'label': label, 'status': 'Needs candidate input', 'note': note}
            for label, note in REVIEW_FIELDS],
        'application_url': str(job.url), 'preview_path': str(preview_path), 'browser_open': True,
        'submission_enabled': job_id == 4 and job.company.casefold() == 'bitdefender'})
    subprocess.Popen([sys.executable, '-m', 'agent.application_workflow', '--job-id', str(job_id), '--headed'],
                     cwd=str(root), close_fds=True)


def main() -> None:
    parser = argparse.ArgumentParser(description='Prepare a Shortlisted Greenhouse application for supervised review.')
    parser.add_argument('--job-id', type=int, required=True)
    parser.add_argument('--platform', choices=['greenhouse','lever'], default='greenhouse')
    parser.add_argument('--headed', action='store_true', help='Open the employer form in a visible Edge window (default).')
    parser.add_argument('--headless', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        result = (prepare_lever(args.job_id, headed=not args.headless, hold_browser=True)
                  if args.platform == 'lever' else
                  prepare_greenhouse(args.job_id, headed=not args.headless, hold_browser=True))
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    except Exception as exc:
        _, _, state_path = _private_paths(ROOT, args.job_id)
        if state_path.parent.is_dir():
            _write_json(state_path, {'status': 'Needs Review', 'job_id': args.job_id,
                'completed_fields': [], 'candidate_input_fields': [
                    {'label': label, 'status': 'Needs candidate input', 'note': note}
                    for label, note in REVIEW_FIELDS], 'browser_open': False,
                'state_note': type(exc).__name__})
        message = str(exc) if isinstance(exc, (ApplicationPreparationError, OSError)) else (
            f'Unexpected preparation error ({type(exc).__name__}); inspect this local terminal.')
        parser.error(message)


if __name__ == '__main__':
    main()

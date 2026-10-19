from pathlib import Path

import pytest
from pydantic import HttpUrl
from streamlit.testing.v1 import AppTest

from agent.application_workflow import (
    ApplicationPreparationError,
    _fill_form,
    _load_shortlisted_job,
    _reject_explicitly_ineligible,
    _page_submit_guard_script,
    fill_supported_control,
    map_verified_fields,
)


class FakeLocator:
    def __init__(self, *, present=True, store=None, key=''):
        self.present = present
        self.store = store if store is not None else {}
        self.key = key

    def count(self):
        return 1 if self.present else 0

    def is_visible(self):
        return self.present

    def fill(self, value):
        self.store[self.key] = value

    def locator(self, selector):
        return FakeLocator(present=self.present, store=self.store, key='country_control')

    def press_sequentially(self, value):
        self.store[self.key] = value

    def set_input_files(self, value):
        self.store[self.key] = value

    def select_option(self, *, label):
        self.store[self.key] = label

    def check(self):
        self.store[self.key] = True

    def uncheck(self):
        self.store[self.key] = False

    def click(self):
        self.store['country_selected'] = True


class FakePage:
    def __init__(self):
        self.values = {}

    def locator(self, selector):
        return FakeLocator(store=self.values, key=selector)

    def get_by_role(self, role, name=None):
        return FakeLocator(present=(role == 'option'), store=self.values, key='country_option')

    def get_by_text(self, value, exact=False):
        return FakeLocator(present=False, store=self.values, key='country_text')


def _full_candidate(candidate):
    return candidate.model_copy(update={
        'name': 'Synthetic Test Candidate',
        'location': 'Chișinău, Moldova',
        'email': 'synthetic@example.invalid',
        'telephone': '+37300000000',
        'work_authorization': 'Authorized to work in Romania',
        'salary_expectations': 'unknown',
    })


def test_verified_mapping_excludes_legal_salary_and_consent(candidate):
    mapped = map_verified_fields(_full_candidate(candidate))
    assert mapped == {
        'first_name': 'Synthetic', 'last_name': 'Test Candidate',
        'email': 'synthetic@example.invalid',
        'country': 'Moldova', 'phone': '+37300000000', 'city': 'Chișinău',
    }
    assert 'preferred_name' not in mapped
    assert not any('authorization' in field or 'salary' in field or 'consent' in field
                   for field in mapped)
    assert 'Authorized to work in Romania' not in str(mapped)


def test_greenhouse_mock_form_fills_mapped_fields_and_uploads_cv(tmp_path, candidate):
    cv = tmp_path / 'synthetic-cv.pdf'
    cv.write_bytes(b'%PDF-1.4 synthetic fixture')
    page = FakePage()
    results = _fill_form(page, _full_candidate(candidate), cv)

    assert [result.label for result in results] == [
        'First name', 'Last name', 'Email',
        'Country', 'Phone', 'Location (City)', 'Resume/CV',
    ]
    assert page.values['#first_name'] == 'Synthetic'
    assert page.values['#last_name'] == 'Test Candidate'
    assert '#preferred_name' not in page.values
    assert page.values['#country'] == 'Moldova'
    assert page.values['#candidate-location'] == 'Chișinău'
    assert page.values['input[type="file"]#resume'] == str(cv.resolve())
    assert page.values['country_selected'] is True
    assert not any('authorization' in field or 'salary' in field or 'consent' in field
                   for field in page.values)


def test_submit_guard_blocks_form_event_and_disables_submit_control():
    guard = _page_submit_guard_script()
    assert 'preventDefault()' in guard
    assert "addEventListener('submit', block, true)" in guard
    assert 'el.disabled = true' in guard
    assert 'aria-disabled' in guard
    assert "el.getAttribute('aria-disabled') !== 'true'" in guard


@pytest.mark.parametrize(('kind', 'value', 'expected'), [
    ('text', 'verified text', 'verified text'),
    ('dropdown', 'Romania', 'Romania'),
    ('radio', 'Yes', True),
    ('checkbox', True, True),
    ('checkbox', False, False),
])
def test_supported_form_controls_require_explicit_values(kind, value, expected):
    locator = FakeLocator(store={}, key='field')
    fill_supported_control(locator, kind, value)
    assert locator.store['field'] == expected


def test_control_type_mismatch_never_falls_back_to_guessed_text():
    with pytest.raises(ApplicationPreparationError, match='Unsupported'):
        fill_supported_control(FakeLocator(), 'dropdown', True)


def test_shortlisted_gate_and_official_greenhouse_host_only(tmp_path, monkeypatch, candidate):
    from agent.db import Company, Source, JobRow, MatchRow, database
    from agent.discovery import upsert_job
    from agent.models import Job

    db_path = tmp_path / 'application.db'
    target = 'sqlite:///' + str(db_path)
    monkeypatch.setenv('DATABASE_URL', target)
    factory = database(target)
    job = Job(title='QA Engineer', company='Bitdefender', location='Bucharest', remote='Unknown',
              description='Synthetic public posting', url='https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4926040101',
              platform='greenhouse', source_id='4926040101')
    with factory() as session:
        company = Company(name='Bitdefender')
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, platform='greenhouse', board='scbitdefendersrl')
        session.add(source)
        session.flush()
        row, _ = upsert_job(session, job, source)
        row.status = 'Shortlisted'
        vacancy_id = row.id
        session.commit()

    assert _load_shortlisted_job(vacancy_id, tmp_path).company == 'Bitdefender'
    with factory() as session:
        assert session.get(JobRow, vacancy_id).status == 'Shortlisted'

    with factory() as session:
        row = session.get(JobRow, vacancy_id)
        row.status = 'New'
        session.commit()
    with pytest.raises(ApplicationPreparationError, match='Shortlisted'):
        _load_shortlisted_job(vacancy_id, tmp_path)

    with factory() as session:
        row = session.get(JobRow, vacancy_id)
        non_greenhouse = Job.model_validate_json(row.payload).model_copy(
            update={'url': HttpUrl('https://www.linkedin.com/jobs/view/123')})
        row.payload = non_greenhouse.model_dump_json()
        row.status = 'Shortlisted'
        session.commit()
    with pytest.raises(ApplicationPreparationError, match='platform|hosts'):
        _load_shortlisted_job(vacancy_id, tmp_path)

    with factory() as session:
        row = session.get(JobRow, vacancy_id)
        row.payload = job.model_dump_json()
        session.add(MatchRow(job_id=vacancy_id, payload='{"eligibility":"Ineligible"}', version='test'))
        session.commit()
    with pytest.raises(ApplicationPreparationError, match='explicitly ineligible'):
        _reject_explicitly_ineligible(vacancy_id, tmp_path)


def test_unknown_or_incomplete_profile_fields_are_never_guessed(candidate):
    incomplete = _full_candidate(candidate).model_copy(update={'email': 'unknown'})
    with pytest.raises(ApplicationPreparationError, match='missing a verified'):
        map_verified_fields(incomplete)
    no_country = _full_candidate(candidate).model_copy(update={'location': 'Chișinău'})
    with pytest.raises(ApplicationPreparationError, match='city and country'):
        map_verified_fields(no_country)


def test_dashboard_prepare_action_is_shortlisted_only_and_does_not_change_status(
        tmp_path, monkeypatch, candidate):
    import shutil
    from sqlalchemy import select
    from agent.db import Company, JobRow, MatchRow, Source, database
    from agent.models import Job, Match
    import agent.application_workflow as workflow

    target = 'sqlite:///' + str(tmp_path / 'dashboard-apply.db')
    monkeypatch.setenv('DATABASE_URL', target)
    monkeypatch.setattr('agent.settings.ROOT', tmp_path)
    (tmp_path / 'config').mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / 'config' / 'search_rules.yaml',
                tmp_path / 'config' / 'search_rules.yaml')
    factory = database(target)
    job = Job(title='QA Engineer', company='Bitdefender', location='Bucharest', remote='Unknown',
              description='Synthetic QA testing with Playwright and regression tests.',
              requirements='Playwright',
              url='https://job-boards.eu.greenhouse.io/scbitdefendersrl/jobs/4926040101',
              platform='greenhouse', source_id='4926040101')
    match = Match(score=85, matching_skills=['Playwright'], missing_skills=[], mandatory_requirements=[],
                  eligibility='Needs Review', explanation='Synthetic test match', recommendation='Good Match',
                  method='rules', professional_score=85, professional_fit='Good Match',
                  professional_reason='Synthetic test match', geographic_reason='Synthetic review',
                  in_main_recommendations=True)
    with factory() as session:
        company = Company(name='Bitdefender')
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, platform='greenhouse', board='scbitdefendersrl')
        session.add(source)
        session.flush()
        row = JobRow(id=4, source_id=source.id, identity='greenhouse:scbitdefendersrl:4926040101',
                     canonical_url=str(job.url), fingerprint='synthetic-fingerprint',
                     payload=job.model_dump_json(), status='Shortlisted')
        session.add(row)
        session.flush()
        session.add(MatchRow(job_id=4, payload=match.model_dump_json(), version=match.version))
        session.commit()
    launched = []
    monkeypatch.setattr(workflow, '_launch_from_dashboard', lambda job_id: launched.append(job_id))

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
    assert not app.exception
    assert not any(button.label == 'Prepare Application' for button in app.button)
    frame = app.dataframe[0].value
    row_index = int(frame.index[frame['ID'] == 4][0])
    app.session_state[app.dataframe[0].key] = {'selection': {'rows': [row_index], 'columns': [], 'cells': []}}
    app.run()
    prepare = next(button for button in app.button if button.label == 'Prepare Application')
    prepare.click().run()
    assert not app.exception
    assert launched == [4]
    with factory() as session:
        statuses = {row.id: row.status for row in session.scalars(select(JobRow))}
    assert statuses == {4: 'Shortlisted'}

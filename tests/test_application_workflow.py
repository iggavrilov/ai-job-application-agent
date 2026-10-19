from pathlib import Path

import pytest
from pydantic import HttpUrl
from streamlit.testing.v1 import AppTest

from agent.application_workflow import (
    ApplicationPreparationError,
    _fill_form,
    _fill_location_fields,
    _fill_verified_text,
    _load_shortlisted_job,
    _reject_explicitly_ineligible,
    _page_submit_guard_script,
    fill_supported_control,
    map_verified_fields,
    ApplicationPreferences,
    location_value_for_label,
    load_application_preferences,
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

    def nth(self, index):
        return self if index == 0 else FakeLocator(present=False, store=self.store, key=self.key)

    def evaluate(self, expression):
        if 'el.files' in expression:
            value = self.store.get(self.key)
            if value:
                path = Path(value)
                return [{'name': path.name, 'size': path.stat().st_size}]
            return []
        if 'tagName' in expression:
            kind = self.store.get('_types', {}).get(self.key, ('input', 'text', ''))
            return {'tag': kind[0], 'type': kind[1], 'role': kind[2]}
        return None

    def locator(self, selector):
        if selector == 'xpath=../../..':
            return FakeLocator(present=self.present, store=self.store, key=self.key + '::wrapper')
        if selector == '.select__single-value':
            base = self.key.removesuffix('::wrapper')
            return FakeLocator(present=self.present, store=self.store, key=base + '::selected')
        return FakeLocator(present=self.present, store=self.store, key=self.key)

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
        if self.key.endswith('::wrapper'):
            self.store['_active'] = self.key.removesuffix('::wrapper')
        elif self.key.startswith('option::'):
            selected = self.key.removeprefix('option::')
            self.store.setdefault('_selected', {})[self.store['_active']] = selected
            self.store[self.store['_active']] = selected
        else:
            self.store['country_selected'] = True

    def all_inner_texts(self):
        active = self.store.get('_active')
        return self.store.get('_options', {}).get(active, [])

    def wait_for(self, *, state='visible', timeout=None):
        if state == 'visible' and not self.all_inner_texts():
            raise TimeoutError('No synthetic option is visible.')

    def press(self, value):
        return None

    def get_attribute(self, name):
        if name == 'type':
            return self.store.get('_types', {}).get(self.key, ('input', 'text', ''))[1]
        return 'candidate-location' if name == 'for' and self.key == 'label' else None

    def input_value(self):
        if self.key.endswith('::selected'):
            return self.store.get('_selected', {}).get(self.key.removesuffix('::selected'), '')
        return self.store.get(self.key, '')

    def inner_text(self):
        if self.key.endswith('::selected'):
            selected = self.store.get('_selected', {}).get(self.key.removesuffix('::selected'), '')
            if self.key == '#country::selected' and '+' in selected:
                return '+' + selected.rsplit('+', 1)[-1]
            return selected
        return 'Location (City)*' if self.key in {'label', 'label[for="candidate-location"]'} else ''


class FakePhoneLocator(FakeLocator):
    def fill(self, value):
        self.store[self.key] = '+1 555 010 0000'

    def get_attribute(self, name):
        return 'tel' if name == 'type' else None


class FakeLabel:
    def __init__(self, store, text, field_id):
        self.store, self.text, self.field_id = store, text, field_id

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.field_id if name == 'for' else None


class FakeLabelCollection:
    def __init__(self, store, labels):
        self.store, self.labels = store, labels

    def count(self):
        return len(self.labels)

    def nth(self, index):
        text, field_id = self.labels[index]
        return FakeLabel(self.store, text, field_id)


class FakePage:
    def __init__(self):
        self.values = {}
        self.values['_options'] = {'#country': ['Exampleland']}
        self.labels = [('Location (City)*', 'candidate-location')]
        self.required_fields = []

    def locator(self, selector):
        if selector == 'label':
            return FakeLabelCollection(self.values, self.labels)
        return FakeLocator(store=self.values, key=selector)

    def evaluate(self, expression):
        return self.required_fields

    def get_by_role(self, role, name=None, exact=False):
        if role != 'option':
            return FakeLocator(present=False, store=self.values, key='')
        if name is not None:
            return FakeLocator(present=name in self.values.get('_options', {}).get(self.values.get('_active'), []),
                               store=self.values, key='option::' + name)
        return FakeLocator(present=True, store=self.values, key='options')

    def get_by_text(self, value, exact=False):
        return FakeLocator(present=False, store=self.values, key='country_text')


def _full_candidate(candidate):
    return candidate.model_copy(update={
        'name': 'Synthetic Test Candidate',
        'location': 'Sample City, Exampleland',
        'email': 'synthetic@example.invalid',
        'telephone': '+37300000000',
        'work_authorization': 'Synthetic work authorization',
        'salary_expectations': 'unknown',
    })


def _confirmed_preferences():
    return ApplicationPreferences(
        first_name='Synthetic', last_name='Applicant', preferred_first_name='Sam',
        current_residence_city='Sample City', current_residence_country='Exampleland',
        preferred_job_location_romania='Romanian Test City',
        preferred_job_location_moldova='Moldovan Test City',
        salary_expectation='5,200 TEST/month',
    )


def test_verified_mapping_uses_confirmed_preferences_without_legal_answers(candidate):
    mapped = map_verified_fields(_full_candidate(candidate), _confirmed_preferences())
    assert mapped == {
        'first_name': 'Synthetic', 'last_name': 'Applicant',
        'preferred_first_name': 'Sam', 'email': 'synthetic@example.invalid',
        'country': 'Exampleland', 'phone': '+37300000000',
        'current_residence_city': 'Sample City', 'current_residence': 'Sample City, Exampleland',
        'preferred_job_location_romania': 'Romanian Test City',
        'preferred_job_location_moldova': 'Moldovan Test City',
        'salary_expectation': '5,200 TEST/month',
    }
    assert not any('authorization' in field or 'consent' in field for field in mapped)
    assert 'Synthetic work authorization' not in str(mapped)


def test_phone_dom_verification_allows_formatting_only_when_digits_match():
    locator = FakePhoneLocator(store={}, key='phone')
    _fill_verified_text(locator, '#phone', '+15550100000')
    assert locator.input_value() == '+1 555 010 0000'

    class WrongPhoneLocator(FakePhoneLocator):
        def fill(self, value):
            self.store[self.key] = '+1 555 111 1111'

    wrong = WrongPhoneLocator(store={}, key='phone')
    with pytest.raises(ApplicationPreparationError, match=r'#phone'):
        _fill_verified_text(wrong, '#phone', '+15550100000')


def test_location_label_mapping_distinguishes_residence_and_preference(candidate):
    mapped = map_verified_fields(_full_candidate(candidate), _confirmed_preferences())
    assert location_value_for_label('Current City', mapped) == ('Sample City', None)
    assert location_value_for_label('Current Location', mapped) == ('Sample City, Exampleland', None)
    assert location_value_for_label('Preferred job location in Romania', mapped) == ('Romanian Test City', None)
    assert location_value_for_label('Preferred job location in Moldova', mapped) == ('Moldovan Test City', None)
    value, reason = location_value_for_label('Location (City)*', mapped)
    assert value is None and 'ambiguous' in reason


def test_location_scanner_fills_only_explicit_residence_or_preference_fields(candidate):
    mapped = map_verified_fields(_full_candidate(candidate), _confirmed_preferences())
    page = FakePage()
    page.labels = [
        ('Preferred job location in Romania*', 'romania-location'),
        ('Preferred job location in Moldova*', 'moldova-location'),
        ('Current City*', 'residence-city'),
        ('Location (City)*', 'ambiguous-location'),
    ]

    results = _fill_location_fields(page, mapped)

    assert page.values['[id="romania-location"]'] == 'Romanian Test City'
    assert page.values['[id="moldova-location"]'] == 'Moldovan Test City'
    assert page.values['[id="residence-city"]'] == 'Sample City'
    assert '[id="ambiguous-location"]' not in page.values
    ambiguous = next(item for item in results if item.label == 'Location (City)')
    assert ambiguous.status == 'Needs candidate input'


def test_vacancy_answer_override_fills_ambiguous_location_without_changing_residence(candidate):
    mapped = map_verified_fields(_full_candidate(candidate), _confirmed_preferences())
    page = FakePage()
    page.labels = [
        ('Current Residence*', 'residence'),
        ('Location (City)*', 'ambiguous-location'),
    ]
    results = _fill_location_fields(page, mapped, location_override='Romanian Test City')
    assert page.values['[id="residence"]'] == 'Sample City, Exampleland'
    assert page.values['[id="ambiguous-location"]'] == 'Romanian Test City'
    assert all(item.status == 'Completed' for item in results)


def test_vacancy_answers_are_scoped_to_id_employer_and_exact_url():
    from agent.application_workflow import application_answers_for
    preferences = ApplicationPreferences(vacancy_answers={
        '7': {
            'employer': 'Synthetic Employer',
            'application_url': 'https://job-boards.eu.greenhouse.io/synthetic/jobs/123',
            'location_city': 'Romanian Test City',
            'work_authorization': 'Yes',
            'recruitment_data_processing_consent': 'Acknowledge',
            'salary_form_value': '5,200 TEST/month',
        }
    })
    assert application_answers_for(7, 'Synthetic Employer',
        'https://job-boards.eu.greenhouse.io/synthetic/jobs/123', preferences).work_authorization == 'Yes'
    assert application_answers_for(8, 'Synthetic Employer',
        'https://job-boards.eu.greenhouse.io/synthetic/jobs/123', preferences) is None
    with pytest.raises(ApplicationPreparationError, match='do not match'):
        application_answers_for(7, 'Other Employer',
            'https://job-boards.eu.greenhouse.io/synthetic/jobs/123', preferences)


def test_confirmed_dropdown_answers_are_selected_and_read_back(candidate):
    from agent.application_workflow import VacancyApplicationAnswers
    page = FakePage()
    answers = VacancyApplicationAnswers(
        employer='Synthetic Employer',
        application_url='https://job-boards.eu.greenhouse.io/synthetic/jobs/123',
        location_city='Romanian Test City',
        work_authorization='Yes',
        recruitment_data_processing_consent='Acknowledge',
        salary_form_value='5,200 TEST/month',
    )
    page.values['_types'] = {
        '#country': ('input', 'text', 'combobox'),
        '[id="candidate-location"]': ('input', 'text', 'combobox'),
        '#question_9299067101': ('input', 'text', 'combobox'),
        '#question_9299069101': ('input', 'text', 'combobox'),
    }
    page.values['_options'] = {
        '#country': ['Exampleland +999'],
        '[id="candidate-location"]': ['Romanian Test City'],
        '#question_9299067101': ['Yes', 'No'],
        '#question_9299069101': ['Acknowledge'],
    }
    results = _fill_form(page, _full_candidate(candidate), Path(__file__),
                         _confirmed_preferences(), answers)
    for label, expected in [
        ('Location (City)', 'Romanian Test City'),
        ('Work authorization', 'Yes'),
        ('Recruitment data-processing consent', 'Acknowledge'),
        ('Salary expectations', '5,200 TEST/month'),
    ]:
        result = next(item for item in results if item.label == label)
        assert result.status == 'Completed'
        assert expected in result.note
    assert page.values['_selected']['[id="candidate-location"]'] == 'Romanian Test City'
    assert page.values['_selected']['#question_9299067101'] == 'Yes'
    assert page.values['_selected']['#question_9299069101'] == 'Acknowledge'
    assert page.values['#question_9299068101'] == '5,200 TEST/month'


def test_country_dropdown_verifies_greenhouse_dial_code_readback():
    from agent.application_workflow import _select_react_option
    page = FakePage()
    page.values['_options'] = {'#country': ['Moldova +373']}
    selected = _select_react_option(page, '#country', 'Moldova')
    assert selected == 'Moldova +373'
    assert page.values['_selected']['#country'] == 'Moldova +373'


def test_city_dropdown_uses_unique_confirmed_city_alias():
    from agent.application_workflow import _select_react_option
    page = FakePage()
    selector = '[id="candidate-location"]'
    page.values['_options'] = {
        selector: ['Bucure\u0219ti, Bucharest, Romania', 'Cotul Bucuresti, Iasi, Romania']
    }
    selected = _select_react_option(page, selector, 'Bucure\u0219ti (Bucharest)')
    assert selected == 'Bucure\u0219ti, Bucharest, Romania'
    assert page.values['_selected'][selector] == selected


def test_application_preferences_load_only_from_private_path(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    path = private / 'application_preferences.json'
    path.write_text(_confirmed_preferences().model_dump_json(), encoding='utf-8')
    loaded = load_application_preferences(tmp_path)
    assert loaded.preferred_job_location_romania == 'Romanian Test City'
    assert loaded.salary_expectation == '5,200 TEST/month'


def test_existing_application_preferences_remain_backward_compatible(tmp_path):
    private=tmp_path/'private'
    private.mkdir()
    path=private/'application_preferences.json'
    path.write_text('{"current_residence_country":"Moldova"}',encoding='utf-8')
    loaded=load_application_preferences(tmp_path)
    assert loaded.current_residence_country=='Moldova'
    assert loaded.geographic_search is None


def test_geographic_search_preferences_validate_without_assuming_us_authorization(tmp_path):
    private=tmp_path/'private'
    private.mkdir()
    path=private/'application_preferences.json'
    path.write_text(ApplicationPreferences(
        current_residence_city='Chișinău',
        current_residence_country='Moldova',
        preferred_job_location_romania='București (Bucharest)',
        preferred_job_location_moldova='Chișinău',
        geographic_search={
            'preferred_work_arrangement':'Remote while residing in current country',
            'willing_to_relocate':True,
            'relocation_scope':['Romania','European Union'],
            'relocation_only_for_attractive_offer':True,
            'eu_work_authorization_confirmed':True,
            'us_work_authorization':'Unknown',
            'priority_order':[
                'confirmed_remote_from_moldova',
                'international_remote_moldova_unconfirmed',
                'moldova_based',
                'remote_requiring_romania_or_eu_residence',
                'eu_hybrid_or_onsite',
                'us_international_remote_or_b2b',
            ],
        },
    ).model_dump_json(),encoding='utf-8')
    loaded=load_application_preferences(tmp_path)
    assert loaded.geographic_search is not None
    assert loaded.geographic_search.eu_work_authorization_confirmed
    assert loaded.geographic_search.us_work_authorization=='Unknown'
    assert loaded.geographic_search.priority_order[0]=='confirmed_remote_from_moldova'


def test_greenhouse_mock_form_verifies_names_and_uploads_cv(tmp_path, candidate):
    cv = tmp_path / 'synthetic-cv.pdf'
    cv.write_bytes(b'%PDF-1.4 synthetic fixture')
    page = FakePage()
    results = _fill_form(page, _full_candidate(candidate), cv, _confirmed_preferences())

    assert [result.label for result in results] == [
        'First name', 'Last name', 'Preferred First Name', 'Email',
        'Country', 'Phone', 'Location (City)', 'Salary expectations', 'Resume/CV',
    ]
    assert page.values['#first_name'] == 'Synthetic'
    assert page.values['#last_name'] == 'Applicant'
    assert page.values['#preferred_name'] == 'Sam'
    assert page.values['#country'] == 'Exampleland'
    assert '#candidate-location' not in page.values
    location_result = next(result for result in results if result.label == 'Location (City)')
    assert location_result.status == 'Needs candidate input'
    assert 'ambiguous' in location_result.note
    assert page.values['#question_9299068101'] == '5,200 TEST/month'
    assert page.values['input[type="file"]#resume'] == str(cv.resolve())
    resume_result = next(result for result in results if result.label == 'Resume/CV')
    assert f'Verified attached file: {cv.name} ({cv.stat().st_size} bytes)' == resume_result.note
    assert page.values['_selected']['#country'] == 'Exampleland'
    assert not any('authorization' in field or 'consent' in field for field in page.values)


def test_unknown_required_field_is_flagged_for_candidate_review(tmp_path, candidate):
    cv = tmp_path / 'synthetic-cv.pdf'
    cv.write_bytes(b'%PDF-1.4 synthetic fixture')
    page = FakePage()
    page.required_fields = ['Portfolio URL*']
    results = _fill_form(page, _full_candidate(candidate), cv, _confirmed_preferences())
    review = next(item for item in results if item.label == 'Portfolio URL*')
    assert review.status == 'Needs candidate input'
    assert 'Required field remains empty' in review.note


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
    no_country = _full_candidate(candidate).model_copy(update={'location': 'Sample City'})
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

from pathlib import Path
import json

from sqlalchemy import select
from streamlit.testing.v1 import AppTest
from tests.support import discovery_dashboard_app_test

from agent.db import Company, JobRow, MatchRow, Source, database
from agent.discovery import upsert_job
from agent.matching import evaluate
from agent.models import Job
from agent.settings import rules


def _seed_same_title_jobs(factory, candidate):
    with factory() as session:
        company = Company(name='Bitdefender')
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, platform='greenhouse', board='bitdefender')
        session.add(source)
        session.flush()
        jobs = [
            Job(title='QA Engineer', company='Bitdefender', location='Moldova', remote='Remote',
                description='Moldova role description with Playwright and REST API testing.',
                requirements='Playwright and REST API testing', url='https://jobs.example/qa-md',
                platform='greenhouse', source_id='qa-md'),
            Job(title='QA Engineer', company='Bitdefender', location='Romania', remote='Hybrid',
                description='Romania role description with Selenium and SQL validation.',
                requirements='Selenium and SQL validation', url='https://jobs.example/qa-ro',
                platform='greenhouse', source_id='qa-ro'),
        ]
        rows = []
        for job in jobs:
            row, _ = upsert_job(session, job, source)
            rows.append(row)
            session.add(MatchRow(job_id=row.id,
                                 payload=evaluate(job, candidate, rules(), False).model_dump_json()))
        session.commit()
        return [row.id for row in rows]


def _select_vacancy(app, vacancy_id):
    frame = app.dataframe[0].value
    row_index = int(frame.index[frame['ID'] == vacancy_id][0])
    key = app.dataframe[0].key
    app.session_state[key] = {'selection': {'rows': [row_index], 'columns': [], 'cells': []}}
    app.run()


def test_same_title_rows_select_distinct_vacancy_details(tmp_path, monkeypatch, candidate):
    target = 'sqlite:///' + str(tmp_path / 'same-title.db')
    monkeypatch.setenv('DATABASE_URL', target)
    factory = database(target)
    first_id, second_id = _seed_same_title_jobs(factory, candidate)
    app = discovery_dashboard_app_test()
    assert not app.exception
    assert app.dataframe[0].value['Job title'].tolist() == ['QA Engineer', 'QA Engineer']

    _select_vacancy(app, first_id)
    assert app.session_state['selected_vacancy_id'] == first_id
    assert app.success[0].value == f'Selected vacancy #{first_id}: Bitdefender — QA Engineer'
    assert any('Moldova role description' in element.value for element in app.text)
    first_url = next(link.proto.url for link in app.link_button if link.label == 'Open job posting')
    assert first_url == 'https://jobs.example/qa-md'

    _select_vacancy(app, second_id)
    assert app.session_state['selected_vacancy_id'] == second_id
    assert app.success[0].value == f'Selected vacancy #{second_id}: Bitdefender — QA Engineer'
    assert any('Romania role description' in element.value for element in app.text)
    second_url = next(link.proto.url for link in app.link_button if link.label == 'Open job posting')
    assert second_url == 'https://jobs.example/qa-ro'
    assert any('Hybrid' in element.value for element in app.markdown)
    assert any('Geographic eligibility' in element.value for element in app.markdown)


def test_selection_persists_when_visible_and_clears_when_filtered_out(tmp_path, monkeypatch, candidate):
    target = 'sqlite:///' + str(tmp_path / 'selection-filters.db')
    monkeypatch.setenv('DATABASE_URL', target)
    factory = database(target)
    first_id, second_id = _seed_same_title_jobs(factory, candidate)
    app = discovery_dashboard_app_test()

    _select_vacancy(app, second_id)
    next(widget for widget in app.multiselect if widget.label == 'Region').select('Romania').run()
    assert not app.exception
    assert app.session_state['selected_vacancy_id'] == second_id
    assert app.success[0].value == f'Selected vacancy #{second_id}: Bitdefender — QA Engineer'
    assert app.dataframe[0].value['ID'].tolist() == [second_id]

    next(widget for widget in app.multiselect if widget.label == 'Region').set_value(['Moldova']).run()
    assert not app.exception
    assert 'selected_vacancy_id' not in app.session_state
    assert any('no longer in the current filtered results' in item.value for item in app.info)
    assert app.dataframe[0].value['ID'].tolist() == [first_id]


def test_review_status_is_saved_to_selected_id_and_survives_reruns(tmp_path, monkeypatch, candidate):
    target = 'sqlite:///' + str(tmp_path / 'selected-status.db')
    monkeypatch.setenv('DATABASE_URL', target)
    factory = database(target)
    first_id, second_id = _seed_same_title_jobs(factory, candidate)
    app = discovery_dashboard_app_test()

    _select_vacancy(app, second_id)
    next(widget for widget in app.selectbox if widget.label == 'Update review status').select('Shortlisted').run()
    next(button for button in app.button if button.label == 'Save review status').click().run()
    assert not app.exception
    app.run()  # A new Streamlit rerun must reload the persisted status from SQLite.

    with factory() as session:
        rows = {row.id: row.status for row in session.scalars(select(JobRow))}
    assert rows == {first_id: 'New', second_id: 'Shortlisted'}
    assert app.session_state['selected_vacancy_id'] == second_id
    assert next(widget for widget in app.selectbox if widget.label == 'Update review status').value == 'Shortlisted'


def test_selected_indicator_and_status_follow_id_when_sort_order_changes(tmp_path, monkeypatch, candidate):
    target = 'sqlite:///' + str(tmp_path / 'selection-sort.db')
    monkeypatch.setenv('DATABASE_URL', target)
    factory = database(target)
    first_id, second_id = _seed_same_title_jobs(factory, candidate)
    with factory() as session:
        first = session.scalar(select(MatchRow).where(MatchRow.job_id == first_id))
        second = session.scalar(select(MatchRow).where(MatchRow.job_id == second_id))
        first_payload, second_payload = json.loads(first.payload), json.loads(second.payload)
        first_payload['professional_score'] = 20
        second_payload['professional_score'] = 95
        first.payload, second.payload = json.dumps(first_payload), json.dumps(second_payload)
        session.commit()

    app = discovery_dashboard_app_test()
    assert not app.exception
    assert next(widget for widget in app.selectbox if widget.label == 'Rank by').value == 'Professional score'
    assert app.dataframe[0].value['ID'].tolist() == [second_id, first_id]
    _select_vacancy(app, second_id)
    selected_label = f'Selected vacancy #{second_id}: Bitdefender — QA Engineer'
    assert app.success[0].value == selected_label

    next(widget for widget in app.selectbox if widget.label == 'Rank by').select('Geographic priority').run()
    assert not app.exception
    assert app.dataframe[0].value['ID'].tolist() == [first_id, second_id]
    assert app.session_state['selected_vacancy_id'] == second_id
    assert app.success[0].value == selected_label
    assert any('Romania role description' in element.value for element in app.text)

    next(widget for widget in app.selectbox if widget.label == 'Update review status').select('Shortlisted').run()
    next(button for button in app.button if button.label == 'Save review status').click().run()
    with factory() as session:
        statuses = {row.id: row.status for row in session.scalars(select(JobRow))}
    assert statuses == {first_id: 'New', second_id: 'Shortlisted'}

import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from streamlit.testing.v1 import AppTest

from agent.db import Base, Company, Source, JobRow, JobObservation, MatchRow, ApplicationRecord, ManualApplicationRecord
from agent.manual_applications import (
    APPLIED_SOURCE, ImportRejected, confirm_manual_applications, import_vacancy,
    normalize_job_url, parse_bulk_urls, set_manual_application, fetch_vacancy, _job_posting_jsonld,
)
from agent.manual_applications import is_relevant_vacancy
from agent.models import Job


@pytest.fixture
def db(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'manual.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        company = Company(name='Example QA Employer')
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, platform='Greenhouse', board='example')
        session.add(source)
        session.flush()
        for job_id in (101, 104, 105, 140):
            job = Job(title=f'QA Engineer {job_id}', company='Example QA Employer', location='Bucharest, Romania',
                      remote='Hybrid', description='Software quality assurance', url=f'https://boards.greenhouse.io/example/jobs/{job_id}',
                      platform='Greenhouse', source_id=str(source.id))
            session.add(JobRow(id=job_id, source_id=source.id, identity=f'gh:{job_id}', canonical_url=str(job.url),
                               fingerprint=f'fp:{job_id}', payload=job.model_dump_json(), status='Shortlisted'))
        session.add(ApplicationRecord(job_id=104, status='Prepared'))
    yield factory
    engine.dispose()


def test_manual_confirmed_submissions_are_separate_and_idempotent(db):
    with db.begin() as session:
        assert confirm_manual_applications(session, (101, 104, 105, 140), applied_date='2024-05-01') == 4
    with db.begin() as session:
        assert confirm_manual_applications(session, (101, 104, 105, 140), applied_date='2024-05-01') == 4
    with db() as session:
        records = list(session.scalars(select(ManualApplicationRecord).order_by(ManualApplicationRecord.job_id)))
        assert [r.job_id for r in records] == [101, 104, 105, 140]
        assert all(r.status == 'Applied' and r.applied_date == '2024-05-01' for r in records)
        assert all(r.source == APPLIED_SOURCE for r in records)
        assert len({r.job_id for r in records}) == 4
        assert session.scalar(select(ApplicationRecord).where(ApplicationRecord.job_id == 104)).status == 'Prepared'
        assert session.scalar(select(func.count()).select_from(ApplicationRecord)) == 1
        assert {r.id: r.status for r in session.scalars(select(JobRow))} == {101: 'Shortlisted', 104: 'Shortlisted', 105: 'Shortlisted', 140: 'Shortlisted'}


def test_manual_status_transitions_and_applied_date_handling(db):
    with db.begin() as session:
        applied = set_manual_application(session, 101, 'Applied', applied_date='2024-05-01', notes='Synthetic application record')
        assert applied.applied_date == '2024-05-01'
    with db.begin() as session:
        interview = set_manual_application(session, 101, 'Interview', notes='Synthetic status transition')
        assert interview.applied_date == '2024-05-01'
    with db() as session:
        row = session.scalar(select(ManualApplicationRecord).where(ManualApplicationRecord.job_id == 101))
        assert (row.status, row.applied_date, row.notes) == ('Interview', '2024-05-01', 'Synthetic status transition')
        assert session.get(JobRow, 101).status == 'Shortlisted'


def test_invalid_manual_state_or_date_is_rejected(db):
    with db.begin() as session, pytest.raises(ValueError):
        set_manual_application(session, 101, 'Submitted')
    with db.begin() as session, pytest.raises(ValueError):
        set_manual_application(session, 101, 'Applied', applied_date='not-a-date')


def test_url_import_deduplicates_and_adds_unknown_match_fields(db):
    posting = {'title': 'Senior QA Automation Engineer', 'company': 'Example QA Employer',
               'location': 'Unknown', 'remote': 'Unknown', 'description': 'QA automation using APIs',
               'requirements': 'Unknown', 'employment_type': 'Unknown', 'salary': None, 'published': None,
               'url': 'https://jobs.example.com/careers/qa?utm_source=mail', 'source_name': 'Official employer career page',
               'platform': 'Official employer career page', 'source_id': 'manual:jobs.example.com',
               'source_type': 'manual-import', 'source_url': 'https://jobs.example.com/careers/qa?utm_source=mail'}
    with db.begin() as session:
        first = import_vacancy(session, posting['url'], posting=posting)
        second = import_vacancy(session, 'https://jobs.example.com/careers/qa', posting=posting)
        assert first['status'] == 'imported'
        assert second['status'] == 'duplicate' and second['job_id'] == first['job_id']
        imported = session.get(JobRow, first['job_id'])
        job = Job.model_validate_json(imported.payload)
        assert job.title == posting['title'] and job.company == posting['company']
        assert job.location == 'Unknown' and job.remote == 'Unknown'
        assert imported.status == 'New'
        assert session.scalar(select(ManualApplicationRecord).where(ManualApplicationRecord.job_id == imported.id)).status == 'New'


def test_import_deduplicates_against_secondary_source_posting_urls(db):
    with db.begin() as session:
        existing = session.get(JobRow, 101)
        session.add(JobObservation(job_id=existing.id, source_id=existing.source_id,
                                   identity='other-source:qa-copy',
                                   canonical_url='https://jobs.example.com/careers/qa',
                                   payload=existing.payload))
    posting = {'title': 'Senior QA Automation Engineer', 'company': 'Example QA Employer',
               'location': 'Unknown', 'remote': 'Unknown', 'description': 'QA automation',
               'requirements': 'Unknown', 'employment_type': 'Unknown', 'salary': None, 'published': None,
               'url': 'https://jobs.example.com/careers/qa', 'source_name': 'Official employer career page',
               'platform': 'Official employer career page', 'source_id': 'manual:jobs.example.com',
               'source_type': 'manual-import', 'source_url': 'https://jobs.example.com/careers/qa'}
    with db.begin() as session:
        result = import_vacancy(session, posting['url'], posting=posting)
        assert result['status'] == 'duplicate' and result['job_id'] == 101


def test_import_relevance_rejects_unrelated_industrial_roles(db):
    posting = {'title': 'Supplier Quality Engineer', 'company': 'Factory', 'location': 'Unknown', 'remote': 'Unknown',
               'description': 'Manufacturing supplier inspection', 'requirements': 'Unknown', 'employment_type': 'Unknown',
               'salary': None, 'published': None, 'url': 'https://jobs.factory.example/role/1',
               'source_name': 'Official employer career page', 'platform': 'Official employer career page',
               'source_id': 'manual:jobs.factory.example', 'source_type': 'manual-import',
               'source_url': 'https://jobs.factory.example/role/1'}
    with db.begin() as session, pytest.raises(ImportRejected):
        import_vacancy(session, posting['url'], posting=posting)


@pytest.mark.parametrize('title', [
    'Senior QA Engineer', 'Manual QA Analyst', 'QA Automation Engineer', 'Software Tester',
    'Software Test Engineer', 'SDET', 'Software Quality Engineer', 'Test Automation Engineer',
    'QA Lead', 'Payments Testing Engineer',
])
def test_software_qa_title_coverage(title):
    assert is_relevant_vacancy(title, '')


def test_technical_fintech_adjacent_roles_are_retained():
    assert is_relevant_vacancy('Payments Integration Engineer', 'Build bank payment integrations')
    assert not is_relevant_vacancy('Supplier Quality Engineer', 'Manufacturing inspection only')


def test_import_url_validation_and_linkedin_exclusion():
    with pytest.raises(ImportRejected):
        parse_bulk_urls('http://example.com/job/1')
    assert parse_bulk_urls('https://rabota.md/job/1\nhttps://rabota.md/job/1?utm_source=x') == ['https://rabota.md/job/1']
    with pytest.raises(ImportRejected):
        normalize_job_url('https://www.linkedin.com/jobs/view/123')
    with pytest.raises(ImportRejected):
        normalize_job_url('https://jobs.md/job/qa')
    with pytest.raises(ImportRejected):
        normalize_job_url('https://example.org/job/qa?session_token=private')


def test_search_results_metadata_is_not_mistaken_for_one_vacancy():
    html = '''<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
      {"@type":"JobPosting","title":"QA 1"},{"@type":"JobPosting","title":"QA 2"}]}</script>'''
    assert _job_posting_jsonld(html) is None


def test_import_respects_robots_without_retry_or_page_fetch(monkeypatch):
    import httpx
    from agent import manual_applications as importer
    class FakeClient:
        def __init__(self):
            self.urls = []
        def get(self, url, **kwargs):
            self.urls.append(url)
            return httpx.Response(200, request=httpx.Request('GET', url), text='User-agent: *\nDisallow: /')
    monkeypatch.setattr(importer, '_public_host', lambda host: True)
    client = FakeClient()
    with pytest.raises(ImportRejected, match='robots.txt'):
        fetch_vacancy('https://official.example/careers/qa-engineer', client=client)
    assert client.urls == ['https://official.example/robots.txt']


def test_privacy_patterns_cover_profile_private_folder_and_database():
    repo = Path(__file__).resolve().parents[1]
    for relative in ('candidate_profile.json', 'private/master_cv.md', 'data/jobs.db'):
        result = subprocess.run(['git', 'check-ignore', '--no-index', '-q', relative], cwd=repo)
        assert result.returncode == 0, f'{relative} must be ignored by Git'


def test_manual_tracker_app_test_saves_only_manual_state(tmp_path, monkeypatch, candidate):
    target = 'sqlite:///' + str(tmp_path / 'tracker-ui.db')
    monkeypatch.setenv('DATABASE_URL', target)
    from agent.db import database as get_database, MatchRow
    from agent.matching import evaluate
    from agent.settings import rules
    factory = get_database(target)
    with factory() as session:
        company = Company(name='Example QA Employer')
        session.add(company)
        session.flush()
        source = Source(company_id=company.id, platform='Greenhouse', board='ui-example')
        session.add(source)
        session.flush()
        job = Job(title='QA Engineer 1', company='Example QA Employer', location='Bucharest, Romania',
                  remote='Hybrid', description='Software quality assurance', url='https://boards.greenhouse.io/example/jobs/1',
                  platform='Greenhouse', source_id=str(source.id))
        row = JobRow(id=101, source_id=source.id, identity='gh:ui-101', canonical_url=str(job.url),
                     fingerprint='fp:ui-101', payload=job.model_dump_json(), status='Shortlisted')
        session.add(row)
        session.add(MatchRow(job_id=101, payload=evaluate(job, candidate, rules(), False).model_dump_json()))
        from pydantic import HttpUrl
        second = job.model_copy(update={'title': 'Manual QA Engineer', 'location': 'Sample City, Exampleland',
                                        'remote': 'Remote', 'url': HttpUrl('https://boards.greenhouse.io/example/jobs/102'),
                                        'source_id': str(source.id)})
        session.add(JobRow(id=102, source_id=source.id, identity='gh:ui-102', canonical_url=str(second.url),
                           fingerprint='fp:ui-102', payload=second.model_dump_json(), status='New'))
        session.commit()
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
    assert not app.exception
    assert any('Manual Job Application Tracker' in item.value for item in app.title)
    assert set(app.dataframe[0].value['ID'].tolist()) == {101, 102}
    next(widget for widget in app.selectbox if widget.label == 'Select vacancy to update').select(101).run()
    assert any('[QA Engineer 1]' in item.value for item in app.markdown)
    next(widget for widget in app.selectbox if widget.label == 'Application status').select('Applied').run()
    next(button for button in app.button if button.label == 'Save application progress').click().run()
    assert not app.exception
    with factory() as session:
        tracked = session.scalar(select(ManualApplicationRecord).where(ManualApplicationRecord.job_id == 101))
        assert tracked.status == 'Applied' and tracked.applied_date
        assert session.get(JobRow, 101).status == 'Shortlisted'
    next(widget for widget in app.selectbox if widget.label == 'Filter by application status').select('Applied').run()
    assert app.dataframe[0].value['ID'].tolist() == [101]
    assert any(button.label == 'Export filtered vacancies as CSV' for button in app.get('download_button'))

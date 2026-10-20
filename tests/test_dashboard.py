from pathlib import Path
import pytest
from sqlalchemy import select
from streamlit.testing.v1 import AppTest
from tests.support import discovery_dashboard_app_test
from agent.db import database, Company, Source, JobRow, MatchRow
from agent.discovery import upsert_job
from agent.models import Job
from agent.matching import evaluate, ranking_key
from agent.settings import rules


@pytest.mark.parametrize('populated', [False, True])
def test_dashboard_load_and_review(tmp_path, monkeypatch, candidate, populated):
    target = 'sqlite:///' + str(tmp_path / 'dashboard.db')
    monkeypatch.setenv('DATABASE_URL', target)
    factory = database(target)
    if populated:
        job = Job(title='QA Engineer',company='Synthetic employer',location='Moldova',remote='Remote',
                  description='Playwright SQL',url='https://example.org/qa',platform='lever',source_id='1')
        with factory() as session:
            company = Company(name=job.company)
            session.add(company); session.flush()
            source = Source(company_id=company.id,platform='lever',board='synthetic')
            session.add(source); session.flush()
            row, _ = upsert_job(session, job, source)
            session.add(MatchRow(job_id=row.id,payload=evaluate(job,candidate,rules(),False).model_dump_json()))
            # Previously stored roles excluded by current preferences remain historical.
            excluded = job.model_copy(update={'title':'Director of Quality Assurance','source_id':'old-director'})
            # A distinct URL avoids exact-URL deduplication with the QA vacancy.
            from pydantic import HttpUrl
            excluded.url = HttpUrl('https://example.org/director')
            old_row, _ = upsert_job(session,excluded,source)
            session.add(MatchRow(job_id=old_row.id,payload=evaluate(excluded,candidate,rules(),False).model_dump_json()))
            session.commit()
    app = discovery_dashboard_app_test()
    assert not app.exception
    if populated:
        assert app.metric[0].value == '1'
        table = app.dataframe[0]
        app.session_state[table.key] = {'selection': {'rows': [0], 'columns': [], 'cells': []}}
        app.run()
        assert app.session_state['selected_vacancy_id'] == int(app.dataframe[0].value.iloc[0]['ID'])
        assert app.subheader[0].value == 'QA Engineer'
        assert any(link.label == 'Open job posting' for link in app.link_button)
        next(widget for widget in app.selectbox if widget.label == 'Update review status').select('Reviewed').run()
        next(button for button in app.button if button.label == 'Save review status').click().run()
        assert not app.exception
        with factory() as session:
            assert session.scalar(select(JobRow)).status == 'Reviewed'
            assert len(list(session.scalars(select(JobRow)))) == 2
    else:
        assert any('No stored relevant vacancies' in item.value for item in app.info)


def test_remote_and_geographic_ranking():
    onsite = {'Region':'Moldova','Remote status':'Onsite','Match score':100}
    remote = {'Region':'Romania','Remote status':'Remote','Match score':80}
    assert ranking_key(remote,'Match score') < ranking_key(onsite,'Match score')
    assert ranking_key(onsite,'Geographic priority') < ranking_key(remote,'Geographic priority')
    assert ranking_key(onsite,'Match score',False) < ranking_key(remote,'Match score',False)

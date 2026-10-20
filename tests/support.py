from pathlib import Path

from agent.models import Candidate


def candidate_fixture():
    """Synthetic matching inputs; never a real candidate or production profile."""
    return Candidate(
        name='Synthetic test candidate', location='Moldova', experience_years=5,
        positions=['QA Engineer'],
        skills=['Playwright', 'JavaScript', 'API testing', 'SQL', 'Drupal', 'Regression testing'],
        projects=[], certifications=[], provenance='Synthetic test fixture only',
    )


def discovery_dashboard_app_test():
    """Open the discovery/review page when an AppTest targets the legacy view."""
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'app.py')).run(timeout=30)
    next(widget for widget in app.radio if widget.label == 'Dashboard').set_value('Vacancy discovery and review').run(timeout=30)
    return app

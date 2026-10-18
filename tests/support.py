from agent.models import Candidate


def candidate_fixture():
    """Synthetic matching inputs; never a real candidate or production profile."""
    return Candidate(
        name='Synthetic test candidate', location='Moldova', experience_years=5,
        positions=['QA Engineer'],
        skills=['Playwright', 'JavaScript', 'API testing', 'SQL', 'Drupal', 'Regression testing'],
        projects=[], certifications=[], provenance='Synthetic test fixture only',
    )

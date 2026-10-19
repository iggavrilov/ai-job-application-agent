from pathlib import Path

import pytest

from agent.cv_workflow import (
    CvValidationError, render_cv, tailor_cv, validate_cv_document,
    validate_private_git_policy,
)
from agent.models import Candidate, CandidateExperience


@pytest.fixture
def candidate():
    return Candidate(
        name="Alex Example", location="Chișinău, Moldova", experience_years=10,
        positions=["QA Engineer"], skills=["Manual QA", "Playwright", "SQL"],
        projects=[], certifications=["ISTQB Foundation"],
        education=["Bachelor’s degree in Computer Science — Example University"],
        languages=["English - B2"], github="https://github.com/example",
        work_authorization="Authorized to work in Romania", email="unknown", telephone="unknown",
        professional_summary="QA Engineer with 10 years of experience in web testing.",
        provenance="Synthetic test fixture.",
        experience=[CandidateExperience(
            company="Example Co", title="QA Engineer", dates="2020–Present",
            location="Remote", bullets=["Tested web applications.", "Automated regression checks with Playwright."],
        )],
    )


def test_rendered_master_cv_is_complete_and_general(candidate):
    cv = render_cv(candidate)
    validate_cv_document(cv, candidate)
    assert "Target employer" not in cv
    assert "Bitdefender" not in cv


def test_tailored_cv_reorders_verified_facts_without_copying_job_requirements(candidate):
    master = render_cv(candidate)
    tailored = tailor_cv(
        master, candidate, target_title="Senior QA Engineer", target_employer="Bitdefender",
        description="Python Java test automation 100% coverage Selenium API testing",
    )
    validate_cv_document(tailored, candidate)
    assert "Target position" in tailored and "Bitdefender" in tailored
    assert "Python" not in tailored and "Selenium" not in tailored and "100% coverage" not in tailored
    assert tailored.index("Playwright") < tailored.index("Manual QA")


def test_reject_internal_ai_notes(candidate):
    cv = render_cv(candidate).replace("QA Engineer with 10 years of experience in web testing.",
                                      "Draft prepared from the existing candidate profile.")
    with pytest.raises(CvValidationError, match="internal profile or AI commentary"):
        validate_cv_document(cv, candidate)


def test_reject_unsupported_skill_even_outside_skill_bullets(candidate):
    cv = render_cv(candidate).replace("## Technical skills", "Unsupported certification: AWS\n\n## Technical skills")
    with pytest.raises(CvValidationError):
        validate_cv_document(cv, candidate)


def test_reject_unsupported_education(candidate):
    cv = render_cv(candidate).replace("Bachelor’s degree in Computer Science — Example University",
                                      "Master’s degree in Computer Science — Example University")
    with pytest.raises(CvValidationError, match="Education contains missing or unsupported facts"):
        validate_cv_document(cv, candidate)


def test_reject_unsupported_certification(candidate):
    cv = render_cv(candidate).replace("- ISTQB Foundation", "- ISTQB Foundation\n- AWS Certified Solutions Architect")
    with pytest.raises(CvValidationError, match="Certifications contains missing or unsupported facts"):
        validate_cv_document(cv, candidate)


def test_reject_unsupported_experience_claim(candidate):
    cv = render_cv(candidate).replace("Tested web applications.", "Led a team of 20 engineers.")
    with pytest.raises(CvValidationError, match="Professional experience contains"):
        validate_cv_document(cv, candidate)


def test_reject_missing_essential_section(candidate):
    cv = render_cv(candidate).replace("## Certifications", "## Achievements")
    with pytest.raises(CvValidationError, match="Missing required section: Certifications"):
        validate_cv_document(cv, candidate)


def test_reject_unapproved_content_inside_skills_section(candidate):
    cv = render_cv(candidate).replace("## Education", "- Python (advanced)\n\n## Education")
    with pytest.raises(CvValidationError, match="Technical skills contain unsupported wording"):
        validate_cv_document(cv, candidate)


def test_private_outputs_are_git_ignored():
    root = Path(__file__).resolve().parents[1]
    validate_private_git_policy(root, [
        Path("candidate_profile.json"), Path("private/cv/master_cv.md"),
        Path("private/application_pilot/job-4-bitdefender-qa-engineer/cv_draft.md"),
    ])

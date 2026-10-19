from pathlib import Path

import pytest

from agent.cv_workflow import (
    CvValidationError, render_cv, tailor_cv, validate_cv_document,
    validate_private_git_policy, _section, _application_metadata,
)
from agent.models import Candidate, CandidateExperience, Job


@pytest.fixture
def candidate():
    return Candidate(
        name="Alex Example", location="Chișinău, Moldova", experience_years=10,
        professional_title="Senior Quality Assurance Engineer",
        positions=["QA Engineer"], skills=["Manual QA", "Playwright", "SQL"],
        projects=[], certifications=["ISTQB Foundation"],
        education=["Bachelor’s degree in Computer Science — Example University"],
        languages=["English - B2"], github="https://github.com/example",
        work_authorization="Authorized to work in Romania", email="alex@example.com", telephone="+37360000000",
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
    assert cv.splitlines()[:2] == ["# Alex Example", "**Senior Quality Assurance Engineer**"]
    assert "## Contact" in cv
    assert "- Location: Chișinău, Moldova" in cv
    assert "- Email: alex@example.com" in cv
    assert "- Phone: +37360000000" in cv
    assert "- GitHub: https://github.com/example" in cv
    assert "Target employer" not in cv and "Target position" not in cv
    assert "Bitdefender" not in cv
    assert "Authorized to work in Romania" not in cv


def test_tailored_cv_reorders_verified_facts_without_copying_job_requirements(candidate):
    master = render_cv(candidate)
    tailored = tailor_cv(
        master, candidate, target_title="Senior QA Engineer",
        description="Python Java test automation 100% coverage Selenium API testing",
    )
    validate_cv_document(tailored, candidate)
    assert "Target position" not in tailored and "Target employer" not in tailored
    assert "Bitdefender" not in tailored
    assert "Python" not in tailored and "Selenium" not in tailored and "100% coverage" not in tailored
    assert not __import__("re").search(r"\bJava\b", tailored, __import__("re").I)


def test_role_focus_reorders_skill_categories_without_changing_facts(candidate):
    master = render_cv(candidate)
    xebia = tailor_cv(
        master, candidate, target_title="Senior QA Automation Engineer",
        description="Playwright API Java automation framework with manual testing, regression testing and QA test cases",
    )
    bitdefender = tailor_cv(
        master, candidate, target_title="QA Engineer",
        description="Manual regression testing, exploratory testing and test cases",
    )
    def first_skill_group(document):
        lines = [line for line in _section(document, "Technical skills").splitlines() if line.strip()]
        return lines[0].split(":**", 1)[0].removeprefix("- **")
    assert first_skill_group(xebia) == "Automation"
    assert first_skill_group(bitdefender) == "Quality assurance"
    validate_cv_document(xebia, candidate)
    validate_cv_document(bitdefender, candidate)


def test_tailoring_prioritizes_relevant_outcomes_and_reduces_repeated_bullets(candidate):
    facts = [
        "Created test plans for routine feature releases.",
        "Prepared manual regression test cases for website updates.",
        "Reduced regression testing from approximately one week to a few hours through automation.",
        "Expanded automated website coverage and identified previously undetected HTTP 500 errors.",
        "Investigated defects and collaborated with developers on fixes.",
        "Verified fixes after successful deployment.",
        "Updated QA documentation for recurring release workflows.",
        "Attended regular project planning meetings.",
    ]
    first_role = candidate.experience[0].model_copy(update={"bullets": facts})
    tailored_candidate = candidate.model_copy(update={"experience": [first_role, *candidate.experience[1:]]})
    master = render_cv(tailored_candidate)
    tailored = tailor_cv(
        master, tailored_candidate, target_title="Senior QA Automation Engineer",
        description="Automated regression testing, automation coverage and regression tests",
    )
    role_section = _section(tailored, "Professional experience")
    first_role_section = role_section.split("### ", 2)[1].split("### ", 1)[0]
    emitted = [line[2:] for line in first_role_section.splitlines() if line.startswith("- ")]
    assert len(emitted) == 6
    assert emitted[0] == facts[2]
    assert facts[3] in emitted
    assert "### " + candidate.experience[0].title in role_section
    assert len([line for line in _section(master, "Professional experience").splitlines()
                if line.startswith("- ")]) == sum(len(role.bullets) for role in tailored_candidate.experience)
    validate_cv_document(tailored, tailored_candidate, require_complete_experience=False)
    with pytest.raises(CvValidationError, match="missing or unsupported profile facts"):
        validate_cv_document(tailored, tailored_candidate)
    with pytest.raises(CvValidationError, match="missing or unsupported profile facts"):
        validate_cv_document(tailored.replace(emitted[0], "Invented automation achievement."),
                             tailored_candidate, require_complete_experience=False)


def test_validator_rejects_target_labels_and_work_authorization(candidate):
    master = render_cv(candidate)
    with pytest.raises(CvValidationError, match="internal profile or AI commentary"):
        validate_cv_document(master.replace("## Contact", "Target employer: Example\n\n## Contact"), candidate)
    with pytest.raises(CvValidationError, match="Work authorization belongs"):
        validate_cv_document(master.replace("## Contact", "Authorized to work in Romania\n\n## Contact"), candidate)


def test_target_information_is_saved_only_in_private_application_metadata(candidate):
    job = Job(
        title="Senior QA Automation Engineer", company="Xebia CEE", location="Remote",
        description="Playwright QA", url="https://example.com/jobs/40", platform="test", source_id="40",
    )
    metadata = _application_metadata(40, job)
    assert metadata == {
        "job_id": 40, "employer": "Xebia CEE", "role": "Senior QA Automation Engineer",
        "application_url": "https://example.com/jobs/40",
    }
    cv = render_cv(candidate, target_title=job.title, description=job.description)
    assert "40" not in cv and "Xebia CEE" not in cv and job.title not in cv


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
        Path("private/application_pilot/job-4-bitdefender-qa-engineer/cv.pdf"),
        Path("private/application_pilot/job-4-bitdefender-qa-engineer/application_metadata.json"),
        Path("private/application_pilot/job-40-xebia-cee-senior-qa-automation-engineer/cv_draft.md"),
        Path("private/application_pilot/job-40-xebia-cee-senior-qa-automation-engineer/application_metadata.json"),
    ])

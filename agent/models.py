from datetime import datetime, timezone
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


def now():
    return datetime.now(timezone.utc).isoformat()


class CandidateExperience(BaseModel):
    company: str
    title: str
    dates: str
    location: str = ''
    bullets: list[str] = Field(default_factory=list)


class Candidate(BaseModel):
    name: str
    location: str
    experience_years: int = Field(ge=0)
    positions: list[str]
    skills: list[str]
    projects: list[str]
    certifications: list[str]
    education: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    experience: list[CandidateExperience] = Field(default_factory=list)
    professional_summary: str = ''
    professional_title: str = ''
    github: str = 'unknown'
    salary_expectations: str = 'unknown'
    citizenship: str = 'unknown'
    work_authorization: str = 'unknown'
    email: str = 'unknown'
    telephone: str = 'unknown'
    provenance: str


class Job(BaseModel):
    title: str
    company: str
    location: str = 'unknown'
    remote: Literal['Remote', 'Hybrid', 'Onsite', 'Unknown'] = 'Unknown'
    description: str
    requirements: str = 'unknown'
    employment_type: str = 'unknown'
    salary: str | None = None
    published: str | None = None
    url: HttpUrl
    platform: str
    source_id: str
    discovered: str = Field(default_factory=now)


class Assessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    technical: float = Field(ge=0, le=1)
    experience: float = Field(ge=0, le=1)
    additional: float = Field(ge=0, le=1)
    matching_skills: list[str]
    missing_skills: list[str]
    mandatory_requirements: list[str]
    uncertain_mandatory: bool
    explanation: str


class Match(BaseModel):
    score: float = Field(ge=0, le=100)
    matching_skills: list[str]
    missing_skills: list[str]
    mandatory_requirements: list[str]
    eligibility: Literal['Eligible', 'Needs Review', 'Ineligible']
    explanation: str
    recommendation: Literal['Strong Match', 'Good Match', 'Needs Review', 'Not Recommended']
    method: str
    # Defaults permit reading historical matches; legacy rows are reassessed by the UI.
    professional_score: float | None = Field(default=None, ge=0, le=100)
    professional_fit: Literal['Strong Match', 'Good Match', 'Needs Review', 'Not Recommended'] | None = None
    professional_reason: str = ''
    geographic_reason: str = ''
    geographic_priority: int = Field(default=5, ge=0)
    in_main_recommendations: bool = False
    exclusion_reasons: list[str] = Field(default_factory=list)
    version: str = 'mvp-01.5'

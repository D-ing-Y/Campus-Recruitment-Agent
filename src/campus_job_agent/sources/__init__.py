"""Source adapters and deterministic role-profile processing for v0.5."""

from campus_job_agent.sources.adapters import (
    FixtureExperienceAdapter,
    FixtureOfficialAdapter,
    FixtureRecruitmentAdapter,
    MeituanOfficialCareersAdapter,
    NowcoderExperienceAdapter,
    OfficialCareersAdapter,
    SourceAdapterRegistry,
    ZhaopinJobsAdapter,
)
from campus_job_agent.sources.credential_store import LocalCredentialStore
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.sources.role_gates import (
    assess_role_detail_evidence,
    classify_role_family,
    experience_link_applies,
    link_experience_scope,
)

__all__ = [
    "FixtureExperienceAdapter",
    "FixtureOfficialAdapter",
    "FixtureRecruitmentAdapter",
    "MeituanOfficialCareersAdapter",
    "NowcoderExperienceAdapter",
    "OfficialCareersAdapter",
    "SourceAdapterRegistry",
    "ZhaopinJobsAdapter",
    "LocalCredentialStore",
    "SQLiteRoleRepository",
    "assess_role_detail_evidence",
    "classify_role_family",
    "experience_link_applies",
    "link_experience_scope",
]

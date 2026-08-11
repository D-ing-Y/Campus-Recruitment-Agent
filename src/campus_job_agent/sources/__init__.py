"""Source adapters and deterministic role-profile processing for v0.5."""

from campus_job_agent.sources.adapters import (
    FixtureExperienceAdapter,
    FixtureOfficialAdapter,
    FixtureRecruitmentAdapter,
    MeituanOfficialCareersAdapter,
    NowcoderExperienceAdapter,
    OfficialCareersAdapter,
    SourceAdapterRegistry,
    SourceDetailAdapter,
    XiaohongshuExperienceAdapter,
    ZhaopinJobsAdapter,
)
from campus_job_agent.sources.credential_store import LocalCredentialStore
from campus_job_agent.sources.repository import SQLiteRoleRepository
from campus_job_agent.sources.role_intelligence import (
    CommunityEvidenceExtractor,
    COMMUNITY_SOURCE_CASCADES,
    ROLE_FAMILY_DISPLAY_NAMES,
    build_community_search_plan,
    build_community_search_query,
    build_company_role_groups,
    ensure_community_body_fragment,
    materialize_community_evidence,
    role_family_display_name,
)
from campus_job_agent.sources.role_intelligence_projection import (
    DemandReputationProjector,
    EvidenceUsageViolation,
    official_escalation_for_job,
    select_consumer_inputs,
)
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
    "SourceDetailAdapter",
    "XiaohongshuExperienceAdapter",
    "ZhaopinJobsAdapter",
    "LocalCredentialStore",
    "SQLiteRoleRepository",
    "CommunityEvidenceExtractor",
    "COMMUNITY_SOURCE_CASCADES",
    "ROLE_FAMILY_DISPLAY_NAMES",
    "DemandReputationProjector",
    "EvidenceUsageViolation",
    "build_community_search_plan",
    "build_community_search_query",
    "build_company_role_groups",
    "ensure_community_body_fragment",
    "materialize_community_evidence",
    "role_family_display_name",
    "official_escalation_for_job",
    "select_consumer_inputs",
    "assess_role_detail_evidence",
    "classify_role_family",
    "experience_link_applies",
    "link_experience_scope",
]

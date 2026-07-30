"""Shared closed-world taxonomy with open-world resume label preservation."""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


CapabilityLevel = Literal[
    "unknown", "beginner", "intermediate", "advanced", "expert"
]
CapabilityId = Literal[
    "programming.python",
    "database.sql",
    "ai.llm",
    "ai.rag",
    "agent.langgraph",
    "engineering.backend",
    "cs.algorithms",
    "systems.distributed",
]
ExperienceKind = Literal[
    "employment",
    "internship",
    "research",
    "project",
    "competition",
    "leadership",
    "campus_activity",
    "volunteering",
    "entrepreneurship",
    "teaching",
    "training",
    "other",
]
ExperienceContext = Literal[
    "employment",
    "internship",
    "coursework",
    "capstone",
    "thesis",
    "academic_research",
    "public_funded_research",
    "industry_collaboration",
    "personal",
    "open_source",
    "competition",
    "campus",
    "volunteering",
    "entrepreneurship",
    "training",
    "community",
    "other",
    "unspecified",
]

CAPABILITY_LEVELS = {
    "unknown", "beginner", "intermediate", "advanced", "expert"
}
CAPABILITY_IDS = {
    "programming.python", "database.sql", "ai.llm", "ai.rag",
    "agent.langgraph", "engineering.backend", "cs.algorithms",
    "systems.distributed",
}
EXPERIENCE_KINDS = {
    "employment", "internship", "research", "project", "competition",
    "leadership", "campus_activity", "volunteering", "entrepreneurship",
    "teaching", "training", "other",
}
EXPERIENCE_CONTEXTS = {
    "employment", "internship", "coursework", "capstone", "thesis",
    "academic_research", "public_funded_research", "industry_collaboration",
    "personal", "open_source", "competition", "campus", "volunteering",
    "entrepreneurship", "training", "community", "other", "unspecified",
}


class CapabilityClaimValue(BaseModel):
    """Canonical proficiency plus the exact wording present in the material."""

    level: CapabilityLevel = Field(
        description="Canonical level; use unknown when the source does not support one."
    )
    raw_label: str = Field(
        min_length=1, description="Exact skill label from the supplied evidence."
    )
    raw_level: str | None = Field(
        default=None,
        description="Exact proficiency wording when it differs from the canonical level.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_open_level(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = {"level": value, "raw_label": "unknown"}
        elif not isinstance(value, dict):
            return value
        data = dict(value)
        raw = str(data.get("level") or "unknown").strip()
        canonical = canonical_capability_level(raw)
        if canonical == "unknown" and _key(raw) != "unknown":
            data.setdefault("raw_level", raw)
        data["level"] = canonical
        label = data.get("raw_label") or data.get("label") or data.get("name")
        data["raw_label"] = str(label or "unknown").strip() or "unknown"
        return data


class ExperienceKindValue(BaseModel):
    """Stable experience category plus context and source-faithful wording."""

    kind: ExperienceKind = Field(
        description="Stable high-level experience category used by downstream policy."
    )
    context: ExperienceContext = Field(
        default="unspecified",
        description="Where the experience occurred, such as coursework or internship.",
    )
    raw_label: str = Field(
        min_length=1,
        description="Exact resume section/type wording; never replace unknown text with an invention.",
    )
    raw_context: str | None = Field(
        default=None,
        description="Exact context wording when it cannot be represented without loss.",
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_open_kind(cls, value: Any) -> Any:
        if isinstance(value, str):
            value = {"kind": value, "raw_label": value}
        elif not isinstance(value, dict):
            return value
        data = dict(value)
        raw_kind = str(
            data.get("raw_label") or data.get("label") or data.get("kind") or "other"
        ).strip()
        data["raw_label"] = raw_kind or "other"
        data["kind"] = canonical_experience_kind(data.get("kind") or raw_kind)

        raw_context = str(data.get("context") or "").strip()
        context = canonical_experience_context(raw_context)
        if context == "unspecified":
            context = infer_experience_context(raw_kind)
        elif context == "other" and raw_context:
            data.setdefault("raw_context", raw_context)
        data["context"] = context
        return data


def canonical_capability_level(value: Any) -> str:
    key = _key(value)
    aliases = {
        "unknown": "unknown", "未知": "unknown", "未说明": "unknown",
        "beginner": "beginner", "basic": "beginner", "entry": "beginner",
        "入门": "beginner", "基础": "beginner", "了解": "beginner",
        "intermediate": "intermediate", "familiar": "intermediate",
        "中级": "intermediate", "熟悉": "intermediate",
        "advanced": "advanced", "skilled": "advanced", "高级": "advanced",
        "熟练": "advanced",
        "expert": "expert", "master": "expert", "专家": "expert", "精通": "expert",
    }
    return aliases.get(key, "unknown")


def canonical_experience_kind(value: Any) -> str:
    key = _key(value)
    aliases = {
        "employment": "employment", "work": "employment", "job": "employment",
        "工作": "employment", "工作经历": "employment", "全职经历": "employment",
        "兼职经历": "employment", "自由职业": "employment",
        "internship": "internship", "intern": "internship", "实习": "internship",
        "实习经历": "internship",
        "research": "research", "research experience": "research",
        "科研": "research", "科研经历": "research", "研究经历": "research",
        "project": "project", "project experience": "project", "项目": "project",
        "项目经历": "project", "课程项目": "project", "课程设计": "project",
        "课设": "project", "毕业设计": "project", "毕设": "project",
        "横向项目": "project", "实习项目": "project", "工作项目": "project",
        "个人项目": "project", "开源项目": "project",
        "competition": "competition", "竞赛": "competition", "竞赛经历": "competition",
        "比赛经历": "competition",
        "leadership": "leadership", "领导力经历": "leadership", "学生干部": "leadership",
        "学生工作": "leadership",
        "campus activity": "campus_activity", "校园活动": "campus_activity",
        "校园经历": "campus_activity", "社团经历": "campus_activity",
        "volunteering": "volunteering", "volunteer": "volunteering",
        "志愿服务": "volunteering", "志愿经历": "volunteering", "公益经历": "volunteering",
        "entrepreneurship": "entrepreneurship", "创业": "entrepreneurship",
        "创业经历": "entrepreneurship",
        "teaching": "teaching", "教学经历": "teaching", "助教经历": "teaching",
        "training": "training", "培训经历": "training", "实训经历": "training",
        "实践训练": "training", "other": "other", "其他": "other", "其他经历": "other",
    }
    if key in aliases:
        return aliases[key]
    for token, result in (
        ("横向", "project"), ("纵向", "research"),
        ("项目", "project"), ("科研", "research"), ("研究", "research"),
        ("实习", "internship"), ("工作", "employment"), ("竞赛", "competition"),
        ("比赛", "competition"), ("志愿", "volunteering"), ("公益", "volunteering"),
        ("创业", "entrepreneurship"), ("教学", "teaching"), ("助教", "teaching"),
        ("实训", "training"), ("校园", "campus_activity"), ("社团", "campus_activity"),
    ):
        if token in key:
            return result
    return "other"


def canonical_experience_context(value: Any) -> str:
    key = _key(value)
    aliases = {
        "": "unspecified", "unspecified": "unspecified", "未说明": "unspecified",
        "employment": "employment", "工作": "employment", "工作项目": "employment",
        "internship": "internship", "实习": "internship", "实习项目": "internship",
        "coursework": "coursework", "course project": "coursework", "课程": "coursework",
        "课程项目": "coursework", "课程设计": "coursework", "课设": "coursework",
        "capstone": "capstone", "毕业设计": "capstone", "毕设": "capstone",
        "thesis": "thesis", "学位论文": "thesis", "毕业论文": "thesis", "论文": "thesis",
        "academic research": "academic_research", "academic_research": "academic_research",
        "学术科研": "academic_research", "实验室科研": "academic_research",
        "public funded research": "public_funded_research",
        "public_funded_research": "public_funded_research", "纵向": "public_funded_research",
        "纵向课题": "public_funded_research", "纵向项目": "public_funded_research",
        "industry collaboration": "industry_collaboration",
        "industry_collaboration": "industry_collaboration", "横向": "industry_collaboration",
        "横向项目": "industry_collaboration", "企业委托": "industry_collaboration",
        "校企合作": "industry_collaboration",
        "personal": "personal", "个人": "personal", "个人项目": "personal",
        "open source": "open_source", "open_source": "open_source", "开源": "open_source",
        "开源项目": "open_source", "competition": "competition", "竞赛": "competition",
        "campus": "campus", "校园": "campus", "社团": "campus",
        "volunteering": "volunteering", "志愿": "volunteering", "公益": "volunteering",
        "entrepreneurship": "entrepreneurship", "创业": "entrepreneurship",
        "training": "training", "培训": "training", "实训": "training",
        "community": "community", "社区": "community", "社会实践": "community",
        "other": "other", "其他": "other",
    }
    return aliases.get(key, "other" if key else "unspecified")


def infer_experience_context(raw_label: str) -> str:
    key = _key(raw_label)
    for tokens, context in (
        (("纵向",), "public_funded_research"),
        (("横向", "企业委托", "校企合作"), "industry_collaboration"),
        (("课程设计", "课程项目", "课设"), "coursework"),
        (("毕业设计", "毕设"), "capstone"),
        (("学位论文", "毕业论文"), "thesis"),
        (("实验室", "学术科研"), "academic_research"),
        (("开源",), "open_source"), (("实习",), "internship"),
        (("工作",), "employment"), (("个人",), "personal"),
        (("竞赛", "比赛"), "competition"), (("校园", "社团"), "campus"),
        (("志愿", "公益"), "volunteering"), (("创业",), "entrepreneurship"),
        (("实训", "培训"), "training"), (("社区", "社会实践"), "community"),
    ):
        if any(token in key for token in tokens):
            return context
    return "unspecified"


def _key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s_-]+", " ", text)

"""PII-safe resume extraction through the shared structured-output gateway."""

from __future__ import annotations

import re
from typing import Any

from campus_job_agent.llm import LLMCache, LLMProvider, parse_structured_output
from campus_job_agent.prompts import (
    RESUME_PROMPT_NAME,
    RESUME_PROMPT_VERSION,
    RESUME_SCHEMA_VERSION,
    build_resume_extractor_messages,
    build_resume_retry_messages,
)
from campus_job_agent.schemas import (
    EvidenceFragment,
    LLMCallRecord,
    LLMConfig,
    PersonalInformation,
    ResumeExtractionBatch,
)


class ResumeEvidenceExtractor:
    def __init__(self, config: LLMConfig, provider: LLMProvider, cache: LLMCache) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache

    def extract(
        self, *, candidate_id: str, fragments: list[EvidenceFragment]
    ) -> tuple[PersonalInformation, ResumeExtractionBatch, list[LLMCallRecord], list[EvidenceFragment]]:
        personal = extract_personal_information(fragments)
        redacted = redact_personal_information(fragments, personal)
        allowed = {item.fragment_id for item in fragments}

        batch, calls = parse_structured_output(
            messages=build_resume_extractor_messages(redacted, candidate_id),
            output_model=ResumeExtractionBatch,
            config=self.config,
            provider=self.provider,
            cache=self.cache,
            prompt_name=RESUME_PROMPT_NAME,
            prompt_version=RESUME_PROMPT_VERSION,
            schema_version=RESUME_SCHEMA_VERSION,
            retry_builder=lambda previous, error: build_resume_retry_messages(
                redacted, candidate_id, previous, error
            ),
        )
        referenced = _batch_fragment_ids(batch)
        if not referenced <= allowed:
            raise ValueError("resume extraction references an out-of-scope fragment")
        return personal, batch, calls, redacted


_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_WECHAT = re.compile(r"(?:微信(?:号)?|wechat)\s*[:：]?\s*([A-Za-z][-_A-Za-z0-9]{5,19})", re.I)
_BIRTH = re.compile(r"(?:出生(?:年月|日期)?|生日)\s*[:：]?\s*((?:19|20)\d{2}[./年-]\d{1,2}(?:[./月-]\d{1,2})?)")
_GENDER = re.compile(r"(?:性别\s*[:：]?\s*)?(男|女)(?:士)?")
_NAME = re.compile(r"(?:姓名\s*[:：]\s*)([\u4e00-\u9fff·]{2,8})")
_JOB_STATUS = re.compile(
    r"(?:当前求职状态|求职状态)\s*[:：]?\s*([^\n|]{2,24})|"
    r"(在校[-—－]?考虑机会|在职[-—－]?考虑机会|离职[-—－]?随时到岗|应届生)"
)
_IDENTITY = re.compile(r"(?:牛人身份|身份)\s*[:：]?\s*([^\n|]{1,20})")
_BIRTHPLACE = re.compile(r"(?:出生地|籍贯)\s*[:：]?\s*([^\n|]{2,30})")
_UNLABELED_BIRTH = re.compile(
    r"(?<!\d)((?:19|20)\d{2}[-./]\d{1,2}(?:[-./]\d{1,2})?)(?!\d)"
)
_SECTION_HEADING = re.compile(
    r"(?m)^\s*(?:教育背景|教育经历|工作经历|实习经历|项目经历|科研经历|"
    r"专业技能|技能特长|荣誉证书|个人优势|期望职位)\s*$"
)
_PERSONAL_LINE = re.compile(
    r"(?:姓名|性别|出生(?:年月|日期|地)?|生日|籍贯|电话|手机|邮箱|微信(?:号)?|"
    r"当前求职状态|求职状态|牛人身份|身份)\s*[:：]",
    re.I,
)


def extract_personal_information(fragments: list[EvidenceFragment]) -> PersonalInformation:
    text = "\n".join(item.text for item in fragments)
    header = _personal_header(text)
    phone = _first_group(_PHONE, text, whole=True)
    email = _first_group(_EMAIL, text, whole=True)
    wechat = _first_group(_WECHAT, text)
    birth = _first_group(_BIRTH, header) or _first_group(
        _UNLABELED_BIRTH, header
    )
    gender = _first_group(_GENDER, header)
    name = _first_group(_NAME, header)
    job_status = _first_nonempty_group(_JOB_STATUS, text)
    identity = _first_group(_IDENTITY, text)
    birthplace = _first_group(_BIRTHPLACE, header)
    if not name:
        for line in (value.strip() for value in text.splitlines() if value.strip()):
            if re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", line) and line not in {
                "个人信息", "个人优势", "项目经历", "教育经历", "专业技能", "工作经历",
            }:
                name = line
                break
    if not birthplace:
        birthplace = _unlabeled_birthplace(header, gender=gender, name=name)
    return PersonalInformation(
        name=name, gender=gender, birth_date=birth,
        job_search_status=job_status, identity=identity, birthplace=birthplace,
        phone=phone, wechat=wechat, email=email,
    )


def redact_personal_information(
    fragments: list[EvidenceFragment], personal: PersonalInformation
) -> list[EvidenceFragment]:
    replacements = {
        value: f"[REDACTED_{field.upper()}]"
        for field, value in personal.model_dump().items()
        if isinstance(value, str) and value.strip()
    }
    result: list[EvidenceFragment] = []
    for fragment in fragments:
        lines = []
        for line in fragment.text.splitlines(keepends=True):
            ending = "\n" if line.endswith("\n") else ""
            if _PERSONAL_LINE.search(line):
                lines.append("[REDACTED_PERSONAL_INFORMATION_LINE]" + ending)
            else:
                lines.append(line)
        text = "".join(lines)
        text = _PHONE.sub("[REDACTED_PHONE]", text)
        text = _EMAIL.sub("[REDACTED_EMAIL]", text)
        text = _WECHAT.sub("微信号：[REDACTED_WECHAT]", text)
        for value, marker in sorted(replacements.items(), key=lambda item: -len(item[0])):
            text = text.replace(value, marker)
        result.append(fragment.model_copy(update={"text": text}))
    return result


def _first_group(pattern: re.Pattern[str], text: str, *, whole: bool = False) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return match.group(0 if whole else 1).strip()


def _first_nonempty_group(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return next(
        (value.strip() for value in match.groups() if value and value.strip()),
        None,
    )


def _personal_header(text: str) -> str:
    match = _SECTION_HEADING.search(text)
    return text[:match.start()] if match is not None else text[:600]


def _unlabeled_birthplace(
    header: str, *, gender: str | None, name: str | None
) -> str | None:
    lines = [value.strip() for value in header.splitlines() if value.strip()]
    if not gender:
        return None
    for index, line in enumerate(lines):
        tokens = line.split()
        if gender not in tokens and line != gender:
            continue
        candidates = tokens[tokens.index(gender) + 1:] if gender in tokens else []
        candidates.extend(lines[index + 1:index + 3])
        for value in candidates:
            value = value.strip("|｜,- ")
            if (
                value != name
                and re.fullmatch(r"[\u4e00-\u9fff]{2,12}", value)
                and value not in {"男", "女", "应届生", "学生"}
            ):
                return value
    return None


def _batch_fragment_ids(batch: ResumeExtractionBatch) -> set[str]:
    values: set[str] = set(batch.personal_advantage.evidence_fragment_ids)
    values.update(batch.professional_skills.evidence_fragment_ids)
    for field in (
        "career_expectations", "work_experiences", "project_experiences",
        "education_experiences", "custom_sections",
    ):
        for item in getattr(batch, field):
            values.update(item.evidence_fragment_ids)
    return values


__all__ = [
    "ResumeEvidenceExtractor", "extract_personal_information",
    "redact_personal_information",
]

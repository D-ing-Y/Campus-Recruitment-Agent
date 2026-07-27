"""Opt-in real structured MatchExplanation smoke; writes only sanitized output."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from campus_job_agent.llm import LLMCache, OpenAICompatibleProvider, load_llm_config
from campus_job_agent.schemas import ComparisonEntry, ComparisonSet, GapAssessment
from campus_job_agent.workflows.profile_matching.explanation import (
    LLMMatchExplanationProvider,
    explain_with_fallback,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="v06-deepseek-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--output-root", type=Path, default=Path("data/runs"))
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    _load_env(args.env_file)
    os.environ["CAMPUS_AGENT_LLM_PROVIDER"] = "openai_compatible"
    config = load_llm_config().model_copy(update={"cache_enabled": False, "max_retries": 1, "temperature": 0})
    provider = LLMMatchExplanationProvider(
        config=config,
        provider=OpenAICompatibleProvider(config),
        cache=LLMCache(str(args.output_root / args.run_id / "cache")),
    )
    assessment = GapAssessment(
        assessment_id="smoke-gap", schema_version="v0.6", input_set_id="smoke-input",
        candidate_profile_snapshot_id="candidate-smoke", career_intent_snapshot_id="intent-smoke",
        role_profile_snapshot_id="role-smoke", job_instance_profile_snapshot_id="role-smoke",
        hard_constraint_status="passed",
        core_coverage={"total_weight": 4.0, "eligible_weight": 3.0, "covered_weight": 2.0, "uncertain_weight": 1.0, "coverage": 0.666667},
        bonus_coverage={"total_weight": 0.5, "eligible_weight": 0, "covered_weight": 0, "uncertain_weight": 0.5, "coverage": None},
        fact_index={
            "fact:smoke-gap:hard": {"kind": "hard_status", "value": "passed"},
            "fact:smoke-gap:core": {"kind": "coverage", "value": {"eligible_weight": 3.0, "covered_weight": 2.0, "uncertain_weight": 1.0, "coverage": 0.666667}},
            "fact:smoke-gap:bonus": {"kind": "coverage", "value": {"eligible_weight": 0, "covered_weight": 0, "uncertain_weight": 0.5, "coverage": None}},
        },
        status="current",
    )
    comparison = ComparisonSet(
        comparison_set_id="smoke-comparison", input_set_id="smoke-input", canonical_hash="smoke",
        entries=[ComparisonEntry(
            job_instance_profile_snapshot_id="role-smoke", gap_assessment_id="smoke-gap",
            recommended_tier="needs_clarification", hard_rank=0,
            blocking_preference_conflict_count=0, core_coverage=0.666667,
            uncertainty_weight=1.5, stable_tie_breaker="role-smoke",
        )],
    )
    explanation, calls, error = explain_with_fallback(comparison, {"smoke-gap": assessment}, provider)
    passed = error is None and bool(calls)
    summary = {
        "run_id": args.run_id, "executed_at": datetime.now(UTC).isoformat(),
        "provider": "openai_compatible", "model": config.model,
        "passed": passed, "fallback_used": error is not None,
        "error": error, "explanation": explanation.model_dump(mode="json"),
        "llm_calls": [item.model_dump(mode="json") for item in calls],
    }
    run_dir = args.output_root / args.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    output = run_dir / "match_explanation_smoke.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "run_id": args.run_id, "passed": passed, "fallback_used": error is not None,
        "provider": summary["provider"], "model": summary["model"], "summary_path": str(output),
    }, ensure_ascii=False))
    return 0 if passed else 1


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


if __name__ == "__main__":
    raise SystemExit(main())

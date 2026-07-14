"""File-based LLM cache."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class LLMCache:
    def __init__(self, cache_dir: str = "data/cache/llm") -> None:
        self.cache_dir = Path(cache_dir)

    def make_cache_key(
        self,
        provider: str,
        model: str,
        prompt_name: str,
        prompt_version: str,
        schema_version: str,
        messages: list[dict[str, str]],
    ) -> str:
        payload = {
            "provider": provider,
            "model": model,
            "prompt_name": prompt_name,
            "prompt_version": prompt_version,
            "schema_version": schema_version,
            "messages": messages,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def read(self, cache_key: str) -> tuple[dict[str, Any] | None, str | None]:
        path = self.cache_dir / f"{cache_key}.json"
        if not path.exists():
            return None, None
        try:
            return json.loads(path.read_text(encoding="utf-8")), None
        except Exception as exc:
            return None, f"cache_error: {exc}"

    def write(
        self,
        cache_key: str,
        provider: str,
        model: str,
        prompt_name: str,
        prompt_version: str,
        schema_version: str,
        raw_output: str,
        parsed_json: dict[str, Any],
        usage: dict[str, Any] | None,
    ) -> str | None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            value = {
                "cache_key": cache_key,
                "created_at": datetime.now(UTC).isoformat(),
                "provider": provider,
                "model": model,
                "prompt_name": prompt_name,
                "prompt_version": prompt_version,
                "schema_version": schema_version,
                "raw_output": raw_output,
                "parsed_json": parsed_json,
                "usage": usage,
            }
            (self.cache_dir / f"{cache_key}.json").write_text(
                json.dumps(value, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            return f"cache_error: {exc}"
        return None

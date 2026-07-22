"""Command-line entrypoint for local agent and credential operations."""

from __future__ import annotations

import argparse
from pathlib import Path


DEFAULT_CREDENTIAL_ROOT = Path("data") / "cache" / "credentials"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="campus-agent")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run")
    run_parser.add_argument("user_input")

    auth_parser = commands.add_parser("auth", help="manage local source credentials")
    auth_commands = auth_parser.add_subparsers(dest="auth_command", required=True)
    chrome_parser = auth_commands.add_parser(
        "import-chrome", help="import domain-scoped cookies from the local Chrome profile"
    )
    chrome_parser.add_argument(
        "--source",
        required=True,
        choices=("zhaopin", "zhaopin_jobs", "nowcoder", "nowcoder_experience"),
    )
    chrome_parser.add_argument("--name", default="default")
    chrome_parser.add_argument("--profile", help="optional Chrome Cookies database path")
    chrome_parser.add_argument(
        "--credential-root", type=Path, default=DEFAULT_CREDENTIAL_ROOT
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "auth" and args.auth_command == "import-chrome":
        return _import_chrome(args)
    if args.command == "run":
        return _run_agent(args.user_input)
    return 1


def _import_chrome(args: argparse.Namespace) -> int:
    from campus_job_agent.sources import LocalCredentialStore

    source_id = {
        "zhaopin": "zhaopin_jobs",
        "zhaopin_jobs": "zhaopin_jobs",
        "nowcoder": "nowcoder_experience",
        "nowcoder_experience": "nowcoder_experience",
    }[args.source]
    try:
        ref = LocalCredentialStore(args.credential_root).import_chrome(
            source_id=source_id,
            name=args.name,
            cookie_file=args.profile,
        )
    except Exception as exc:
        # Import failures are deliberately sanitized by the store and never contain cookies.
        print("status: failed")
        print(f"error: {exc}")
        return 1
    print("status: success")
    print(f"source_id: {ref.source_id}")
    print(f"credential_ref: {ref.credential_ref}")
    print(f"credential_root: {Path(args.credential_root).resolve()}")
    return 0


def _run_agent(user_input: str) -> int:
    from campus_job_agent.agent import run_agent

    try:
        state = run_agent(user_input)
    except Exception as exc:
        print("status: failed")
        print(f"error: {exc}")
        return 1
    run_id = state["run_id"]
    verification = state.get("verification", {})
    status = "success" if verification.get("passed") else "failed"
    print(f"run_id: {run_id}")
    print(f"status: {status}")
    print(f"report_path: {state.get('report_path')}")
    print(f"trace_path: data/runs/{run_id}/trace.json")
    if state.get("llm_calls") is not None:
        print(f"llm_calls_path: data/runs/{run_id}/llm_calls.json")
    if status == "failed" and state.get("errors"):
        print(f"errors: {state['errors']}")
    return 0 if status == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())

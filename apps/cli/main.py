"""v0.1 CLI entrypoint."""

import argparse
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from campus_job_agent.agent import run_agent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(prog="campus-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("user_input")

    args = parser.parse_args()

    if args.command == "run":
        try:
            state = run_agent(args.user_input)
        except Exception as exc:
            print(f"status: failed")
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

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

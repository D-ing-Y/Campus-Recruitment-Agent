"""Single production CLI for the observable runtime and legacy compatibility."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4


class CLIArgumentError(ValueError):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CLIArgumentError(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="campus-agent")
    parser.add_argument("--json", action="store_true", dest="json_output", help="emit one JSON document")
    parser.add_argument("--data-root", type=Path, help="explicit cwd-independent data root")
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("ui", help="open the project CLI UI")

    commands.add_parser("doctor", help="check runtime paths and dependencies without exposing secrets")

    session = commands.add_parser("session", help="manage lightweight RunSession navigation")
    session_commands = session.add_subparsers(dest="session_command", required=True)
    start = session_commands.add_parser("start")
    start.add_argument("--user-id", default="local-user")
    start.add_argument("--idempotency-key")
    status = session_commands.add_parser("status")
    status.add_argument("session_id")
    resume = session_commands.add_parser("resume")
    resume.add_argument("session_id")
    resume.add_argument("--expected-version", type=int)
    history = session_commands.add_parser("history")
    history.add_argument("session_id")
    history.add_argument("--user-id")

    inspect = commands.add_parser("inspect", help="inspect stable run and repository contracts")
    inspect_commands = inspect.add_subparsers(dest="inspect_command", required=True)
    run = inspect_commands.add_parser("run")
    run.add_argument("run_id")
    node = inspect_commands.add_parser("node")
    node.add_argument("run_id")
    node.add_argument("--node")
    llm = inspect_commands.add_parser("llm")
    llm.add_argument("run_id")
    evidence = inspect_commands.add_parser("evidence")
    evidence.add_argument("object_id")
    claims = inspect_commands.add_parser("claims")
    claims.add_argument("subject_id")
    profile = inspect_commands.add_parser("profile")
    profile.add_argument("snapshot_id")
    handoff = inspect_commands.add_parser("handoff")
    handoff.add_argument("handoff_id", nargs="?")
    handoff.add_argument("--run-id")
    handoff.add_argument("--session-id")

    resume_evidence = commands.add_parser("resume", help="import and confirm structured resume evidence")
    resume_commands = resume_evidence.add_subparsers(dest="resume_command", required=True)
    resume_import = resume_commands.add_parser("import")
    resume_import.add_argument("session_id")
    resume_import.add_argument("--candidate-id", required=True)
    resume_import.add_argument("--input", required=True)
    resume_import.add_argument(
        "--reparse", action="store_true",
        help="create a new draft version from the same PDF artifact",
    )
    resume_resume = resume_commands.add_parser("resume")
    resume_resume.add_argument("session_id")
    resume_resume.add_argument(
        "--action", choices=("confirm", "correct", "remove", "retry", "cancel")
    )
    resume_resume.add_argument("--response-id")
    resume_resume.add_argument("--patch")
    resume_show = resume_commands.add_parser("show")
    resume_show.add_argument("object_id")

    candidate = commands.add_parser("candidate", help="build and resume CandidateProfileGraph")
    candidate_commands = candidate.add_subparsers(dest="candidate_command", required=True)
    candidate_build = candidate_commands.add_parser("build")
    candidate_build.add_argument("session_id")
    candidate_build.add_argument("--candidate-id", required=True)
    candidate_build.add_argument("--resume-evidence", required=True)
    candidate_resume = candidate_commands.add_parser("resume")
    candidate_resume.add_argument("session_id")
    candidate_resume.add_argument(
        "--action", required=True,
        choices=("answer", "upload", "correct", "confirm", "skip", "cancel"),
    )
    candidate_resume.add_argument("--response-id", required=True)
    candidate_resume.add_argument("--answer", action="append", default=[])
    candidate_resume.add_argument("--upload", action="append", default=[])
    candidate_resume.add_argument("--correction", action="append", default=[])
    candidate_resume.add_argument("--skip-id", action="append", default=[])
    candidate_show = candidate_commands.add_parser("show")
    candidate_show.add_argument("snapshot_id")
    candidate_diff = candidate_commands.add_parser("diff")
    candidate_diff.add_argument("from_snapshot_id")
    candidate_diff.add_argument("to_snapshot_id")

    intent = commands.add_parser("intent", help="create and confirm CareerIntent")
    intent_commands = intent.add_subparsers(dest="intent_command", required=True)
    intent_create = intent_commands.add_parser("create")
    intent_create.add_argument("session_id")
    intent_create.add_argument("--text", required=True)
    intent_resume = intent_commands.add_parser("resume")
    intent_resume.add_argument("session_id")
    intent_resume.add_argument("--action", required=True, choices=("confirm", "revise", "cancel"))
    intent_resume.add_argument("--response-id", required=True)
    intent_resume.add_argument("--patch")
    intent_show = intent_commands.add_parser("show")
    intent_show.add_argument("snapshot_id")

    role = commands.add_parser("role", help="research role demand and reputation evidence")
    role_commands = role.add_subparsers(dest="role_command", required=True)
    role_research = role_commands.add_parser("research")
    role_research.add_argument("session_id")
    role_research.add_argument("--handoff", required=True)
    role_resume = role_commands.add_parser("resume")
    role_resume.add_argument("session_id")
    role_resume.add_argument(
        "--action", required=True,
        choices=("authorized", "skip-source", "cancel"),
    )
    role_resume.add_argument("--response-id", required=True)
    role_resume.add_argument("--credential-ref")
    role_resume.add_argument("--browser-profile-ref")
    role_show = role_commands.add_parser("show")
    role_show.add_argument("bundle_id")

    model = commands.add_parser("model", help="manage CC Switch-style model providers")
    model_commands = model.add_subparsers(dest="model_command", required=True)
    model_add = model_commands.add_parser("add")
    model_add.add_argument("--preset", required=True, choices=("deepseek", "openai-compatible", "mock"))
    model_add.add_argument("--id", required=True, dest="profile_id")
    model_add.add_argument("--name", required=True)
    model_add.add_argument("--base-url")
    model_add.add_argument("--model")
    model_add.add_argument("--timeout-seconds", type=float)
    model_add.add_argument("--category")
    model_add.add_argument("--website-url")
    model_add.add_argument("--notes")
    model_add.add_argument("--icon")
    model_add.add_argument("--api-key-stdin", action="store_true")
    model_add.add_argument("--api-key", help=argparse.SUPPRESS)
    model_add.add_argument("--activate", action="store_true")
    model_commands.add_parser("list")
    model_edit = model_commands.add_parser("edit")
    model_edit.add_argument("profile_id")
    model_edit.add_argument("--name")
    model_edit.add_argument("--base-url")
    model_edit.add_argument("--model")
    model_edit.add_argument("--timeout-seconds", type=float)
    model_edit.add_argument("--category")
    model_edit.add_argument("--website-url")
    model_edit.add_argument("--notes")
    model_edit.add_argument("--icon")
    model_edit.add_argument("--api-key-stdin", action="store_true")
    model_edit.add_argument("--api-key", help=argparse.SUPPRESS)
    model_show = model_commands.add_parser("show")
    model_show.add_argument("profile_id")
    model_use = model_commands.add_parser("use")
    model_use.add_argument("profile_id")
    model_remove = model_commands.add_parser("remove")
    model_remove.add_argument("profile_id")
    model_test = model_commands.add_parser("test")
    model_test.add_argument("profile_id")

    legacy = commands.add_parser(
        "run",
        help="legacy-mini-runtime: mock job search compatibility only; not the formal business workflow",
        description="legacy-mini-runtime (mock job search only; not Candidate/Role/Matching/Preparation/Feedback)",
    )
    legacy.add_argument("user_input")

    auth = commands.add_parser("auth", help="manage local source credentials")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    chrome = auth_commands.add_parser("import-chrome")
    chrome.add_argument("--source", required=True, choices=("zhaopin", "zhaopin_jobs"))
    chrome.add_argument("--name", default="default")
    chrome.add_argument("--profile")
    chrome.add_argument("--credential-root", type=Path)
    api_key = auth_commands.add_parser("import-api-key")
    api_key.add_argument("--source", required=True, choices=("brave_search",))
    api_key.add_argument("--name", default="default")
    api_key.add_argument("--api-key-stdin", action="store_true")
    api_key.add_argument("--api-key", help=argparse.SUPPRESS)
    api_key.add_argument("--credential-root", type=Path)
    browser_profile = auth_commands.add_parser(
        "browser-profile", help="manage isolated authenticated Chrome profiles"
    )
    browser_profile_commands = browser_profile.add_subparsers(
        dest="browser_profile_command", required=True
    )
    for action in ("init", "open", "status", "stop"):
        operation = browser_profile_commands.add_parser(action)
        operation.add_argument(
            "--source", required=True,
            choices=("nowcoder_experience", "xiaohongshu_experience"),
        )
        operation.add_argument("--name", default="default")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = "--json" in raw_argv
    if not raw_argv:
        if sys.stdin.isatty() and sys.stdout.isatty():
            raw_argv = ["ui"]
        else:
            return _interactive_guide(json_mode=False)
    try:
        args = _build_parser().parse_args(raw_argv)
        if args.command is None:
            return _interactive_guide(json_mode=args.json_output)
        return _dispatch(args)
    except CLIArgumentError as exc:
        return _emit_error("invalid_input", str(exc), 2, json_mode=json_mode,
                           recovery_hint="run campus-agent --help and correct the command arguments")
    except SystemExit:
        raise
    except Exception as exc:
        return _handle_error(exc, json_mode=json_mode)


def _dispatch(args: argparse.Namespace) -> int:
    from campus_job_agent.runtime import RuntimeFactory

    owner_id = getattr(args, "user_id", None) or "local-user"
    runtime = RuntimeFactory(data_root=args.data_root).build(owner_id=owner_id)
    if args.command == "doctor":
        checks = runtime.doctor()
        status = "completed" if (
            all(checks["writable"].values())
            and checks["sqlite_checkpointer"]["available"]
            and checks["llm"]["configuration_complete"]
            and (
                not checks["community_retrieval"]["required"]
                or checks["community_retrieval"]["ready"]
            )
        ) else "partial"
        return _emit({
            "schema_version": "v0.7.1", "command": "doctor", "status": status,
            "checks": checks, "next_action": "session.start" if status == "completed" else "fix_doctor_failures",
            "warnings": [] if status == "completed" else ["runtime_dependency_incomplete"], "errors": [],
        }, json_mode=args.json_output)
    if args.command == "ui":
        if args.json_output:
            raise CLIArgumentError("CLI UI does not support --json")
        from campus_job_agent.cli_ui import run_cli_ui
        return run_cli_ui(runtime)
    if args.command == "model":
        return _model(runtime, args)
    if args.command == "session":
        return _session(runtime, args)
    if args.command == "inspect":
        return _inspect(runtime, args)
    if args.command == "resume":
        return _resume_evidence(runtime, args)
    if args.command == "candidate":
        return _candidate(runtime, args)
    if args.command == "intent":
        return _intent(runtime, args)
    if args.command == "role":
        return _role(runtime, args)
    if args.command == "run":
        return _legacy_run(runtime, args)
    if args.command == "auth":
        return _source_auth(runtime, args)
    raise CLIArgumentError("unknown command")


def _session(runtime: Any, args: argparse.Namespace) -> int:
    from campus_job_agent.runtime import NodeObserver

    command = f"session.{args.session_command}"
    if args.session_command == "status":
        session = runtime.session_service.status(args.session_id)
        return _emit(_session_payload(command, session), json_mode=args.json_output)
    if args.session_command == "history":
        if args.user_id is not None:
            runtime.session_repository.get(args.session_id, user_id=args.user_id)
        history = runtime.session_service.history(args.session_id)
        session = runtime.session_service.status(args.session_id)
        payload = _session_payload(command, session)
        payload["history"] = history
        return _emit(payload, json_mode=args.json_output)

    if args.session_command == "start":
        session = runtime.session_service.start(
            user_id=args.user_id, idempotency_key=args.idempotency_key
        )
        parent_run_id = session.latest_run_id
    else:
        session = runtime.session_service.status(args.session_id)
        parent_run_id = session.latest_run_id

    thread_id = f"thread-{uuid4()}"
    manifest = runtime.artifact_writer.initialize_run(
        session_id=session.session_id, thread_id=thread_id, workflow="runtime",
        command=command, parent_run_id=parent_run_id,
        input_refs={"session_id": session.session_id},
    )
    try:
        with NodeObserver(runtime.artifact_writer, manifest, "persist_session") as observed:
            if args.session_command == "start":
                updated = session if session.latest_run_id else runtime.session_repository.update_navigation(
                    session.session_id, expected_version=session.session_version,
                    operation="run_linked", latest_run_id=manifest.run_id,
                )
                next_action = "resume.import"
            else:
                updated = runtime.session_service.resume(
                    session.session_id, expected_version=args.expected_version,
                    latest_run_id=manifest.run_id,
                )
                next_action = _next_action(
                    updated.current_stage,
                    updated.pending_request,
                    updated.current_refs,
                    updated.status,
                )
            observed.finish(
                output_refs={"session_id": updated.session_id},
                counts={"session_version": updated.session_version}, route=next_action,
            )
        safe_state = {
            "session_id": updated.session_id, "session_version": updated.session_version,
            "status": updated.status, "current_stage": updated.current_stage,
            "current_refs": updated.current_refs, "pending_request": updated.pending_request,
            "pending_handoff_ids": updated.pending_handoff_ids, "latest_run_id": updated.latest_run_id,
        }
        runtime.artifact_writer.write_state(manifest.run_id, safe_state)
        runtime.artifact_writer.write_report(
            manifest.run_id,
            f"# Session command\n\n- session_id: `{updated.session_id}`\n- status: `{updated.status}`\n- next action: `{next_action}`\n",
        )
        terminal = runtime.artifact_writer.finish_run(
            manifest.run_id, status="completed", next_action=next_action,
            output_refs={"session_id": updated.session_id},
        )
    except Exception as exc:
        try:
            runtime.artifact_writer.finish_run(
                manifest.run_id, status="failed", next_action="inspect.run",
                reason_codes=["internal_error"],
            )
        except Exception:
            pass
        raise exc
    payload = _session_payload(command, updated)
    payload.update({
        "run_id": manifest.run_id, "thread_id": thread_id, "next_action": next_action,
        "artifact_paths": terminal.artifact_paths,
    })
    return _emit(payload, json_mode=args.json_output)


def _inspect(runtime: Any, args: argparse.Namespace) -> int:
    from campus_job_agent.runtime import redact

    command = f"inspect.{args.inspect_command}"
    result: Any
    artifact_paths: dict[str, str] = {}
    if args.inspect_command == "run":
        manifest = runtime.artifact_writer.load_manifest(args.run_id)
        result = {
            "manifest": manifest.model_dump(mode="json"),
            "errors": runtime.artifact_writer.read_jsonl(args.run_id, "errors"),
            "pending_interaction": manifest.pending_request_id,
            "pending_handoffs": manifest.pending_handoff_ids,
        }
        artifact_paths = manifest.artifact_paths
    elif args.inspect_command == "node":
        events = runtime.artifact_writer.read_jsonl(args.run_id, "events")
        result = [item for item in events if item.get("node") and (not args.node or item.get("node") == args.node)]
        artifact_paths = runtime.artifact_writer.load_manifest(args.run_id).artifact_paths
    elif args.inspect_command == "llm":
        result = runtime.artifact_writer.read_jsonl(args.run_id, "llm_calls")
        artifact_paths = runtime.artifact_writer.load_manifest(args.run_id).artifact_paths
    elif args.inspect_command == "evidence":
        artifact = runtime.evidence_repository.get_artifact(args.object_id)
        fragment = runtime.evidence_repository.get_fragment(args.object_id)
        if artifact is None and fragment is None:
            raise KeyError(f"evidence object not found: {args.object_id}")
        value = artifact or fragment
        data = value.model_dump(mode="json")
        data.pop("text", None)
        result = redact(data)
    elif args.inspect_command == "claims":
        claim_items = [
            {
                "claim_id": item.claim_id, "subject_id": item.subject_id,
                "predicate": item.predicate, "claim_type": item.claim_type,
                "evidence_fragment_ids": item.evidence_fragment_ids,
                "source_evidence_ids": item.source_evidence_ids,
                "confidence": item.confidence, "schema_version": item.schema_version,
                "status": item.status,
                "origin_kind": item.origin_kind, "origin_ref": item.origin_ref,
                "effective_at": item.effective_at.isoformat(),
                "supersedes_claim_ids": item.all_supersedes_claim_ids,
            }
            for item in runtime.evidence_repository.list_claims(args.subject_id)
        ]
        result = {
            "claims": claim_items,
            "validation_receipts": [
                item.model_dump(mode="json")
                for item in runtime.evidence_repository.list_validation_receipts(
                    subject_ref=args.subject_id
                )
            ],
        }
    elif args.inspect_command == "profile":
        profile = runtime.profile_repository.get_profile(args.snapshot_id)
        if profile is None:
            raise KeyError(f"profile not found: {args.snapshot_id}")
        result = {
            "snapshot_id": profile.snapshot_id, "subject_id": profile.subject_id,
            "profile_type": profile.profile_type, "version": profile.version,
            "schema_version": profile.schema_version, "supporting_claim_ids": profile.supporting_claim_ids,
            "created_at": profile.created_at.isoformat(),
            "profile_fields": sorted(profile.profile_data),
        }
    else:
        if args.run_id:
            result = runtime.artifact_writer.read_jsonl(args.run_id, "handoffs")
            artifact_paths = runtime.artifact_writer.load_manifest(args.run_id).artifact_paths
        elif args.handoff_id:
            result = runtime.session_repository.get_handoff(args.handoff_id).model_dump(mode="json")
        else:
            result = [
                item.model_dump(mode="json")
                for item in runtime.session_repository.list_handoffs(session_id=args.session_id)
            ]
    payload = {
        "schema_version": "v0.7.1", "command": command, "status": "completed",
        "result": redact(result), "artifact_paths": artifact_paths,
        "next_action": None, "warnings": [], "errors": [],
    }
    return _emit(payload, json_mode=args.json_output)


def _candidate(runtime: Any, args: argparse.Namespace) -> int:
    from campus_job_agent.schemas import (
        HumanAnswer,
        HumanInteractionResponse,
        ProfileCorrection,
    )
    from campus_job_agent.tools.candidate_profile import diff_profile_snapshots

    service = runtime.application_services["candidate"]
    command = f"candidate.{args.candidate_command}"
    if args.candidate_command == "build":
        payload = service.build(
            session_id=args.session_id,
            candidate_id=args.candidate_id,
            resume_evidence_id=args.resume_evidence,
        )
        return _emit(
            payload, json_mode=args.json_output,
            exit_code=_candidate_exit_code(payload),
        )
    if args.candidate_command == "show":
        snapshot = runtime.profile_repository.get_profile(args.snapshot_id)
        if snapshot is None:
            raise KeyError(f"profile not found: {args.snapshot_id}")
        result = {
            "snapshot_id": snapshot.snapshot_id,
            "subject_id": snapshot.subject_id,
            "version": snapshot.version,
            "schema_version": snapshot.schema_version,
            "supporting_claim_ids": snapshot.supporting_claim_ids,
            "profile": snapshot.profile_data,
        }
        return _emit({
            "schema_version": "v0.7.1", "command": command,
            "status": "completed", "result": result,
            "next_action": None, "warnings": [], "errors": [],
        }, json_mode=args.json_output)
    if args.candidate_command == "diff":
        before = runtime.profile_repository.get_profile(args.from_snapshot_id)
        after = runtime.profile_repository.get_profile(args.to_snapshot_id)
        if before is None:
            raise KeyError(f"profile not found: {args.from_snapshot_id}")
        if after is None:
            raise KeyError(f"profile not found: {args.to_snapshot_id}")
        result = diff_profile_snapshots(
            before.snapshot_id, before.profile_data,
            after.snapshot_id, after.profile_data,
        ).model_dump(mode="json")
        return _emit({
            "schema_version": "v0.7.1", "command": command,
            "status": "completed", "result": result,
            "next_action": None, "warnings": [], "errors": [],
        }, json_mode=args.json_output)

    session = runtime.session_service.status(args.session_id)
    receipt = runtime.evidence_repository.get_response_receipt(args.response_id)
    request = None
    thread_id = None
    user_id = session.user_id
    if session.latest_run_id:
        manifest = runtime.artifact_writer.load_manifest(session.latest_run_id)
        thread_id = manifest.thread_id
        with runtime.open_workflow("candidate") as workflow:
            values = dict(workflow.get_state(thread_id).values or {})
        request = values.get("pending_interaction")
    if request is None and receipt is not None:
        request = {
            "request_id": receipt.get("request_id"),
            "thread_id": receipt.get("thread_id"),
            "user_id": receipt.get("user_id"),
        }
        thread_id = str(receipt.get("thread_id") or thread_id or "")
        user_id = str(receipt.get("user_id") or user_id)
    if not isinstance(request, dict) or not request.get("request_id") or not thread_id:
        raise CLIArgumentError("session has no pending Candidate interaction")

    answers: list[HumanAnswer] = []
    for value in args.answer:
        if "=" not in value:
            raise CLIArgumentError("--answer must use QUESTION_ID=TEXT")
        question_id, text = value.split("=", 1)
        answers.append(HumanAnswer(question_id=question_id.strip(), text=text))
    corrections: list[ProfileCorrection] = []
    for value in args.correction:
        try:
            corrections.append(ProfileCorrection.model_validate(json.loads(value)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise CLIArgumentError(f"invalid --correction JSON: {exc}") from exc
    response = HumanInteractionResponse(
        response_id=args.response_id,
        request_id=str(request["request_id"]),
        thread_id=str(thread_id),
        user_id=user_id,
        action=args.action,
        answers=answers,
        file_paths=[str(Path(value).expanduser().resolve()) for value in args.upload],
        corrections=corrections,
        confirmation=True if args.action == "confirm" else None,
        skipped_ids=args.skip_id,
    )
    payload = service.resume(session_id=args.session_id, response=response)
    return _emit(
        payload, json_mode=args.json_output,
        exit_code=_candidate_exit_code(payload),
    )


def _resume_evidence(runtime: Any, args: argparse.Namespace) -> int:
    from campus_job_agent.schemas import ResumeReviewResponse

    service = runtime.application_services["resume"]
    command = f"resume.{args.resume_command}"
    if args.resume_command == "show":
        draft = runtime.evidence_repository.get_resume_draft(args.object_id)
        snapshot = runtime.evidence_repository.get_resume_evidence_snapshot(args.object_id)
        if draft is None and snapshot is None:
            raise KeyError(f"resume evidence object not found: {args.object_id}")
        value = draft or snapshot
        return _emit({
            "schema_version": "v0.7.1", "command": command,
            "status": "completed", "result": value.model_dump(mode="json"),
            "next_action": None, "warnings": [], "errors": [],
        }, json_mode=args.json_output)
    if args.resume_command == "import":
        payload = service.import_pdf(
            session_id=args.session_id, candidate_id=args.candidate_id,
            input_path=args.input, reparse=args.reparse,
        )
        if not args.json_output and sys.stdin.isatty() and payload.get("status") == "interrupted":
            payload = _interactive_resume_loop(runtime, args.session_id, payload)
        return _emit(payload, json_mode=args.json_output)

    session = runtime.session_service.status(args.session_id)
    if args.response_id:
        receipt = runtime.evidence_repository.get_resume_review_receipt(
            args.response_id
        )
        if receipt is not None:
            if not session.latest_run_id:
                raise CLIArgumentError("resume review run is unavailable")
            manifest = runtime.artifact_writer.load_manifest(session.latest_run_id)
            if manifest.workflow != "resume_evidence":
                raise CLIArgumentError("legacy_session_incompatible")
            patch = _parse_resume_patch(args.patch, args.action)
            replay = ResumeReviewResponse(
                response_id=args.response_id, request_id=receipt.request_id,
                thread_id=manifest.thread_id, user_id=session.user_id,
                action=args.action, patch=patch,
                attests_pdf_source=args.action == "correct",
            )
            return _emit(
                service.resume(session_id=args.session_id, response=replay),
                json_mode=args.json_output,
            )
    pending, thread_id = _pending_resume_request(runtime, session)
    if pending is None or thread_id is None:
        raise CLIArgumentError("session has no pending resume review")
    if args.action is None:
        if args.json_output or not sys.stdin.isatty():
            raise CLIArgumentError("--action is required outside interactive TTY mode")
        payload = {
            "status": "interrupted", "pending_request": pending,
            "thread_id": thread_id,
        }
        payload = _interactive_resume_loop(runtime, args.session_id, payload)
        return _emit(payload, json_mode=False)
    if not args.response_id:
        raise CLIArgumentError("--response-id is required with --action")
    patch = _parse_resume_patch(args.patch, args.action)
    response = ResumeReviewResponse(
        response_id=args.response_id, request_id=str(pending["request_id"]),
        thread_id=thread_id, user_id=session.user_id, action=args.action,
        patch=patch, attests_pdf_source=args.action == "correct",
    )
    payload = service.resume(session_id=args.session_id, response=response)
    return _emit(payload, json_mode=args.json_output)


def _pending_resume_request(runtime: Any, session: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not session.latest_run_id:
        return None, None
    manifest = runtime.artifact_writer.load_manifest(session.latest_run_id)
    if manifest.workflow != "resume_evidence":
        raise CLIArgumentError("legacy_session_incompatible")
    with runtime.open_workflow("resume") as workflow:
        values = dict(workflow.get_state(manifest.thread_id).values or {})
    pending = values.get("pending_interaction")
    return (pending if isinstance(pending, dict) else None), manifest.thread_id


def _interactive_resume_loop(
    runtime: Any, session_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    from campus_job_agent.schemas import ResumeReviewResponse

    service = runtime.application_services["resume"]
    current = payload
    while current.get("status") == "interrupted":
        pending = current.get("pending_request")
        if not isinstance(pending, dict):
            session = runtime.session_service.status(session_id)
            pending, _ = _pending_resume_request(runtime, session)
        if not isinstance(pending, dict):
            raise CLIArgumentError("resume review request is unavailable")
        view = service.review_view(pending)
        print(f"\n[{view['section']}] {view['target_kind']}")
        print(json.dumps(view["value"], ensure_ascii=False, indent=2))
        if view["source_excerpts"]:
            print("Source:")
            for excerpt in view["source_excerpts"]:
                print(f"  page {excerpt['page']}: {excerpt['text']}")
        allowed = set(pending["allowed_actions"])
        suffix = "[Y/e/d/r/c]" if "remove" in allowed else "[Y/e/r/c]"
        answer = input(f"Confirm this resume item? {suffix} ").strip().lower()
        action = {
            "": "confirm", "y": "confirm", "yes": "confirm",
            "e": "correct", "d": "remove", "r": "retry", "c": "cancel",
        }.get(answer)
        if action is None or action not in allowed:
            print("Invalid action. Please choose one of the displayed options.")
            continue
        patch = None
        if action == "correct":
            raw = input("Correction JSON patch : ").strip()
            patch = _parse_resume_patch(raw, action)
        response = ResumeReviewResponse(
            response_id=f"resume-response-{uuid4()}",
            request_id=str(pending["request_id"]),
            thread_id=str(pending["thread_id"]),
            user_id=str(pending["user_id"]), action=action,
            patch=patch, attests_pdf_source=action == "correct",
        )
        current = service.resume(session_id=session_id, response=response)
    return current


def _parse_resume_patch(raw: str | None, action: str) -> dict[str, Any] | None:
    if action != "correct":
        if raw is not None:
            raise CLIArgumentError("--patch is only valid with --action correct")
        return None
    if raw is None:
        raise CLIArgumentError("correct action requires --patch JSON")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CLIArgumentError(f"invalid --patch JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise CLIArgumentError("--patch must be a JSON object")
    return value


def _intent(runtime: Any, args: argparse.Namespace) -> int:
    from campus_job_agent.schemas import IntentReviewResponse, IntentRevisionPatch, SearchScope

    service = runtime.application_services["intent"]
    command = f"intent.{args.intent_command}"
    if args.intent_command == "create":
        payload = service.create(session_id=args.session_id, raw_text=args.text)
        return _emit(payload, json_mode=args.json_output)
    if args.intent_command == "show":
        snapshot = runtime.profile_repository.get_profile(args.snapshot_id)
        if snapshot is None or snapshot.profile_type != "career_intent":
            raise KeyError(f"CareerIntent snapshot not found: {args.snapshot_id}")
        scopes = [
            item for item in runtime.intent_repository.list("search_scope", SearchScope, owner_id=snapshot.subject_id)
            if item.career_intent_snapshot_id == snapshot.snapshot_id
        ]
        return _emit({
            "schema_version": "v0.7.1", "command": command, "status": "completed",
            "result": {
                "snapshot_id": snapshot.snapshot_id, "subject_id": snapshot.subject_id,
                "version": snapshot.version, "profile": snapshot.profile_data,
                "search_scopes": [item.model_dump(mode="json") for item in scopes],
            },
            "next_action": None, "warnings": [], "errors": [],
        }, json_mode=args.json_output)

    session = runtime.session_service.status(args.session_id)
    pending = None
    thread_id = None
    if session.latest_run_id:
        manifest = runtime.artifact_writer.load_manifest(session.latest_run_id)
        thread_id = manifest.thread_id
        with runtime.open_workflow("intent") as workflow:
            values = dict(workflow.get_state(thread_id).values or {})
        pending = values.get("pending_interaction")
    stored = runtime.intent_repository.get_response_result(args.response_id)
    if pending is None and stored is not None:
        previous = stored.get("response_payload") or {}
        pending = {
            "request_id": previous.get("request_id"),
            "thread_id": previous.get("thread_id"),
            "user_id": previous.get("user_id"),
        }
        thread_id = str(previous.get("thread_id") or thread_id or "")
    if not isinstance(pending, dict) or not pending.get("request_id") or not thread_id:
        raise CLIArgumentError("session has no pending CareerIntent review")
    patch = None
    if args.patch is not None:
        try:
            patch = IntentRevisionPatch.model_validate(json.loads(args.patch))
        except (json.JSONDecodeError, ValueError) as exc:
            raise CLIArgumentError(f"invalid --patch JSON: {exc}") from exc
    response = IntentReviewResponse(
        response_id=args.response_id, request_id=str(pending["request_id"]),
        thread_id=str(thread_id), user_id=session.user_id,
        action=args.action, patch=patch,
    )
    payload = service.resume(session_id=args.session_id, response=response)
    return _emit(payload, json_mode=args.json_output)


def _role(runtime: Any, args: argparse.Namespace) -> int:
    service = runtime.application_services["role"]
    command = f"role.{args.role_command}"
    if args.role_command == "research":
        payload = service.research(
            session_id=args.session_id, handoff_id=args.handoff
        )
        return _emit(payload, json_mode=args.json_output)
    if args.role_command == "resume":
        payload = service.resume(
            session_id=args.session_id,
            action=args.action,
            response_id=args.response_id,
            credential_ref=args.credential_ref,
            browser_profile_ref=args.browser_profile_ref,
        )
        return _emit(payload, json_mode=args.json_output)
    result = service.show(args.bundle_id)
    return _emit({
        "schema_version": "v0.7.1",
        "command": command,
        "status": "completed",
        "result": result,
        "next_action": None,
        "warnings": [],
        "errors": [],
    }, json_mode=args.json_output)


def _model(runtime: Any, args: argparse.Namespace) -> int:
    command = f"model.{args.model_command}"
    service = runtime.model_profile_service
    if args.model_command == "list":
        result = {
            "providers": service.list_safe(),
            "current": (
                runtime.model_profile_repository.get_current().profile_id
                if runtime.model_profile_repository.get_current() else None
            ),
        }
    elif args.model_command == "show":
        result = service.show_safe(args.profile_id)
    elif args.model_command == "edit":
        if args.api_key is not None:
            raise CLIArgumentError(
                "--api-key is forbidden; use hidden input or --api-key-stdin"
            )
        api_key = None
        if args.api_key_stdin:
            api_key = sys.stdin.readline().rstrip("\r\n")
            if not api_key:
                raise CLIArgumentError("--api-key-stdin received an empty key")
        profile = service.edit(
            args.profile_id, name=args.name, base_url=args.base_url,
            model=args.model, api_key=api_key,
            timeout_seconds=args.timeout_seconds, category=args.category,
            website_url=args.website_url, notes=args.notes, icon=args.icon,
        )
        result = service.safe(profile)
    elif args.model_command == "use":
        result = service.safe(service.use(args.profile_id))
    elif args.model_command == "remove":
        removed = service.remove(args.profile_id)
        result = {"id": removed.profile_id, "removed": True}
    elif args.model_command == "test":
        result = service.test(args.profile_id)
    else:
        if args.api_key is not None:
            raise CLIArgumentError(
                "--api-key is forbidden; use hidden input or --api-key-stdin"
            )
        api_key = None
        if args.preset != "mock":
            if args.api_key_stdin:
                api_key = sys.stdin.readline().rstrip("\r\n")
            elif sys.stdin.isatty():
                api_key = getpass.getpass("API key: ")
            else:
                raise CLIArgumentError(
                    "non-mock provider requires hidden input or --api-key-stdin"
                )
        profile = service.add(
            profile_id=args.profile_id, name=args.name, preset=args.preset,
            base_url=args.base_url, model=args.model, api_key=api_key,
            activate=args.activate, timeout_seconds=args.timeout_seconds,
            category=args.category,
            website_url=args.website_url, notes=args.notes, icon=args.icon,
        )
        result = service.safe(profile)
    return _emit({
        "schema_version": "v0.7.1", "command": command,
        "status": "completed", "result": result,
        "next_action": None, "warnings": [], "errors": [],
    }, json_mode=args.json_output)


def _candidate_exit_code(payload: dict[str, Any]) -> int:
    if payload.get("status") != "failed":
        return 0
    error_types = {
        str(item.get("error_type")) for item in payload.get("errors", [])
    }
    if error_types.intersection({"provider_error", "network_timeout", "rate_limited", "auth_required"}):
        return 4
    if error_types.intersection({"storage_error", "storage_failure", "checkpoint_error", "checkpoint_failure"}):
        return 5
    if error_types.intersection({"validation_error", "llm_output_error", "contract_violation"}):
        return 3
    return 6


def _legacy_run(runtime: Any, args: argparse.Namespace) -> int:
    from campus_job_agent.agent import run_agent

    state = run_agent(args.user_input, data_root=runtime.paths.data_root)
    verification = state.get("verification", {})
    status = "completed" if verification.get("passed") else "failed"
    exit_code = 0 if status == "completed" else 6
    errors = state.get("errors", []) if status == "failed" else []
    llm_error_types = {
        str(item.get("error_type"))
        for item in state.get("llm_calls", [])
        if item.get("error_type")
    }
    llm_error_types.update(
        str(item.get("error_type"))
        for item in errors
        if item.get("node") == "parse_goal" and item.get("error_type")
    )
    if status == "failed" and llm_error_types.intersection(
        {"provider_error", "network_timeout", "rate_limited", "auth_required"}
    ):
        exit_code = 4
        errors = [{
            "error_type": "external_dependency",
            "message": "legacy-mini-runtime LLM provider unavailable",
            "recovery_hint": "inspect the safe LLM receipt and retry the provider",
        }, *errors]
    payload = {
        "schema_version": "v0.7.1", "command": "run", "workflow": "legacy-mini-runtime",
        "run_id": state["run_id"], "session_id": None, "status": status,
        "next_action": "session.start", "output_refs": {}, "pending_request": None,
        "artifact_paths": {
            "report": state.get("report_path"),
            "trace": str(runtime.paths.run_root / state["run_id"] / "trace.json"),
            "llm_calls": str(runtime.paths.run_root / state["run_id"] / "llm_calls.json"),
        },
        "warnings": ["not_formal_business_workflow", "mock_job_search_only"],
        "errors": errors,
    }
    return _emit(payload, json_mode=args.json_output, exit_code=exit_code)


def _source_auth(runtime: Any, args: argparse.Namespace) -> int:
    if args.auth_command == "browser-profile":
        manager = runtime.browser_profile_manager
        profile_ref = (
            f"local-browser-profile://{args.source}/{args.name}"
        )
        if args.browser_profile_command == "init":
            ref = manager.init(source_id=args.source, name=args.name)
            status = manager.status(ref.browser_profile_ref)
        elif args.browser_profile_command == "open":
            status = manager.open(profile_ref)
            ref = manager._parse_ref(profile_ref)
        elif args.browser_profile_command == "status":
            status = manager.status(profile_ref)
            ref = manager._parse_ref(profile_ref)
        else:
            status = manager.stop(profile_ref)
            ref = manager._parse_ref(profile_ref)
        next_action = None
        if args.browser_profile_command == "open":
            next_action = "complete_manual_login"
        elif not status.configured:
            next_action = "auth.browser-profile.init"
        return _emit({
            "schema_version": "v0.7.1",
            "command": f"auth.browser-profile.{args.browser_profile_command}",
            "status": "completed",
            "source_id": ref.source_id,
            "browser_profile_ref": ref.browser_profile_ref,
            "profile_status": status.model_dump(mode="json"),
            "next_action": next_action,
            "warnings": [],
            "errors": [],
        }, json_mode=args.json_output)
    store = runtime.credential_resolver
    if args.credential_root:
        from campus_job_agent.sources import LocalCredentialStore
        store = LocalCredentialStore(args.credential_root)
    if args.auth_command == "import-api-key":
        if args.api_key is not None:
            raise CLIArgumentError(
                "--api-key is forbidden; use --api-key-stdin"
            )
        if not args.api_key_stdin:
            raise CLIArgumentError("source API key import requires --api-key-stdin")
        value = sys.stdin.readline().rstrip("\r\n")
        if not value:
            raise CLIArgumentError("--api-key-stdin received an empty key")
        ref = store.save_source_api_key(
            source_id="nowcoder_experience", name=args.name, api_key=value,
        )
        command = "auth.import-api-key"
    else:
        source_id = {"zhaopin": "zhaopin_jobs"}.get(args.source, args.source)
        ref = store.import_chrome(
            source_id=source_id, name=args.name, cookie_file=args.profile
        )
        command = "auth.import-chrome"
    return _emit({
        "schema_version": "v0.7.1", "command": command, "status": "completed",
        "source_id": ref.source_id, "credential_ref": ref.credential_ref,
        "credential_root": str(store.root.resolve()), "next_action": None,
        "warnings": [], "errors": [],
    }, json_mode=args.json_output)


def _session_payload(command: str, session: Any) -> dict[str, Any]:
    return {
        "schema_version": "v0.7.1", "command": command,
        "run_id": session.latest_run_id, "session_id": session.session_id,
        "session_version": session.session_version, "status": session.status,
        "current_stage": session.current_stage, "current_refs": session.current_refs,
        "pending_request": session.pending_request,
        "pending_handoff_ids": session.pending_handoff_ids,
        "next_action": _next_action(
            session.current_stage,
            session.pending_request,
            session.current_refs,
            session.status,
        ),
        "artifact_paths": {}, "warnings": [], "errors": [],
    }


def _next_action(
    stage: str,
    pending_request: str | None = None,
    current_refs: dict[str, Any] | None = None,
    status: str = "active",
) -> str:
    if status == "failed":
        return "session.resume"
    if pending_request:
        if pending_request.startswith("request-resume-"):
            return "resume.resume"
        if pending_request.startswith("request-intent-"):
            return "intent.resume"
        if pending_request.startswith("request-role-auth-"):
            return "role.resume"
        if stage == "candidate":
            return "candidate.resume"
    refs = current_refs or {}
    if stage == "candidate":
        return (
            "candidate.build"
            if refs.get("resume_evidence_snapshot_id")
            and not refs.get("candidate_profile_snapshot_id")
            else "resume.import"
        )
    return {
        "intent": "intent.create", "role": "role.research",
        "matching": "match.run", "preparation": "plan.build", "feedback": "feedback.add",
    }.get(stage, "session.status")


def _interactive_guide(*, json_mode: bool) -> int:
    payload = {
        "schema_version": "v0.7.1", "command": "guide", "status": "completed",
        "message": (
            "Start with `campus-agent session start`; import and confirm resume evidence, "
            "then build/confirm the Candidate profile, "
            "then use `campus-agent intent create` and `intent resume`."
        ),
        "next_action": "session.start", "warnings": [], "errors": [],
    }
    return _emit(payload, json_mode=json_mode)


def _emit(payload: dict[str, Any], *, json_mode: bool, exit_code: int = 0) -> int:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        for key in (
            "workflow", "run_id", "session_id", "status", "current_stage", "next_action",
            "source_id", "credential_ref", "credential_root", "deduplicated",
        ):
            if key in payload and payload[key] is not None:
                print(f"{key}: {payload[key]}")
        if payload.get("message"):
            print(payload["message"])
        if payload.get("warnings"):
            print("warnings: " + ", ".join(payload["warnings"]))
        if payload.get("artifact_paths"):
            print("artifact_paths: " + json.dumps(payload["artifact_paths"], ensure_ascii=False))
        if payload.get("output_refs"):
            print("output_refs: " + json.dumps(payload["output_refs"], ensure_ascii=False))
        if payload.get("pending_request"):
            print("pending_request: " + json.dumps(payload["pending_request"], ensure_ascii=False))
        if payload.get("metrics"):
            print("metrics: " + json.dumps(payload["metrics"], ensure_ascii=False))
        if payload.get("checks"):
            print(json.dumps(payload["checks"], ensure_ascii=False, indent=2))
        if "result" in payload:
            print(json.dumps(payload["result"], ensure_ascii=False, indent=2))
        if "history" in payload:
            print(json.dumps(payload["history"], ensure_ascii=False, indent=2))
    return exit_code


def _emit_error(error_type: str, message: str, exit_code: int, *, json_mode: bool,
                recovery_hint: str) -> int:
    payload = {
        "schema_version": "v0.7.1", "command": "error", "status": "failed",
        "next_action": recovery_hint, "warnings": [],
        "errors": [{"error_type": error_type, "message": message, "recovery_hint": recovery_hint}],
    }
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    else:
        print("status: failed", file=sys.stderr)
        print(f"error_type: {error_type}", file=sys.stderr)
        print(f"error: {message}", file=sys.stderr)
        print(f"recovery_hint: {recovery_hint}", file=sys.stderr)
    return exit_code


def _handle_error(exc: Exception, *, json_mode: bool) -> int:
    from campus_job_agent.integrations import BrowserProfileError
    from campus_job_agent.llm import LLMConfigError, LLMProviderError, StructuredOutputError
    from campus_job_agent.runtime import (
        ArtifactWriteError, CandidateApplicationError, IntentApplicationError,
        ModelProfileError, ResumeApplicationError, RoleApplicationError, SessionError,
        exit_code_for_error,
    )
    from campus_job_agent.workflows.career_intent import CareerIntentWorkflowError
    from campus_job_agent.workflows.resume_evidence import ResumeEvidenceWorkflowError
    from campus_job_agent.workflows.role_profile import RoleProfileWorkflowError

    if isinstance(exc, CLIArgumentError):
        return _emit_error("invalid_input", str(exc), 2, json_mode=json_mode, recovery_hint="check command arguments")
    if isinstance(exc, BrowserProfileError):
        return _emit_error(
            exc.code, str(exc), 3, json_mode=json_mode,
            recovery_hint="inspect auth browser-profile status and retry safely",
        )
    if isinstance(exc, SessionError):
        return _emit_error(exc.error_type, str(exc), 3, json_mode=json_mode, recovery_hint="inspect session refs and retry with the current version")
    if isinstance(exc, CandidateApplicationError):
        return _emit_error(exc.error_type, str(exc), 3, json_mode=json_mode, recovery_hint="inspect the Candidate run and retry with valid input")
    if isinstance(exc, (ResumeApplicationError, ResumeEvidenceWorkflowError)):
        error_type = str(getattr(exc, "error_type", "contract_violation"))
        return _emit_error(
            error_type, str(exc), exit_code_for_error(error_type),
            json_mode=json_mode,
            recovery_hint="inspect the ResumeEvidence run and resume with the current request",
        )
    if isinstance(exc, (IntentApplicationError, CareerIntentWorkflowError)):
        return _emit_error("contract_violation", str(exc), 3, json_mode=json_mode, recovery_hint="inspect the CareerIntent run and resume with the current request")
    if isinstance(exc, (RoleApplicationError, RoleProfileWorkflowError)):
        return _emit_error(
            "contract_violation", str(exc), 3, json_mode=json_mode,
            recovery_hint="inspect the WP3.1 role run and resume with the current request",
        )
    if isinstance(exc, ModelProfileError):
        return _emit_error(exc.error_type, str(exc), 3, json_mode=json_mode, recovery_hint="inspect model providers and choose a valid profile")
    if isinstance(exc, KeyError):
        return _emit_error("not_found", str(exc).strip("'"), 3, json_mode=json_mode, recovery_hint="verify the object ID and data root")
    if isinstance(exc, LLMConfigError):
        return _emit_error("invalid_input", str(exc), 2, json_mode=json_mode, recovery_hint="run campus-agent doctor and configure the provider")
    if isinstance(exc, LLMProviderError) or (
        isinstance(exc, StructuredOutputError)
        and exc.error_type in {"provider_error", "network_timeout", "rate_limited", "auth_required"}
    ):
        return _emit_error("external_dependency", str(exc), 4, json_mode=json_mode, recovery_hint="inspect the provider status and retry safely")
    if isinstance(exc, StructuredOutputError):
        return _emit_error("contract_violation", str(exc), 3, json_mode=json_mode, recovery_hint="inspect the safe LLM receipt and contract versions")
    if isinstance(exc, ArtifactWriteError) or isinstance(exc, OSError):
        return _emit_error("storage_failure", str(exc), 5, json_mode=json_mode, recovery_hint="check data-root permissions and inspect the run")
    return _emit_error("internal_error", str(exc), 6, json_mode=json_mode, recovery_hint="run campus-agent doctor and inspect the latest run")


if __name__ == "__main__":
    raise SystemExit(main())

"""Thin local HTTP adapter over the existing application services.

This module intentionally contains transport, ownership and presentation logic only.
Resume extraction, review, sufficiency evaluation and Candidate Profile generation
remain owned by the existing runtime application services and graph ports.
"""

from __future__ import annotations

import json
import os
import hashlib
import tempfile
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.datastructures import UploadFile
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_413_CONTENT_TOO_LARGE,
    HTTP_500_INTERNAL_SERVER_ERROR,
    HTTP_502_BAD_GATEWAY,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from starlette.concurrency import run_in_threadpool

from campus_job_agent.runtime.factory import Runtime, RuntimeFactory
from campus_job_agent.schemas import (
    HumanAnswer,
    HumanInteractionResponse,
    ProfileCorrection,
    ResumeReviewResponse,
)
from campus_job_agent.tools.candidate_profile import diff_profile_snapshots


LOCAL_USER_ID = "local-web-user"
LOCAL_CANDIDATE_ID = "local-web-user"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
RESUME_CONTENT_TYPES = {"application/pdf", "application/octet-stream", ""}
MATERIAL_SUFFIXES = {".pdf", ".md", ".markdown", ".txt"}
MATERIAL_CONTENT_TYPES = {
    "application/pdf",
    "application/octet-stream",
    "text/plain",
    "text/markdown",
    "",
}


class WebAdapterError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        error_type: str = "contract_violation",
        status_code: int = HTTP_400_BAD_REQUEST,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code
        self.retryable = retryable


def _success(data: Any, status_code: int = HTTP_200_OK) -> JSONResponse:
    return JSONResponse(
        {"ok": True, "data": data, "error": None}, status_code=status_code
    )


def _safe_message(exc: Exception, runtime: Runtime | None = None) -> str:
    message = " ".join(str(exc).split())[:400] or "request failed"
    roots = [tempfile.gettempdir()]
    if runtime is not None:
        roots.extend([str(runtime.paths.data_root), str(runtime.paths.run_root)])
    for root in roots:
        if root:
            message = message.replace(root, "<local-path>")
    return message


def _error_response(exc: Exception, runtime: Runtime | None = None) -> JSONResponse:
    if isinstance(exc, WebAdapterError):
        status_code = exc.status_code
        error_type = exc.error_type
        retryable = exc.retryable
        message = _safe_message(exc, runtime)
    elif isinstance(exc, ValidationError):
        status_code = HTTP_400_BAD_REQUEST
        error_type = "contract_violation"
        retryable = False
        message = "提交内容不符合当前交互契约，请检查必填项。"
    else:
        raw = str(exc).lower()
        declared = str(getattr(exc, "error_type", "internal_error"))
        retryable = bool(getattr(exc, "retryable", False))
        if "not found" in raw or declared == "not_found":
            status_code, error_type = HTTP_404_NOT_FOUND, "not_found"
        elif "identity mismatch" in raw or "owner" in raw or "permission" in raw:
            status_code, error_type = HTTP_403_FORBIDDEN, "permission_denied"
        elif "idempotency" in raw or "stale" in raw or "pending" in raw:
            status_code, error_type = HTTP_409_CONFLICT, "stale_input"
        elif declared in {"schema_validation_error", "json_parse_error"}:
            status_code, error_type = HTTP_502_BAD_GATEWAY, "llm_invalid_output"
            retryable = False
        elif declared in {
            "config_error", "llm_unavailable", "source_unavailable",
            "provider_error", "network_timeout", "unsupported_capability",
            "auth_required", "rate_limited",
        }:
            status_code = HTTP_503_SERVICE_UNAVAILABLE
            error_type = (
                declared
                if declared in {"auth_required", "rate_limited", "config_error"}
                else "llm_unavailable"
            )
            retryable = declared not in {"config_error", "unsupported_capability"}
        elif declared in {"contract_violation", "validation_error"}:
            status_code, error_type = HTTP_400_BAD_REQUEST, "contract_violation"
        else:
            status_code, error_type = HTTP_500_INTERNAL_SERVER_ERROR, "internal_error"
        if error_type == "llm_invalid_output":
            message = "模型返回的简历结构不符合契约，系统重试后仍未通过。请重新上传；若持续失败，请检查当前模型配置。"
        elif error_type in {
            "llm_unavailable", "auth_required", "rate_limited", "config_error"
        }:
            message = "当前模型服务不可用，请检查模型配置或稍后重试。"
        else:
            message = (
                _safe_message(exc, runtime)
                if status_code < HTTP_500_INTERNAL_SERVER_ERROR
                else "本地服务处理失败，请查看服务端运行日志。"
            )
    return JSONResponse(
        {
            "ok": False,
            "data": None,
            "error": {
                "type": error_type,
                "message": message,
                "retryable": retryable,
            },
        },
        status_code=status_code,
    )


def _session(runtime: Runtime, session_id: str) -> Any:
    try:
        session = runtime.session_service.status(session_id)
    except Exception as exc:
        raise WebAdapterError(
            "Session 不存在。", error_type="not_found", status_code=HTTP_404_NOT_FOUND
        ) from exc
    if session.user_id != LOCAL_USER_ID:
        raise WebAdapterError(
            "Session 不属于当前本地用户。",
            error_type="permission_denied",
            status_code=HTTP_403_FORBIDDEN,
        )
    return session


def _next_action(session: Any) -> str:
    if session.pending_request:
        if session.latest_run_id and str(session.pending_request).startswith("resume-"):
            return "resume.review"
        return "candidate.interaction"
    if session.status not in {"active", "completed"}:
        return "session.inspect"
    if session.current_refs.get("candidate_profile_snapshot_id"):
        return "candidate.view"
    if session.current_refs.get("resume_evidence_snapshot_id"):
        return "candidate.build"
    return "resume.import"


def _session_view(session: Any) -> dict[str, Any]:
    return {
        **session.model_dump(mode="json"),
        "next_action": _next_action(session),
    }


def _workflow_pending(runtime: Runtime, session: Any, workflow_name: str) -> dict[str, Any] | None:
    if not session.latest_run_id or not session.pending_request:
        return None
    manifest = runtime.artifact_writer.load_manifest(session.latest_run_id)
    if manifest.workflow != workflow_name:
        return None
    graph_name = "resume" if workflow_name == "resume_evidence" else "candidate"
    with runtime.open_workflow(graph_name) as workflow:
        state = workflow.get_state(manifest.thread_id)
    values = dict(state.values or {})
    pending = values.get("pending_interaction")
    if hasattr(pending, "model_dump"):
        return pending.model_dump(mode="json")
    return dict(pending) if isinstance(pending, dict) else None


def _resume_review(runtime: Runtime, session: Any) -> dict[str, Any] | None:
    pending = _workflow_pending(runtime, session, "resume_evidence")
    if pending is None:
        return None
    return {
        "request": pending,
        "view": runtime.application_services["resume"].review_view(pending),
    }


def _candidate_interaction(runtime: Runtime, session: Any) -> dict[str, Any] | None:
    return _workflow_pending(runtime, session, "candidate_profile")


def _profile(runtime: Runtime, snapshot_id: str) -> Any:
    profile = runtime.profile_repository.get_profile(snapshot_id)
    if profile is None:
        raise WebAdapterError(
            "候选人画像不存在。", error_type="not_found", status_code=HTTP_404_NOT_FOUND
        )
    if profile.subject_id != LOCAL_CANDIDATE_ID or profile.profile_type != "candidate":
        raise WebAdapterError(
            "候选人画像不属于当前本地用户。",
            error_type="permission_denied",
            status_code=HTTP_403_FORBIDDEN,
        )
    return profile


def _workspace(runtime: Runtime, session: Any) -> dict[str, Any]:
    resume_snapshot = None
    resume_id = session.current_refs.get("resume_evidence_snapshot_id")
    if isinstance(resume_id, str):
        item = runtime.evidence_repository.get_resume_evidence_snapshot(resume_id)
        if item is not None and item.owner_id == LOCAL_USER_ID:
            resume_snapshot = item.model_dump(mode="json")

    profile_snapshot = None
    profile_id = session.current_refs.get("candidate_profile_snapshot_id")
    if isinstance(profile_id, str):
        item = runtime.profile_repository.get_profile(profile_id)
        if item is not None and item.subject_id == LOCAL_CANDIDATE_ID:
            profile_snapshot = item.model_dump(mode="json")

    profiles = runtime.profile_repository.list_profiles(
        LOCAL_CANDIDATE_ID, "candidate"
    )
    history = [
        {
            "snapshot_id": item.snapshot_id,
            "version": item.version,
            "created_at": item.created_at.isoformat(),
        }
        for item in profiles
    ]
    latest_diff = None
    if len(profiles) >= 2:
        before, after = profiles[-2], profiles[-1]
        latest_diff = diff_profile_snapshots(
            before.snapshot_id,
            before.profile_data,
            after.snapshot_id,
            after.profile_data,
        ).model_dump(mode="json")

    review = None
    interaction = None
    if session.pending_request:
        try:
            review = _resume_review(runtime, session)
            if review is None:
                interaction = _candidate_interaction(runtime, session)
        except Exception:
            # The explicit interaction endpoints will surface a safe error. The
            # composite view remains usable for session recovery/navigation.
            pass

    return {
        "session": _session_view(session),
        "resume": resume_snapshot,
        "resume_review": review,
        "candidate_profile": profile_snapshot,
        "candidate_interaction": interaction,
        "profile_history": history,
        "latest_diff": latest_diff,
        "model": {
            "provider": getattr(runtime.llm_config, "provider", None),
            "model": getattr(runtime.llm_config, "model", None),
            "configured": runtime.llm_config_error is None,
        },
    }


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        value = await request.json()
    except Exception as exc:
        raise WebAdapterError("请求体必须是 JSON 对象。") from exc
    if not isinstance(value, dict):
        raise WebAdapterError("请求体必须是 JSON 对象。")
    return value


async def _read_upload(
    upload: UploadFile,
    *,
    allowed_suffixes: set[str],
    allowed_types: set[str],
) -> tuple[bytes, str]:
    filename = Path(upload.filename or "").name
    suffix = Path(filename).suffix.lower()
    if suffix not in allowed_suffixes and filename.lower() != "readme":
        raise WebAdapterError("不支持的文件类型。")
    if upload.content_type not in allowed_types:
        raise WebAdapterError("文件 Content-Type 不受支持。")
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise WebAdapterError(
            "文件不能超过 10 MB。", status_code=HTTP_413_CONTENT_TOO_LARGE
        )
    if not content:
        raise WebAdapterError("文件不能为空。")
    return content, suffix or ".txt"


def _handler(
    runtime: Runtime, callback: Callable[[Request], Any]
) -> Callable[[Request], Any]:
    async def wrapped(request: Request) -> JSONResponse:
        try:
            return await callback(request)
        except Exception as exc:
            return _error_response(exc, runtime)

    return wrapped


def create_app(data_root: str | Path | None = None) -> Starlette:
    runtime = RuntimeFactory(data_root=data_root).build(owner_id=LOCAL_USER_ID)

    async def health(_: Request) -> JSONResponse:
        return _success(
            {
                "service": "campus-job-agent-web",
                "mode": "local-only",
                "model": {
                    "provider": getattr(runtime.llm_config, "provider", None),
                    "model": getattr(runtime.llm_config, "model", None),
                    "configured": runtime.llm_config_error is None,
                },
            }
        )

    async def create_session(request: Request) -> JSONResponse:
        body: dict[str, Any] = {}
        if await request.body():
            body = await _json_body(request)
        key = body.get("idempotency_key")
        if key is not None and not isinstance(key, str):
            raise WebAdapterError("idempotency_key 必须是字符串。")
        session = await run_in_threadpool(
            runtime.session_service.start,
            user_id=LOCAL_USER_ID,
            idempotency_key=key,
        )
        return _success(_workspace(runtime, session), HTTP_201_CREATED)

    async def get_session(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        return _success(_session_view(session))

    async def get_workspace(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        return _success(await run_in_threadpool(_workspace, runtime, session))

    async def import_resume(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise WebAdapterError("必须提交 PDF 文件。")
        content, suffix = await _read_upload(
            upload, allowed_suffixes={".pdf"}, allowed_types=RESUME_CONTENT_TYPES
        )
        with tempfile.TemporaryDirectory(prefix="campus-web-resume-") as directory:
            path = Path(directory) / f"resume{suffix}"
            path.write_bytes(content)
            result = await run_in_threadpool(
                runtime.application_services["resume"].import_pdf,
                session_id=session.session_id,
                candidate_id=LOCAL_CANDIDATE_ID,
                input_path=str(path),
                reparse=str(form.get("reparse", "false")).lower() == "true",
            )
        current = await run_in_threadpool(_session, runtime, session.session_id)
        return _success({"result": result, "workspace": _workspace(runtime, current)})

    async def get_resume_review(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        review = await run_in_threadpool(_resume_review, runtime, session)
        if review is None:
            raise WebAdapterError(
                "当前没有待处理的简历确认。",
                error_type="not_found",
                status_code=HTTP_404_NOT_FOUND,
            )
        return _success(review)

    async def submit_resume_review(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        body = await _json_body(request)
        action = str(body.get("action", ""))
        response_id = str(body.get("response_id") or f"web-resume-{uuid4()}")
        receipt = runtime.evidence_repository.get_resume_review_receipt(response_id)
        if receipt is not None:
            if not session.latest_run_id:
                raise WebAdapterError("简历确认运行记录不可用。", status_code=HTTP_409_CONFLICT)
            manifest = runtime.artifact_writer.load_manifest(session.latest_run_id)
            if manifest.workflow != "resume_evidence":
                raise WebAdapterError("简历确认已进入后续阶段。", status_code=HTTP_409_CONFLICT)
            pending = {
                "request_id": receipt.request_id,
                "thread_id": manifest.thread_id,
            }
        else:
            review = await run_in_threadpool(_resume_review, runtime, session)
            if review is None:
                raise WebAdapterError("当前没有待处理的简历确认。", status_code=HTTP_409_CONFLICT)
            pending = review["request"]
            if action not in pending.get("allowed_actions", []):
                raise WebAdapterError("当前确认步骤不允许该操作。", status_code=HTTP_409_CONFLICT)
        response = ResumeReviewResponse(
            response_id=response_id,
            request_id=str(receipt.request_id if receipt is not None else pending["request_id"]),
            thread_id=str(pending["thread_id"]),
            user_id=LOCAL_USER_ID,
            action=action,
            patch=body.get("patch"),
            attests_pdf_source=bool(body.get("attests_pdf_source", action == "correct")),
        )
        result = await run_in_threadpool(
            runtime.application_services["resume"].resume,
            session_id=session.session_id,
            response=response,
        )
        current = await run_in_threadpool(_session, runtime, session.session_id)
        return _success({"result": result, "workspace": _workspace(runtime, current)})

    async def get_resume(request: Request) -> JSONResponse:
        snapshot = await run_in_threadpool(
            runtime.evidence_repository.get_resume_evidence_snapshot,
            request.path_params["snapshot_id"],
        )
        if snapshot is None:
            raise WebAdapterError(
                "ResumeEvidence 不存在。",
                error_type="not_found",
                status_code=HTTP_404_NOT_FOUND,
            )
        if snapshot.owner_id != LOCAL_USER_ID or snapshot.candidate_id != LOCAL_CANDIDATE_ID:
            raise WebAdapterError(
                "ResumeEvidence 不属于当前本地用户。",
                error_type="permission_denied",
                status_code=HTTP_403_FORBIDDEN,
            )
        return _success(snapshot.model_dump(mode="json"))

    async def build_candidate(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        resume_id = session.current_refs.get("resume_evidence_snapshot_id")
        if not isinstance(resume_id, str):
            raise WebAdapterError("请先完成简历证据确认。", status_code=HTTP_409_CONFLICT)
        result = await run_in_threadpool(
            runtime.application_services["candidate"].build,
            session_id=session.session_id,
            candidate_id=LOCAL_CANDIDATE_ID,
            resume_evidence_id=resume_id,
        )
        current = await run_in_threadpool(_session, runtime, session.session_id)
        return _success({"result": result, "workspace": _workspace(runtime, current)})

    async def get_candidate_interaction(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        pending = await run_in_threadpool(_candidate_interaction, runtime, session)
        if pending is None:
            raise WebAdapterError(
                "当前没有待处理的画像补充问题。",
                error_type="not_found",
                status_code=HTTP_404_NOT_FOUND,
            )
        return _success(pending)

    async def submit_candidate_interaction(request: Request) -> JSONResponse:
        session = await run_in_threadpool(
            _session, runtime, request.path_params["session_id"]
        )
        file_content: bytes | None = None
        file_suffix = ""
        if request.headers.get("content-type", "").startswith("multipart/form-data"):
            form = await request.form()
            raw_payload = form.get("payload", "{}")
            try:
                body = json.loads(str(raw_payload))
            except json.JSONDecodeError as exc:
                raise WebAdapterError("payload 必须是 JSON。") from exc
            upload = form.get("file")
            if isinstance(upload, UploadFile):
                file_content, file_suffix = await _read_upload(
                    upload,
                    allowed_suffixes=MATERIAL_SUFFIXES,
                    allowed_types=MATERIAL_CONTENT_TYPES,
                )
        else:
            body = await _json_body(request)

        if not isinstance(body, dict):
            raise WebAdapterError("payload 必须是 JSON 对象。")
        response_id = str(body.get("response_id") or f"web-candidate-{uuid4()}")
        receipt = runtime.evidence_repository.get_response_receipt(response_id)
        pending = await run_in_threadpool(_candidate_interaction, runtime, session)
        if receipt is not None:
            pending = {
                "request_id": receipt.get("request_id"),
                "thread_id": receipt.get("thread_id"),
                "user_id": receipt.get("user_id"),
                "allowed_actions": [receipt.get("action")],
            }
        if pending is None or not pending.get("request_id") or not pending.get("thread_id"):
            raise WebAdapterError("当前没有待处理的画像补充问题。", status_code=HTTP_409_CONFLICT)
        if str(pending.get("user_id")) != LOCAL_USER_ID:
            raise WebAdapterError(
                "交互记录不属于当前本地用户。",
                error_type="permission_denied",
                status_code=HTTP_403_FORBIDDEN,
            )
        action = str(body.get("action", ""))
        if action not in pending.get("allowed_actions", []):
            raise WebAdapterError("当前补充步骤不允许该操作。", status_code=HTTP_409_CONFLICT)

        answers = [HumanAnswer.model_validate(item) for item in body.get("answers", [])]
        corrections = [
            ProfileCorrection.model_validate(item) for item in body.get("corrections", [])
        ]
        file_paths: list[str] = []
        staged_path: Path | None = None
        try:
            if file_content is not None:
                staging_root = runtime.paths.cache_root / "web_uploads"
                staging_root.mkdir(parents=True, exist_ok=True)
                stable_name = hashlib.sha256(response_id.encode("utf-8")).hexdigest()
                staged_path = staging_root / f"{stable_name}{file_suffix}"
                staged_path.write_bytes(file_content)
                file_paths = [str(staged_path)]
            response = HumanInteractionResponse(
                response_id=response_id,
                request_id=str(pending["request_id"]),
                thread_id=str(pending["thread_id"]),
                user_id=LOCAL_USER_ID,
                action=action,
                answers=answers,
                file_paths=file_paths,
                corrections=corrections,
                confirmation=body.get("confirmation"),
                skipped_ids=[str(value) for value in body.get("skipped_ids", [])],
            )
            result = await run_in_threadpool(
                runtime.application_services["candidate"].resume,
                session_id=session.session_id,
                response=response,
            )
        finally:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
        current = await run_in_threadpool(_session, runtime, session.session_id)
        return _success({"result": result, "workspace": _workspace(runtime, current)})

    async def get_candidate(request: Request) -> JSONResponse:
        profile = await run_in_threadpool(
            _profile, runtime, request.path_params["snapshot_id"]
        )
        return _success(profile.model_dump(mode="json"))

    async def get_candidate_diff(request: Request) -> JSONResponse:
        before_id = request.query_params.get("from", "")
        after_id = request.query_params.get("to", "")
        if not before_id or not after_id:
            raise WebAdapterError("from 与 to 都是必填参数。")
        before = await run_in_threadpool(_profile, runtime, before_id)
        after = await run_in_threadpool(_profile, runtime, after_id)
        diff = diff_profile_snapshots(
            before.snapshot_id,
            before.profile_data,
            after.snapshot_id,
            after.profile_data,
        )
        return _success(diff.model_dump(mode="json"))

    routes = [
        Route("/api/health", _handler(runtime, health), methods=["GET"]),
        Route("/api/sessions", _handler(runtime, create_session), methods=["POST"]),
        Route("/api/sessions/{session_id}", _handler(runtime, get_session), methods=["GET"]),
        Route(
            "/api/sessions/{session_id}/workspace",
            _handler(runtime, get_workspace),
            methods=["GET"],
        ),
        Route(
            "/api/sessions/{session_id}/resume",
            _handler(runtime, import_resume),
            methods=["POST"],
        ),
        Route(
            "/api/sessions/{session_id}/resume/review",
            _handler(runtime, get_resume_review),
            methods=["GET"],
        ),
        Route(
            "/api/sessions/{session_id}/resume/review",
            _handler(runtime, submit_resume_review),
            methods=["POST"],
        ),
        Route("/api/resume/{snapshot_id}", _handler(runtime, get_resume), methods=["GET"]),
        Route(
            "/api/sessions/{session_id}/candidate",
            _handler(runtime, build_candidate),
            methods=["POST"],
        ),
        Route(
            "/api/sessions/{session_id}/candidate/interaction",
            _handler(runtime, get_candidate_interaction),
            methods=["GET"],
        ),
        Route(
            "/api/sessions/{session_id}/candidate/interaction",
            _handler(runtime, submit_candidate_interaction),
            methods=["POST"],
        ),
        Route("/api/candidate/diff", _handler(runtime, get_candidate_diff), methods=["GET"]),
        Route(
            "/api/candidate/{snapshot_id}",
            _handler(runtime, get_candidate),
            methods=["GET"],
        ),
    ]
    application = Starlette(debug=False, routes=routes)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
        ],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type"],
    )
    application.state.runtime = runtime
    return application


app = create_app(os.getenv("CAMPUS_WEB_DATA_ROOT"))

from __future__ import annotations

from pathlib import Path

from starlette.testclient import TestClient

from apps.web.server.app import create_app


def _text_pdf(path: Path, text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 11 Tf 36 740 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode()
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for object_id, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{object_id} 0 obj\n".encode())
        output.extend(body)
        output.extend(b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode())
    output.extend(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n"
        ).encode()
    )
    path.write_bytes(bytes(output))


def test_local_web_runs_existing_resume_to_candidate_vertical(tmp_path) -> None:
    pdf = tmp_path / "resume.pdf"
    _text_pdf(
        pdf,
        "Anonymous Candidate University 2027 Project Agent platform "
        "responsibility implemented Python LangGraph RAG evaluation evidence. " * 3,
    )
    client = TestClient(create_app(tmp_path / "data"))
    session_id = client.post("/api/sessions", json={}).json()["data"]["session"][
        "session_id"
    ]

    with pdf.open("rb") as handle:
        imported = client.post(
            f"/api/sessions/{session_id}/resume",
            files={"file": ("resume.pdf", handle, "application/pdf")},
        )
    assert imported.status_code == 200
    assert imported.json()["data"]["result"]["status"] == "interrupted"

    reviewed_sections: list[str] = []
    for index in range(40):
        workspace = client.get(
            f"/api/sessions/{session_id}/workspace"
        ).json()["data"]
        review = workspace["resume_review"]
        if review is None:
            break
        reviewed_sections.append(review["view"]["section"])
        response = client.post(
            f"/api/sessions/{session_id}/resume/review",
            json={"action": "confirm", "response_id": f"web-review-{index}"},
        )
        assert response.status_code == 200

    workspace = client.get(f"/api/sessions/{session_id}/workspace").json()["data"]
    assert reviewed_sections
    assert workspace["resume"]["status"] == "confirmed"
    assert workspace["session"]["next_action"] == "candidate.build"

    built = client.post(f"/api/sessions/{session_id}/candidate")
    assert built.status_code == 200
    assert built.json()["data"]["result"]["status"] == "completed"
    workspace = built.json()["data"]["workspace"]
    assert workspace["candidate_profile"]["profile_data"]["capabilities"]
    assert workspace["session"]["next_action"] == "candidate.view"


def test_local_web_exposes_candidate_question_and_resumes_idempotently(tmp_path) -> None:
    pdf = tmp_path / "resume-needs-clarification.pdf"
    _text_pdf(
        pdf,
        "Candidate University 2027 Project Agent Python LangGraph. " * 4,
    )
    client = TestClient(create_app(tmp_path / "data"))
    session_id = client.post("/api/sessions", json={}).json()["data"]["session"][
        "session_id"
    ]
    with pdf.open("rb") as handle:
        imported = client.post(
            f"/api/sessions/{session_id}/resume",
            files={"file": ("resume.pdf", handle, "application/pdf")},
        )
    assert imported.status_code == 200

    for index in range(40):
        workspace = client.get(
            f"/api/sessions/{session_id}/workspace"
        ).json()["data"]
        review = workspace["resume_review"]
        if review is None:
            break
        response_id = f"clarification-review-{index}"
        first = client.post(
            f"/api/sessions/{session_id}/resume/review",
            json={"action": "confirm", "response_id": response_id},
        )
        assert first.status_code == 200
        replay = client.post(
            f"/api/sessions/{session_id}/resume/review",
            json={"action": "confirm", "response_id": response_id},
        )
        assert replay.status_code == 200
        assert replay.json()["data"]["result"]["deduplicated"] is True

    built = client.post(f"/api/sessions/{session_id}/candidate")
    assert built.status_code == 200
    assert built.json()["data"]["result"]["status"] == "interrupted"
    interaction = built.json()["data"]["workspace"]["candidate_interaction"]
    assert interaction["interaction_type"] == "answer_questions"
    question = interaction["questions"][0]

    answer_payload = {
        "action": "answer",
        "response_id": "candidate-answer-once",
        "answers": [
            {
                "question_id": question["question_id"],
                "text": (
                    "I implemented the LangGraph workflow and tests; "
                    "other team members handled crawling."
                ),
            }
        ],
    }
    answered = client.post(
        f"/api/sessions/{session_id}/candidate/interaction", json=answer_payload
    )
    assert answered.status_code == 200
    assert answered.json()["data"]["result"]["status"] == "completed"
    assert answered.json()["data"]["workspace"]["candidate_profile"] is not None
    history_count = len(answered.json()["data"]["workspace"]["profile_history"])

    replay = client.post(
        f"/api/sessions/{session_id}/candidate/interaction", json=answer_payload
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["result"]["deduplicated"] is True
    assert len(replay.json()["data"]["workspace"]["profile_history"]) == history_count

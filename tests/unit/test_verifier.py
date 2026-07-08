from campus_job_agent.agent.verifier import verify_state


def test_verifier_passes_valid_state():
    state = {
        "parsed_goal": {"role_query": "AI Agent"},
        "plan": [{"task_id": "task_001"}],
        "tool_results": [
            {
                "tool_name": "mock_job_search",
                "status": "success",
                "records": [{"company": "Mock Company"}],
                "evidence_ids": [],
                "error": None,
                "metadata": {},
            }
        ],
    }

    verification = verify_state(state)

    assert verification.passed is True
    assert all(verification.checks.values())


def test_verifier_fails_missing_plan_and_tool_results():
    state = {
        "parsed_goal": {"role_query": "AI Agent"},
        "plan": [],
        "tool_results": [],
    }

    verification = verify_state(state)

    assert verification.passed is False
    assert verification.checks["plan_non_empty"] is False
    assert verification.checks["tool_results_non_empty"] is False
    assert verification.checks["tool_result_fields_valid"] is False

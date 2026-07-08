from campus_job_agent.tools import MockJobSearchTool, ToolRegistry


def test_tool_registry_runs_registered_tool():
    registry = ToolRegistry()
    registry.register(MockJobSearchTool())

    result = registry.run(
        "mock_job_search",
        {"role_query": "AI Agent", "city": "成都", "graduation_year": "2027"},
    )

    assert result.status == "success"
    assert result.tool_name == "mock_job_search"
    assert len(result.records) >= 2


def test_tool_registry_unregistered_tool_returns_failed_result():
    registry = ToolRegistry()

    result = registry.run("missing_tool", {})

    assert result.status == "failed"
    assert result.tool_name == "missing_tool"
    assert result.records == []
    assert result.error == "Tool not registered: missing_tool"

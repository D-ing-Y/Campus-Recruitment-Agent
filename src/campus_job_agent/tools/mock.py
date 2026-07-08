"""Mock tools for v0.1."""

from typing import Any

from campus_job_agent.schemas import ToolResult


class MockJobSearchTool:
    name = "mock_job_search"

    def run(self, args: dict[str, Any]) -> ToolResult:
        return ToolResult(
            tool_name=self.name,
            status="success",
            records=[
                {
                    "company": "Mock Company",
                    "role_title": "AI Agent Intern",
                    "city": "成都",
                    "graduation_year": args.get("graduation_year", "unknown"),
                    "requirements": ["Python", "LLM", "Agent"],
                },
                {
                    "company": "Noise Tech",
                    "role_title": "Backend Intern",
                    "city": "上海",
                    "graduation_year": "2027",
                    "requirements": ["Python", "SQL"],
                },
            ],
            evidence_ids=[],
            metadata={
                "role_query": args.get("role_query", "unknown"),
                "city": args.get("city", "unknown"),
                "graduation_year": args.get("graduation_year", "unknown"),
            },
        )

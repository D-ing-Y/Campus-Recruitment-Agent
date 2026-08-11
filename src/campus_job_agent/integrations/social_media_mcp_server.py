"""FastMCP entry point exposing the read-only social bridge allowlist."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from campus_job_agent.integrations.social_media import (
    MediaCrawlerSidecarClient,
    MediaCrawlerSidecarConfig,
)


def build_social_mcp_server(client: MediaCrawlerSidecarClient) -> FastMCP:
    server = FastMCP("campus-agent-social")

    @server.tool(name="social.health")
    def social_health() -> dict:
        return client.health()

    @server.tool(name="social.auth_status")
    def social_auth_status() -> dict:
        return client.auth_status()

    @server.tool(name="social.search_posts")
    def social_search_posts(keywords: str, limit: int = 3) -> dict:
        return client.search_posts(keywords=keywords, limit=limit)

    @server.tool(name="social.fetch_post_detail")
    def social_fetch_post_detail(candidate_ref: str) -> dict:
        return client.fetch_post_detail(candidate_ref=candidate_ref)

    return server


def main() -> None:
    client = MediaCrawlerSidecarClient(MediaCrawlerSidecarConfig.from_env())
    build_social_mcp_server(client).run(transport="stdio")


__all__ = ["build_social_mcp_server", "main"]

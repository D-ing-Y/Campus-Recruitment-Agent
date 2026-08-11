"""External integration adapters."""

from campus_job_agent.integrations.mcp import MCPToolCatalog
from campus_job_agent.integrations.social_media import (
    MediaCrawlerSidecarClient,
    MediaCrawlerSidecarConfig,
    SocialBridgeError,
)

__all__ = [
    "MCPToolCatalog", "MediaCrawlerSidecarClient", "MediaCrawlerSidecarConfig",
    "SocialBridgeError",
]

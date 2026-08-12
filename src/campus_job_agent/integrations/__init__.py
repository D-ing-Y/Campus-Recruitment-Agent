"""External integration adapters."""

from campus_job_agent.integrations.mcp import MCPToolCatalog
from campus_job_agent.integrations.community_retrieval import (
    BraveSearchClient,
    CommunityFetchResult,
    CommunityRetrievalError,
    Crawl4AICommunityFetcher,
)
from campus_job_agent.integrations.social_media import (
    MediaCrawlerSidecarClient,
    MediaCrawlerSidecarConfig,
    SocialBridgeError,
)

__all__ = [
    "BraveSearchClient", "CommunityFetchResult", "CommunityRetrievalError",
    "Crawl4AICommunityFetcher", "MCPToolCatalog", "MediaCrawlerSidecarClient", "MediaCrawlerSidecarConfig",
    "SocialBridgeError",
]

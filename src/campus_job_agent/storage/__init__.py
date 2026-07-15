"""Persistence adapters for evidence and versioned profiles."""

from campus_job_agent.storage.base import (
    BlobStore,
    EvidenceRepository,
    ProfileRepository,
)
from campus_job_agent.storage.local_blob import LocalBlobStore
from campus_job_agent.storage.sqlite import SQLiteRepository

__all__ = [
    "BlobStore",
    "EvidenceRepository",
    "ProfileRepository",
    "LocalBlobStore",
    "SQLiteRepository",
]

"""SQLite namespace for immutable preparation objects and plan history."""

from campus_job_agent.workflows._v07_repository import SQLiteV07Repository


class SQLitePreparationRepository(SQLiteV07Repository):
    table = "preparation_records"
    namespace = "preparation"


__all__ = ["SQLitePreparationRepository"]

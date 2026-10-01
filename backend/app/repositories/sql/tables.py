"""Relational schema — the structured source of truth (ADR-001, ADR-005).

Scalar fields are real columns so they can be indexed and queried; nested value
objects (attribute maps, geometry, provenance) are JSON. Every table carries ``seq``,
a repository-assigned insertion counter used to order rows deterministically when
timestamps tie. Schema changes must ship with an Alembic migration in
``database/migrations``.
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer, MetaData, String, Table, Text
from sqlalchemy.types import TypeDecorator


class UTCDateTimeType(TypeDecorator):
    """Stores UTC. Postgres keeps tz-aware values; SQLite stores naive UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        return value if dialect.name == "postgresql" else value.replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, str):
            value = datetime.fromisoformat(value)
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


metadata = MetaData()


def _seq() -> Column:
    return Column("seq", Integer, nullable=False, index=True)


entities = Table(
    "entities",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("type", String(64), nullable=False, index=True),
    Column("name", String(256)),
    Column("canonical_attributes", JSON, nullable=False),
    Column("geometry", JSON),
    Column("anchor", String(128), index=True),
    Column("current_state", JSON, nullable=False),
    Column("attribute_statuses", JSON, nullable=False),
    Column("status", String(32), nullable=False),
    Column("observed_at", UTCDateTimeType, nullable=False),
    Column("freshness_policies", JSON, nullable=False),
    Column("evidence_refs", JSON, nullable=False),
    Column("history_refs", JSON, nullable=False),
    Column("permissions", JSON, nullable=False),
    Column("identity_status", String(32), nullable=False, server_default="ESTABLISHED"),
    Column("identity_candidates", JSON, nullable=False, server_default="[]"),
    Column("created_at", UTCDateTimeType, nullable=False),
    Column("updated_at", UTCDateTimeType, nullable=False),
)

observations = Table(
    "observations",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("timestamp", UTCDateTimeType, nullable=False, index=True),
    Column("source", String(128), nullable=False),
    Column("session_id", String(128), index=True),
    Column("raw_reference", Text),
    Column("observed_entities", JSON, nullable=False),
    Column("spatial_context", JSON, nullable=False),
    Column("quality", Float, nullable=False),
    Column("authority", Float, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("resolutions", JSON, nullable=False, server_default="[]"),
)

evidence = Table(
    "evidence",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("source_type", String(64), nullable=False, index=True),
    Column("source_reference", String(256), nullable=False, index=True),
    Column("timestamp", UTCDateTimeType, nullable=False, index=True),
    Column("quality", Float, nullable=False),
    Column("authority", Float, nullable=False),
    Column("provenance", JSON, nullable=False),
    Column("retention_policy", String(64)),
    Column("integrity_reference", String(128)),
)

state_versions = Table(
    "state_versions",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("entity_id", String(128), nullable=False, index=True),
    Column("attribute", String(128), nullable=False, index=True),
    Column("value", JSON),
    Column("status", String(32), nullable=False),
    Column("valid_from", UTCDateTimeType, nullable=False, index=True),
    Column("valid_to", UTCDateTimeType),
    Column("supported_by", JSON, nullable=False),
    Column("invalidation_reason", Text),
)

events = Table(
    "events",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("timestamp", UTCDateTimeType, nullable=False, index=True),
    Column("event_type", String(64), nullable=False, index=True),
    Column("entity_id", String(128), index=True),
    Column("relation_id", String(128)),
    Column("before_state", JSON),
    Column("after_state", JSON),
    Column("evidence_refs", JSON, nullable=False),
    Column("description", Text),
)

relations = Table(
    "relations",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("source_entity", String(128), nullable=False, index=True),
    Column("relation_type", String(64), nullable=False),
    Column("target_entity", String(128), nullable=False, index=True),
    Column("valid_from", UTCDateTimeType, nullable=False),
    Column("valid_to", UTCDateTimeType),
    Column("status", String(32), nullable=False),
    Column("evidence_refs", JSON, nullable=False),
)

tasks = Table(
    "tasks",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("goal", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", UTCDateTimeType, nullable=False),
    Column("updated_at", UTCDateTimeType, nullable=False),
)

task_steps = Table(
    "task_steps",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("task_id", String(128), nullable=False, index=True),
    Column("step_order", Integer, nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String(32), nullable=False),
    Column("dependencies", JSON, nullable=False),
    Column("preconditions", JSON, nullable=False),
    Column("evidence_refs", JSON, nullable=False),
    Column("blocked_reason", Text),
    Column("completed_at", UTCDateTimeType),
)

world_diffs = Table(
    "world_diffs",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("baseline_timestamp", UTCDateTimeType),
    Column("target_timestamp", UTCDateTimeType, nullable=False),
    Column("changes", JSON, nullable=False),
    Column("created_at", UTCDateTimeType, nullable=False),
)

search_coverage = Table(
    "search_coverage",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("region", String(128), nullable=False, index=True),
    Column("timestamp", UTCDateTimeType, nullable=False, index=True),
    Column("visibility_conditions", JSON, nullable=False),
    Column("searched_for", JSON, nullable=False),
    Column("result", String(64), nullable=False),
    Column("confidence", Float, nullable=False),
    Column("evidence_refs", JSON, nullable=False),
)

anchors = Table(
    "anchors",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("name", String(256)),
    Column("anchor_type", String(64), nullable=False),
    Column("parent_id", String(128), index=True),
    Column("frame", JSON, nullable=False),
    Column("created_at", UTCDateTimeType, nullable=False),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(128), primary_key=True),
    _seq(),
    Column("label", String(256)),
    Column("actor", String(128)),
    Column("started_at", UTCDateTimeType, nullable=False, index=True),
    Column("ended_at", UTCDateTimeType),
    Column("last_observation_at", UTCDateTimeType),
)

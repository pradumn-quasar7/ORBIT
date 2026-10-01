"""SQLAlchemy Core implementation of the Repository boundary.

Works against SQLite (dev/test) and PostgreSQL (target deployment) with the same
schema. Rows are converted to and from domain models generically: JSON columns hold
the JSON-mode dump of a field, every other column holds the Python value.
"""
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Type, TypeVar

from pydantic import BaseModel
from sqlalchemy import Table, create_engine, func, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import StaticPool

from backend.app.domain.models import (
    Anchor,
    Entity,
    Event,
    Evidence,
    Observation,
    Relation,
    SearchCoverage,
    Session,
    StateVersion,
    Task,
    TaskStep,
    WorldDiff,
)
from backend.app.repositories.base import Repository
from backend.app.repositories.sql import tables as t
from backend.app.repositories.sql.tables import UTCDateTimeType

M = TypeVar("M", bound=BaseModel)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        kwargs: Dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs["poolclass"] = StaticPool
        return create_engine(url, **kwargs)
    return create_engine(url, pool_pre_ping=True)


class SqlRepository(Repository):
    def __init__(self, url: str = "sqlite://", engine: Optional[Engine] = None, create_schema: bool = False):
        self.engine = engine or make_engine(url)
        self._local = threading.local()
        if create_schema:
            t.metadata.create_all(self.engine)

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def transaction(self) -> Iterator[None]:
        if getattr(self._local, "conn", None) is not None:
            yield
            return
        with self.engine.begin() as conn:
            self._local.conn = conn
            try:
                yield
            finally:
                self._local.conn = None

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            yield conn
        else:
            with self.engine.begin() as fresh:
                yield fresh

    @staticmethod
    def _to_row(table: Table, model: BaseModel) -> Dict[str, Any]:
        py = model.model_dump(mode="python")
        js = model.model_dump(mode="json")
        row: Dict[str, Any] = {}
        for col in table.columns:
            if col.name not in py:
                continue
            if isinstance(col.type, UTCDateTimeType):
                row[col.name] = py[col.name]
            else:
                # JSON-mode values: JSON-safe nested data, enum values as strings.
                row[col.name] = js[col.name]
        return row

    @staticmethod
    def _from_row(model_cls: Type[M], row: Any) -> M:
        data = {k: v for k, v in row._mapping.items() if k in model_cls.model_fields}
        return model_cls.model_validate(data)

    def _upsert(self, table: Table, model: BaseModel) -> None:
        row = self._to_row(table, model)
        with self._connection() as conn:
            exists = conn.execute(select(table.c.id).where(table.c.id == row["id"])).first()
            if exists:
                conn.execute(table.update().where(table.c.id == row["id"]).values(**row))
            else:
                next_seq = conn.execute(select(func.coalesce(func.max(table.c.seq), 0) + 1)).scalar_one()
                conn.execute(table.insert().values(seq=next_seq, **row))

    def _get_one(self, table: Table, model_cls: Type[M], key: str) -> Optional[M]:
        with self._connection() as conn:
            row = conn.execute(select(table).where(table.c.id == key)).first()
        return self._from_row(model_cls, row) if row else None

    def _list(self, table: Table, model_cls: Type[M], order: Sequence[Any], where: Any = None) -> List[M]:
        stmt = select(table)
        if where is not None:
            stmt = stmt.where(where)
        stmt = stmt.order_by(*order, table.c.seq)
        with self._connection() as conn:
            rows = conn.execute(stmt).fetchall()
        return [self._from_row(model_cls, r) for r in rows]

    # ------------------------------------------------------------------ entities
    def save_entity(self, entity: Entity) -> Entity:
        self._upsert(t.entities, entity)
        return entity

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        return self._get_one(t.entities, Entity, entity_id)

    def list_entities(self) -> List[Entity]:
        return self._list(t.entities, Entity, [])

    # -------------------------------------------------------------- observations
    def save_observation(self, observation: Observation) -> Observation:
        self._upsert(t.observations, observation)
        return observation

    def get_observation(self, observation_id: str) -> Optional[Observation]:
        return self._get_one(t.observations, Observation, observation_id)

    def list_observations(self) -> List[Observation]:
        return self._list(t.observations, Observation, [t.observations.c.timestamp])

    def list_observations_for_session(self, session_id: str) -> List[Observation]:
        return self._list(
            t.observations, Observation, [t.observations.c.timestamp], t.observations.c.session_id == session_id
        )

    # ------------------------------------------------------------------ evidence
    def save_evidence(self, evidence: Evidence) -> Evidence:
        self._upsert(t.evidence, evidence)
        return evidence

    def get_evidence(self, evidence_id: str) -> Optional[Evidence]:
        return self._get_one(t.evidence, Evidence, evidence_id)

    def list_evidence(self) -> List[Evidence]:
        return self._list(t.evidence, Evidence, [t.evidence.c.timestamp])

    # ------------------------------------------------------------ state versions
    def save_state_version(self, sv: StateVersion) -> StateVersion:
        self._upsert(t.state_versions, sv)
        return sv

    def get_state_versions_for_entity(
        self, entity_id: str, attribute: Optional[str] = None
    ) -> List[StateVersion]:
        cond = t.state_versions.c.entity_id == entity_id
        if attribute is not None:
            cond = cond & (t.state_versions.c.attribute == attribute)
        return self._list(t.state_versions, StateVersion, [t.state_versions.c.valid_from], cond)

    # -------------------------------------------------------------------- events
    def save_event(self, event: Event) -> Event:
        self._upsert(t.events, event)
        return event

    def list_events(self) -> List[Event]:
        return self._list(t.events, Event, [t.events.c.timestamp])

    def get_events_for_entity(self, entity_id: str) -> List[Event]:
        return self._list(t.events, Event, [t.events.c.timestamp], t.events.c.entity_id == entity_id)

    # ----------------------------------------------------------------- relations
    def save_relation(self, relation: Relation) -> Relation:
        self._upsert(t.relations, relation)
        return relation

    def list_relations(self) -> List[Relation]:
        return self._list(t.relations, Relation, [t.relations.c.valid_from])

    def get_relations_for_entity(self, entity_id: str) -> List[Relation]:
        cond = (t.relations.c.source_entity == entity_id) | (t.relations.c.target_entity == entity_id)
        return self._list(t.relations, Relation, [t.relations.c.valid_from], cond)

    # ------------------------------------------------------------------- anchors
    def save_anchor(self, anchor: Anchor) -> Anchor:
        self._upsert(t.anchors, anchor)
        return anchor

    def get_anchor(self, anchor_id: str) -> Optional[Anchor]:
        return self._get_one(t.anchors, Anchor, anchor_id)

    def list_anchors(self) -> List[Anchor]:
        return self._list(t.anchors, Anchor, [])

    # ------------------------------------------------------------------ sessions
    def save_session(self, session: Session) -> Session:
        self._upsert(t.sessions, session)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._get_one(t.sessions, Session, session_id)

    def list_sessions(self) -> List[Session]:
        return self._list(t.sessions, Session, [t.sessions.c.started_at])

    # --------------------------------------------------------------------- tasks
    def save_task(self, task: Task) -> Task:
        with self.transaction():
            self._upsert(t.tasks, task)
            with self._connection() as conn:
                keep = [s.id for s in task.steps]
                conn.execute(
                    t.task_steps.delete().where(
                        (t.task_steps.c.task_id == task.id) & (t.task_steps.c.id.notin_(keep))
                    )
                )
            for step in task.steps:
                self._upsert(t.task_steps, step)
        return task

    def _assemble_task(self, row: Any) -> Task:
        steps = self._list(
            t.task_steps, TaskStep, [t.task_steps.c.step_order], t.task_steps.c.task_id == row._mapping["id"]
        )
        data = {k: v for k, v in row._mapping.items() if k in Task.model_fields}
        data["steps"] = [s.model_dump() for s in steps]
        return Task.model_validate(data)

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._connection() as conn:
            row = conn.execute(select(t.tasks).where(t.tasks.c.id == task_id)).first()
        return self._assemble_task(row) if row else None

    def list_tasks(self) -> List[Task]:
        with self._connection() as conn:
            rows = conn.execute(select(t.tasks).order_by(t.tasks.c.created_at, t.tasks.c.seq)).fetchall()
        return [self._assemble_task(r) for r in rows]

    # --------------------------------------------------------------- world diffs
    def save_world_diff(self, diff: WorldDiff) -> WorldDiff:
        self._upsert(t.world_diffs, diff)
        return diff

    def get_world_diff(self, diff_id: str) -> Optional[WorldDiff]:
        return self._get_one(t.world_diffs, WorldDiff, diff_id)

    # ------------------------------------------------------------ search coverage
    def save_search_coverage(self, coverage: SearchCoverage) -> SearchCoverage:
        self._upsert(t.search_coverage, coverage)
        return coverage

    def list_search_coverage(self) -> List[SearchCoverage]:
        return self._list(t.search_coverage, SearchCoverage, [t.search_coverage.c.timestamp])


__all__ = ["SqlRepository", "make_engine"]

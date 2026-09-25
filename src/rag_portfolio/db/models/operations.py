from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Text, UniqueConstraint, Uuid, func, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rag_portfolio.db.base import Base, Created, Identity, Tenant


class SourceAsset(Identity, Tenant, Created, Base):
    __tablename__ = "source_asset"
    __table_args__ = (
        UniqueConstraint("tenant_id", "storage_key"),
        UniqueConstraint("id", "tenant_id"),
        CheckConstraint("size_bytes >= 0", name="nonnegative_size"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="sha256"),
    )
    storage_key: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int]
    source_locator: Mapped[str] = mapped_column(Text)


class BackgroundJob(Identity, Tenant, Created, Base):
    __tablename__ = "background_job"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key"),
        CheckConstraint("status IN ('queued','running','succeeded','failed')", name="status"),
        CheckConstraint("attempts >= 0", name="attempts"),
        CheckConstraint(
            "(status = 'running' AND lease_until IS NOT NULL AND lease_token IS NOT NULL) "
            "OR (status <> 'running' AND lease_until IS NULL AND lease_token IS NULL)",
            name="lease_state",
        ),
        Index("ix_job_pending", "status", "next_run_at"),
        Index("ix_job_expired", "status", "lease_until"),
    )
    kind: Mapped[str] = mapped_column(Text)
    aggregate_id: Mapped[UUID | None]
    dedupe_key: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    result_json: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    attempts: Mapped[int] = mapped_column(server_default=text("0"))
    next_run_at: Mapped[datetime] = mapped_column(server_default=func.now())
    lease_until: Mapped[datetime | None]
    lease_token: Mapped[UUID | None]
    last_error_code: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None]


class AuditEvent(Identity, Tenant, Created, Base):
    __tablename__ = "audit_event"
    __table_args__ = (
        Index("ix_audit_entity", "tenant_id", "entity_type", "entity_id", "created_at"),
    )
    actor_id: Mapped[str] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str] = mapped_column(Text)
    entity_id: Mapped[UUID]
    old_ref: Mapped[str | None] = mapped_column(Text)
    new_ref: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    request_id: Mapped[UUID]


class RetrievalTrace(Identity, Tenant, Created, Base):
    __tablename__ = "retrieval_trace"
    __table_args__ = (
        Index("ix_trace_request", "tenant_id", "request_id"),
        Index("ix_trace_created", "created_at"),
    )
    request_id: Mapped[UUID]
    release_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid))
    snapshot_id: Mapped[UUID | None]
    candidate_chunk_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid))
    returned_chunk_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid))
    scores_json: Mapped[dict] = mapped_column(JSONB)
    filter_summary: Mapped[dict] = mapped_column(JSONB)
    latency_json: Mapped[dict] = mapped_column(JSONB)
    failure_code: Mapped[str | None] = mapped_column(Text)

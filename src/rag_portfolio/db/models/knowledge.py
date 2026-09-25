from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rag_portfolio.db.base import Base, Created, Identity, Tenant


class KnowledgeBase(Identity, Tenant, Created, Base):
    __tablename__ = "knowledge_base"
    __table_args__ = (
        UniqueConstraint("tenant_id", "scope_key", "code"),
        UniqueConstraint("id", "tenant_id"),
        ForeignKeyConstraint(
            ["space_id", "tenant_id"], ["scene_space.id", "scene_space.tenant_id"]
        ),
        ForeignKeyConstraint(
            ["active_release_id", "id"],
            ["kb_release.id", "kb_release.kb_id"],
            name="fk_kb_active_release",
            use_alter=True,
        ),
        CheckConstraint("length(scope_key) > 0", name="scope_nonempty"),
        CheckConstraint("row_version > 0", name="row_version_positive"),
        CheckConstraint(
            "code IN ('rehab_kb','scene_kb','clinical_kb','device_kb','hospital_kb','sop_kb')",
            name="known_code",
        ),
        CheckConstraint("code <> 'scene_kb' OR space_id IS NOT NULL", name="scene_space_required"),
    )
    scope_key: Mapped[str] = mapped_column(Text)
    code: Mapped[str] = mapped_column(Text)
    hospital_id: Mapped[str | None] = mapped_column(Text)
    space_id: Mapped[UUID | None]
    enabled: Mapped[bool] = mapped_column(server_default=text("false"))
    active_release_id: Mapped[UUID | None]
    row_version: Mapped[int] = mapped_column(server_default=text("1"))


class SourceDocument(Identity, Tenant, Created, Base):
    __tablename__ = "source_document"
    __table_args__ = (
        UniqueConstraint("kb_id", "source_instance_id", "external_key"),
        UniqueConstraint("id", "kb_id", "tenant_id"),
        ForeignKeyConstraint(
            ["kb_id", "tenant_id"], ["knowledge_base.id", "knowledge_base.tenant_id"]
        ),
        CheckConstraint("lifecycle IN ('active','archived')", name="lifecycle"),
        Index("ix_document_kb_lifecycle", "kb_id", "lifecycle"),
    )
    kb_id: Mapped[UUID]
    title: Mapped[str] = mapped_column(Text)
    source_system: Mapped[str] = mapped_column(Text)
    source_instance_id: Mapped[str] = mapped_column(Text)
    external_key: Mapped[str] = mapped_column(Text)
    external_dataset_id: Mapped[str | None] = mapped_column(Text)
    external_document_id: Mapped[str | None] = mapped_column(Text)
    lifecycle: Mapped[str] = mapped_column(Text, server_default="active")
    deleted_at: Mapped[datetime | None]


class DocumentVersion(Identity, Tenant, Created, Base):
    __tablename__ = "document_version"
    __table_args__ = (
        UniqueConstraint("document_id", "revision"),
        UniqueConstraint("id", "document_id", "kb_id", "tenant_id"),
        UniqueConstraint("id", "tenant_id"),
        ForeignKeyConstraint(
            ["document_id", "kb_id", "tenant_id"],
            ["source_document.id", "source_document.kb_id", "source_document.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["original_asset_id", "tenant_id"],
            ["source_asset.id", "source_asset.tenant_id"],
        ),
        CheckConstraint("revision > 0 AND row_version > 0", name="positive_versions"),
        CheckConstraint("expires_at IS NULL OR expires_at > effective_at", name="valid_window"),
        CheckConstraint(
            "review_status IN ('draft','pending','approved','rejected')",
            name="review_status",
        ),
        CheckConstraint(
            "review_status <> 'approved' OR (reviewed_by IS NOT NULL "
            "AND length(trim(reviewed_by)) > 0 AND reviewed_at IS NOT NULL "
            "AND cardinality(audiences) > 0)",
            name="approval_provenance",
        ),
        CheckConstraint(
            "audiences <@ ARRAY['patient','doctor','internal']::text[]",
            name="audiences",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"),
        CheckConstraint("metadata_hash ~ '^[0-9a-f]{64}$'", name="metadata_hash"),
        Index("ix_version_document_revision", "document_id", "revision"),
    )
    document_id: Mapped[UUID]
    kb_id: Mapped[UUID]
    revision: Mapped[int]
    upstream_version: Mapped[str | None] = mapped_column(Text)
    original_asset_id: Mapped[UUID | None]
    normalized_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    metadata_hash: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    audiences: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'::text[]"))
    review_status: Mapped[str] = mapped_column(Text, server_default="draft")
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None]
    effective_at: Mapped[datetime]
    expires_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    row_version: Mapped[int] = mapped_column(server_default=text("1"))


class DocumentChunk(Identity, Tenant, Created, Base):
    __tablename__ = "document_chunk"
    __table_args__ = (
        UniqueConstraint("version_id", "chunker_version", "ordinal"),
        UniqueConstraint("id", "version_id", "chunker_version"),
        ForeignKeyConstraint(
            ["version_id", "tenant_id"],
            ["document_version.id", "document_version.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["parent_chunk_id", "version_id", "chunker_version"],
            ["document_chunk.id", "document_chunk.version_id", "document_chunk.chunker_version"],
        ),
        ForeignKeyConstraint(
            ["scene_snapshot_id", "tenant_id"],
            ["scene_snapshot.id", "scene_snapshot.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["scene_snapshot_id", "scene_node_id"],
            ["scene_node.snapshot_id", "scene_node.node_id"],
        ),
        CheckConstraint("ordinal >= 0 AND token_count >= 0", name="nonnegative_counts"),
        CheckConstraint("parent_chunk_id IS NULL OR parent_chunk_id <> id", name="not_own_parent"),
        CheckConstraint(
            "(scene_snapshot_id IS NULL) = (scene_node_id IS NULL)",
            name="scene_anchor_pair",
        ),
        CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="content_hash"),
    )
    version_id: Mapped[UUID]
    chunker_version: Mapped[str] = mapped_column(Text)
    ordinal: Mapped[int]
    parent_chunk_id: Mapped[UUID | None]
    section_path: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int]
    scene_node_id: Mapped[str | None] = mapped_column(Text)
    scene_snapshot_id: Mapped[UUID | None]


class KnowledgeRelease(Identity, Tenant, Created, Base):
    __tablename__ = "kb_release"
    __table_args__ = (
        UniqueConstraint("kb_id", "release_no"),
        UniqueConstraint("collection_name"),
        UniqueConstraint("id", "kb_id"),
        UniqueConstraint("id", "kb_id", "tenant_id"),
        ForeignKeyConstraint(
            ["kb_id", "tenant_id"], ["knowledge_base.id", "knowledge_base.tenant_id"]
        ),
        ForeignKeyConstraint(["base_release_id", "kb_id"], ["kb_release.id", "kb_release.kb_id"]),
        ForeignKeyConstraint(
            ["scene_snapshot_id", "tenant_id"],
            ["scene_snapshot.id", "scene_snapshot.tenant_id"],
        ),
        CheckConstraint("release_no > 0", name="positive_release"),
        CheckConstraint(
            "status IN ('queued','building','ready','active','retired','failed','conflict')",
            name="status",
        ),
        CheckConstraint("manifest_hash ~ '^[0-9a-f]{64}$'", name="manifest_hash"),
        CheckConstraint("status <> 'active' OR activated_at IS NOT NULL", name="activation_time"),
    )
    kb_id: Mapped[UUID]
    release_no: Mapped[int]
    base_release_id: Mapped[UUID | None]
    scene_snapshot_id: Mapped[UUID | None]
    manifest_hash: Mapped[str] = mapped_column(Text)
    collection_name: Mapped[str] = mapped_column(Text)
    embedding_revision: Mapped[str] = mapped_column(Text)
    reranker_revision: Mapped[str] = mapped_column(Text)
    chunker_version: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="queued")
    requested_effective_at: Mapped[datetime]
    activated_at: Mapped[datetime | None]
    retired_at: Mapped[datetime | None]


class ReleaseDocument(Tenant, Base):
    __tablename__ = "kb_release_document"
    __table_args__ = (
        ForeignKeyConstraint(
            ["release_id", "kb_id", "tenant_id"],
            ["kb_release.id", "kb_release.kb_id", "kb_release.tenant_id"],
        ),
        ForeignKeyConstraint(
            ["version_id", "document_id", "kb_id", "tenant_id"],
            [
                "document_version.id",
                "document_version.document_id",
                "document_version.kb_id",
                "document_version.tenant_id",
            ],
        ),
    )
    release_id: Mapped[UUID] = mapped_column(primary_key=True)
    document_id: Mapped[UUID] = mapped_column(primary_key=True)
    kb_id: Mapped[UUID]
    version_id: Mapped[UUID]

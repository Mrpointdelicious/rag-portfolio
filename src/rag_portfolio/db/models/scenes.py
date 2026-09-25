from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from rag_portfolio.db.base import Base, Created, Identity, Tenant


class SceneSpace(Identity, Tenant, Created, Base):
    __tablename__ = "scene_space"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_space_id"),
        UniqueConstraint("id", "tenant_id"),
        ForeignKeyConstraint(
            ["active_snapshot_id", "id"],
            ["scene_snapshot.id", "scene_snapshot.space_id"],
            name="fk_space_active_snapshot",
            use_alter=True,
        ),
        CheckConstraint("row_version > 0", name="row_version_positive"),
    )
    hospital_id: Mapped[str] = mapped_column(Text)
    external_space_id: Mapped[str] = mapped_column(Text)
    active_snapshot_id: Mapped[UUID | None]
    enabled: Mapped[bool] = mapped_column(server_default=text("false"))
    row_version: Mapped[int] = mapped_column(server_default=text("1"))


class SceneSnapshot(Identity, Tenant, Created, Base):
    __tablename__ = "scene_snapshot"
    __table_args__ = (
        UniqueConstraint("space_id", "revision"),
        UniqueConstraint("id", "space_id"),
        UniqueConstraint("id", "tenant_id"),
        UniqueConstraint("id", "upstream_catalog_version"),
        ForeignKeyConstraint(
            ["space_id", "tenant_id"], ["scene_space.id", "scene_space.tenant_id"]
        ),
        ForeignKeyConstraint(
            ["base_snapshot_id", "space_id"],
            ["scene_snapshot.id", "scene_snapshot.space_id"],
        ),
        CheckConstraint("revision > 0 AND upstream_catalog_version >= 0", name="versions"),
        CheckConstraint("status IN ('draft','approved','published','retired')", name="status"),
        CheckConstraint("source_hash ~ '^[0-9a-f]{64}$'", name="source_hash"),
        CheckConstraint(
            "status NOT IN ('approved','published') OR "
            "(reviewed_by IS NOT NULL AND length(trim(reviewed_by)) > 0)",
            name="reviewer",
        ),
        CheckConstraint("status <> 'published' OR published_at IS NOT NULL", name="publish_time"),
    )
    space_id: Mapped[UUID]
    revision: Mapped[int]
    base_snapshot_id: Mapped[UUID | None]
    upstream_catalog_version: Mapped[int]
    source_hash: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="draft")
    reviewed_by: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None]


class SceneNode(Base):
    __tablename__ = "scene_node"
    __table_args__ = (
        ForeignKeyConstraint(["snapshot_id"], ["scene_snapshot.id"]),
        ForeignKeyConstraint(
            ["snapshot_id", "parent_node_id"],
            ["scene_node.snapshot_id", "scene_node.node_id"],
        ),
        CheckConstraint("node_type IN ('hospital','floor','zone','room')", name="node_type"),
        CheckConstraint(
            "parent_node_id IS NULL OR parent_node_id <> node_id", name="not_own_parent"
        ),
    )
    snapshot_id: Mapped[UUID] = mapped_column(primary_key=True)
    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_node_id: Mapped[str | None] = mapped_column(Text)
    node_type: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    floor: Mapped[int | None]
    zone: Mapped[str | None] = mapped_column(Text)
    allowed_roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    source_locator: Mapped[str] = mapped_column(Text)


class SceneEdge(Base):
    __tablename__ = "scene_edge"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "from_node_id"],
            ["scene_node.snapshot_id", "scene_node.node_id"],
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "to_node_id"],
            ["scene_node.snapshot_id", "scene_node.node_id"],
        ),
        CheckConstraint("cost_seconds >= 0", name="nonnegative_cost"),
        CheckConstraint("edge_type IN ('walk','teleport','elevator')", name="edge_type"),
        Index("ix_edge_outgoing", "snapshot_id", "from_node_id"),
    )
    snapshot_id: Mapped[UUID] = mapped_column(primary_key=True)
    edge_id: Mapped[str] = mapped_column(Text, primary_key=True)
    from_node_id: Mapped[str] = mapped_column(Text)
    to_node_id: Mapped[str] = mapped_column(Text)
    edge_type: Mapped[str] = mapped_column(Text)
    cost_seconds: Mapped[Decimal] = mapped_column(Numeric(12, 3))
    allowed_roles: Mapped[list[str]] = mapped_column(
        ARRAY(Text), server_default=text("'{}'::text[]")
    )
    enabled: Mapped[bool] = mapped_column(server_default=text("false"))


class SceneObject(Base):
    __tablename__ = "scene_object"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "object_id", "node_id"),
        ForeignKeyConstraint(
            ["snapshot_id", "node_id"],
            ["scene_node.snapshot_id", "scene_node.node_id"],
        ),
        Index("ix_object_node", "snapshot_id", "node_id"),
    )
    snapshot_id: Mapped[UUID] = mapped_column(primary_key=True)
    object_id: Mapped[str] = mapped_column(Text, primary_key=True)
    node_id: Mapped[str] = mapped_column(Text)
    object_type: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)
    capabilities_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    source_locator: Mapped[str] = mapped_column(Text)


class SceneService(Identity, Base):
    __tablename__ = "scene_service"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id",
            "node_id",
            "service_code",
            "object_id",
            postgresql_nulls_not_distinct=True,
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "node_id"],
            ["scene_node.snapshot_id", "scene_node.node_id"],
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "object_id", "node_id"],
            ["scene_object.snapshot_id", "scene_object.object_id", "scene_object.node_id"],
        ),
        Index("ix_service_lookup", "snapshot_id", "service_code", "node_id"),
    )
    snapshot_id: Mapped[UUID]
    node_id: Mapped[str] = mapped_column(Text)
    service_code: Mapped[str] = mapped_column(Text)
    object_id: Mapped[str | None] = mapped_column(Text)


class SceneAction(Base):
    __tablename__ = "scene_action"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "node_id"],
            ["scene_node.snapshot_id", "scene_node.node_id"],
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "object_id", "node_id"],
            ["scene_object.snapshot_id", "scene_object.object_id", "scene_object.node_id"],
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "upstream_catalog_version"],
            ["scene_snapshot.id", "scene_snapshot.upstream_catalog_version"],
        ),
        CheckConstraint("command_index >= 0", name="nonnegative_command"),
        CheckConstraint(
            "command_type IN ('Telepor','ScenePoint','ClickPoint','GamePoint')",
            name="command_type",
        ),
        CheckConstraint(
            "permission_level IN ('free','confirm','professional','forbidden')",
            name="permission",
        ),
    )
    snapshot_id: Mapped[UUID] = mapped_column(primary_key=True)
    action_id: Mapped[str] = mapped_column(Text, primary_key=True)
    node_id: Mapped[str] = mapped_column(Text)
    object_id: Mapped[str | None] = mapped_column(Text)
    action_key: Mapped[str] = mapped_column(Text)
    permission_level: Mapped[str] = mapped_column(Text, server_default="confirm")
    command_type: Mapped[str] = mapped_column(Text)
    command_index: Mapped[int]
    upstream_catalog_version: Mapped[int]

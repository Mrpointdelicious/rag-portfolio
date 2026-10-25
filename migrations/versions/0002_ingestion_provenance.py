"""Persist source anchors and resumable release build manifests."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "document_chunk",
        sa.Column("provenance_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )
    op.add_column(
        "kb_release",
        sa.Column("manifest_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
    )


def downgrade():
    op.drop_column("kb_release", "manifest_json")
    op.drop_column("document_chunk", "provenance_json")

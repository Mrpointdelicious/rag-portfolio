from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import insert, inspect, update
from sqlalchemy.exc import IntegrityError

from rag_portfolio.db.models import (
    Base,
    DocumentVersion,
    KnowledgeBase,
    KnowledgeRelease,
    SceneAction,
    SceneNode,
    SceneService,
    SceneSnapshot,
    SceneSpace,
    SourceDocument,
)

pytestmark = pytest.mark.integration


async def add(connection, model, **values):
    return (
        await connection.execute(insert(model).values(**values).returning(model.id))
    ).scalar_one()


async def base(connection, tenant, scope="shared"):
    return await add(connection, KnowledgeBase, tenant_id=tenant, scope_key=scope, code="rehab_kb")


async def document(connection, tenant, kb_id):
    return await add(
        connection,
        SourceDocument,
        tenant_id=tenant,
        kb_id=kb_id,
        title="fixture",
        source_system="test",
        source_instance_id="test",
        external_key=uuid4().hex,
    )


async def scene(connection, tenant):
    space = await add(
        connection, SceneSpace, tenant_id=tenant, hospital_id="h", external_space_id=uuid4().hex
    )
    snapshot = await add(
        connection,
        SceneSnapshot,
        tenant_id=tenant,
        space_id=space,
        revision=1,
        upstream_catalog_version=7,
        source_hash="a" * 64,
    )
    await connection.execute(
        insert(SceneNode).values(
            snapshot_id=snapshot,
            node_id="room",
            node_type="room",
            name="Test room",
            source_locator="test",
        )
    )
    return snapshot


async def test_all_tables_and_current_pointer_foreign_keys_exist(database):
    async with database.engine.connect() as connection:
        tables = await connection.run_sync(lambda conn: inspect(conn).get_table_names())
        assert set(tables) == {*Base.metadata.tables, "alembic_version"}
        for table, name in [
            ("knowledge_base", "fk_kb_active_release"),
            ("scene_space", "fk_space_active_snapshot"),
        ]:
            keys = await connection.run_sync(
                lambda conn, t=table: inspect(conn).get_foreign_keys(t)
            )
            assert name in {key["name"] for key in keys}


async def test_cross_tenant_document_is_rejected(database, tenant):
    async with database.engine.begin() as connection:
        kb = await base(connection, tenant)
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await document(connection, "other", kb)


async def test_active_release_cannot_reference_another_base(database, tenant):
    async with database.engine.begin() as connection:
        first = await base(connection, tenant)
        second = await base(connection, tenant, "other")
        release = await add(
            connection,
            KnowledgeRelease,
            tenant_id=tenant,
            kb_id=second,
            release_no=1,
            manifest_hash="a" * 64,
            collection_name=uuid4().hex,
            embedding_revision="test",
            reranker_revision="test",
            chunker_version="1",
            requested_effective_at=datetime.now(UTC),
        )
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    update(KnowledgeBase)
                    .where(KnowledgeBase.id == first)
                    .values(active_release_id=release)
                )


@pytest.mark.parametrize("invalid", ["missing_reviewer", "invalid_time_window"])
async def test_document_version_approval_and_time_constraints(database, tenant, invalid):
    async with database.engine.begin() as connection:
        kb = await base(connection, tenant)
        doc = await document(connection, tenant, kb)
        values = dict(
            tenant_id=tenant,
            kb_id=kb,
            document_id=doc,
            revision=1,
            normalized_text="test",
            content_hash="a" * 64,
            metadata_hash="b" * 64,
            effective_at=datetime.now(UTC),
        )
        if invalid == "missing_reviewer":
            values.update(review_status="approved", audiences=["patient"])
        else:
            values["expires_at"] = values["effective_at"] - timedelta(seconds=1)
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await add(connection, DocumentVersion, **values)


async def test_duplicate_room_service_with_null_object_is_rejected(database, tenant):
    async with database.engine.begin() as connection:
        snapshot = await scene(connection, tenant)
        await add(
            connection, SceneService, snapshot_id=snapshot, node_id="room", service_code="gait"
        )
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await add(
                    connection,
                    SceneService,
                    snapshot_id=snapshot,
                    node_id="room",
                    service_code="gait",
                )


async def test_action_must_bind_exact_upstream_version(database, tenant):
    async with database.engine.begin() as connection:
        snapshot = await scene(connection, tenant)
        with pytest.raises(IntegrityError):
            async with connection.begin_nested():
                await connection.execute(
                    insert(SceneAction).values(
                        snapshot_id=snapshot,
                        action_id="a",
                        node_id="room",
                        action_key="open",
                        command_type="ClickPoint",
                        command_index=0,
                        upstream_catalog_version=8,
                    )
                )

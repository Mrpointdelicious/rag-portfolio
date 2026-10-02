# 手动版本管理
# 该手动版本验证脚本为手动流程临时工具

import argparse
import asyncio
from uuid import UUID, uuid4

from sqlalchemy import select

from rag_portfolio.config import Settings
from rag_portfolio.db.models import DocumentVersion, SourceDocument
from rag_portfolio.db.session import Database
from rag_portfolio.event_loop import loop_factory
from rag_portfolio.services.documents import ActorContext, DocumentServiceError
from rag_portfolio.services.reviews import ReviewService


async def approve(version_id: UUID) -> int:
    settings = Settings()
    database = Database(settings)

    try:
        # 1. 先只读，展示即将审核的版本
        async with database.sessions() as session:
            row = (
                await session.execute(
                    select(DocumentVersion, SourceDocument)
                    .join(
                        SourceDocument,
                        SourceDocument.id == DocumentVersion.document_id,
                    )
                    .where(
                        DocumentVersion.id == version_id,
                        DocumentVersion.tenant_id == settings.tenant_id,
                    )
                )
            ).first()

            if row is None:
                print("Version not found.")
                return 1

            version, document = row

            print()
            print("Document Review")
            print("=" * 60)
            print(f"title:          {document.title}")
            print(f"external_key:   {document.external_key}")
            print(f"version_id:     {version.id}")
            print(f"revision:       {version.revision}")
            print(f"status:         {version.review_status}")
            print(f"row_version:    {version.row_version}")
            print(f"content_hash:   {version.content_hash}")
            print(f"metadata_hash:  {version.metadata_hash}")
            print(f"audiences:      {version.audiences}")
            print()
            print("Content")
            print("-" * 60)
            print(version.normalized_text)
            print("-" * 60)

            if version.review_status != "pending":
                print(f"Cannot approve: status is {version.review_status!r}")
                return 1

            # 保存我们实际审核过的版本状态
            expected_row_version = version.row_version
            expected_content_hash = version.content_hash
            expected_metadata_hash = version.metadata_hash

        # 2. 人工确认
        answer = input("\nType APPROVE to approve this exact version and generate chunks: ")

        if answer != "APPROVE":
            print("Cancelled.")
            return 0

        # 3. 真正批准，在一个事务中完成 approved + chunks
        try:
            async with database.sessions.begin() as session:
                service = ReviewService(
                    session,
                    ActorContext(
                        tenant_id=settings.tenant_id,
                        actor_id="manual-reviewer",
                        request_id=uuid4(),
                    ),
                )

                result = await service.approve_version(
                    version_id,
                    expected_row_version=expected_row_version,
                    expected_content_hash=expected_content_hash,
                    expected_metadata_hash=expected_metadata_hash,
                    reason="manual review: content verified",
                )

                print()
                print("Approved")
                print("=" * 60)
                print(f"version_id:     {result.version.id}")
                print(f"status:         {result.version.review_status}")
                print(f"reviewed_by:    {result.version.reviewed_by}")
                print(f"chunks_created: {len(result.chunks)}")

                print()
                print("Chunks")
                print("-" * 60)

                for chunk in result.chunks:
                    preview = chunk.text.replace("\n", " ")[:120]
                    print(
                        f"[{chunk.ordinal}] "
                        f"section={chunk.section_path!r} "
                        f"tokens≈{chunk.token_count} "
                        f"text={preview}"
                    )

        except DocumentServiceError as exc:
            print(f"Approval failed: {exc.code}")
            return 1

        return 0

    finally:
        await database.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("version_id", type=UUID)
    args = parser.parse_args()

    return asyncio.run(
        approve(args.version_id),
        loop_factory=loop_factory,
    )


if __name__ == "__main__":
    raise SystemExit(main())

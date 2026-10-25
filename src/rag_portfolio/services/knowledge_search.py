"""Business-facing facade over the existing authenticated retrieval service."""

from rag_portfolio.adapters.ingestion import token_ids
from rag_portfolio.contracts import RetrieveRequest
from rag_portfolio.knowledge_contract import KnowledgeSearchResponse, KnowledgeSource
from rag_portfolio.services.documents import DocumentServiceError
from rag_portfolio.services.retrieval import retrieve


async def search(database, vector, settings, principal, body, request_id):
    if body.domain == "health":
        # A caller's current hospital never restricts medical literature.
        kb_ids = settings.knowledge_global_kb_ids
        if not kb_ids:
            raise DocumentServiceError("knowledge_not_configured", 503)
    else:
        if not body.scope.space_id:
            raise DocumentServiceError("knowledge_space_required", 422)
        kb_ids = settings.knowledge_space_kb_ids.get(body.scope.space_id, {}).get(body.domain)
        if not kb_ids:
            # Do not reveal whether another hospital or corpus exists.
            raise DocumentServiceError("knowledge_scope_not_allowed", 403)

    budget = min(body.limits.max_context_tokens, settings.knowledge_max_context_tokens)
    result = await retrieve(
        database,
        vector,
        settings,
        principal,
        RetrieveRequest(
            kb_ids=kb_ids,
            queries=[{"q": body.query}],
            strategy=settings.knowledge_strategy,
            candidate_k=settings.knowledge_candidate_k,
            top_k=settings.knowledge_top_k,
            max_context_tokens=budget,
            rerank=True,
            answer_mode="evidence",
        ),
        request_id,
    )
    if result.decision == "unavailable":
        raise DocumentServiceError("knowledge_provider_unavailable", 503)
    if result.trace_id is None:
        raise DocumentServiceError("knowledge_trace_unavailable", 503)

    # Pack the complete model context, including labels, inside the advertised limit.
    blocks, sources, returned = [], [], set()
    for row in result.evidence:
        source_id = f"S{len(sources) + 1}"
        block = f"[{source_id}] {row.title}\n{row.text}"
        if len(token_ids("\n\n".join([*blocks, block]))) > budget:
            continue
        location = " · ".join(
            part
            for part in (
                row.source_locator,
                f"PDF 第 {row.page} 页" if row.page else "",
                row.section,
            )
            if part
        )
        sources.append(
            KnowledgeSource(
                id=source_id,
                title=row.title,
                locator=location or "原文分段",
                ref=f"{row.document_id}@{row.version_id}#{row.chunk_id}:{row.release_id}",
            )
        )
        blocks.append(block)
        returned.add(str(row.chunk_id))

    gaps = []
    if sources:
        if len(sources) != len(result.evidence) or any(
            not decision.get("covered", False)
            or not returned.intersection(decision.get("accepted_chunk_ids", []))
            for decision in result.query_decisions
        ):
            gaps.append("部分问题或证据未能在本次检索和上下文预算内覆盖。")
    else:
        gaps.append("未找到相关性足够且可在当前范围使用的已发布资料。")
    return KnowledgeSearchResponse(
        status="partial" if sources and gaps else "ok" if sources else "no_evidence",
        context="\n\n".join(blocks),
        sources=sources,
        gaps=gaps,
        trace_id=str(result.trace_id),
    )

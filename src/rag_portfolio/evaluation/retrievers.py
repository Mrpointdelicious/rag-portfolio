"""Offline replay plus shared runtime algorithms; replay is never imported by runtime."""

from uuid import UUID

from rag_portfolio.evaluation.corpus import EvaluationError
from rag_portfolio.services.retrieval_algorithms import (
    BM25_B as BM25_B,
)
from rag_portfolio.services.retrieval_algorithms import (
    BM25_K1 as BM25_K1,
)
from rag_portfolio.services.retrieval_algorithms import (
    CANDIDATE_DEPTH as CANDIDATE_DEPTH,
)
from rag_portfolio.services.retrieval_algorithms import (
    ROLE_BOOST as ROLE_BOOST,
)
from rag_portfolio.services.retrieval_algorithms import (
    RRF_K as RRF_K,
)
from rag_portfolio.services.retrieval_algorithms import (
    TOKEN_PATTERN as TOKEN_PATTERN,
)
from rag_portfolio.services.retrieval_algorithms import (
    TOKENIZER_VERSION as TOKENIZER_VERSION,
)
from rag_portfolio.services.retrieval_algorithms import (
    Candidate as Candidate,
)
from rag_portfolio.services.retrieval_algorithms import (
    DenseRetriever as DenseRetriever,
)
from rag_portfolio.services.retrieval_algorithms import (
    LocalBM25Retriever as LocalBM25Retriever,
)
from rag_portfolio.services.retrieval_algorithms import (
    RerankerPort as RerankerPort,
)
from rag_portfolio.services.retrieval_algorithms import (
    SparseRetriever as SparseRetriever,
)
from rag_portfolio.services.retrieval_algorithms import (
    reciprocal_rank_fusion as reciprocal_rank_fusion,
)
from rag_portfolio.services.retrieval_algorithms import (
    role_boost as role_boost,
)
from rag_portfolio.services.retrieval_algorithms import (
    tokenize as tokenize,
)


class ArchivedDenseRetriever:
    """Replay real, SHA-validated provider/Qdrant results, not a query-to-Gold implementation."""

    def __init__(self, report: dict):
        self.rankings = {
            row["query"]: [
                Candidate(
                    chunk_id=UUID(hit["chunk_id"]),
                    score=hit["score"],
                    dense_rank=hit["rank"],
                    dense_score=hit["score"],
                )
                for hit in row["top5"]
            ]
            for row in report["cases"]
        }

    async def search(self, query: str, *, limit: int = CANDIDATE_DEPTH) -> list[Candidate]:
        if not 1 <= limit <= CANDIDATE_DEPTH or query not in self.rankings:
            raise EvaluationError("Dense replay only supports the frozen queries and stored Top-5")
        return self.rankings[query][:limit]

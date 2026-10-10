"""Replaceable eval retrievers and pure fusion; no Gold or Qdrant-specific logic."""

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from rag_portfolio.evaluation.corpus import EvaluationError

CANDIDATE_DEPTH = 5  # Exact depth actually stored by the immutable Dense v1 report.
RRF_K = 60
BM25_K1 = 1.2
BM25_B = 0.75
ROLE_BOOST = 0.05
TOKENIZER_VERSION = "nfkc-casefold-han-unigram-bigram-ascii-v1"
TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]+|[a-z0-9]+")


@dataclass(frozen=True)
class Candidate:
    chunk_id: UUID
    score: float
    dense_rank: int | None = None
    dense_score: float | None = None
    sparse_rank: int | None = None
    sparse_score: float | None = None
    fused_rank: int | None = None
    metadata_boost: float = 0.0


class DenseRetriever(Protocol):
    async def search(self, query: str, *, limit: int) -> list[Candidate]: ...


class SparseRetriever(Protocol):
    async def search(self, query: str, *, limit: int) -> list[Candidate]: ...


class RerankerPort(Protocol):
    async def rerank(
        self, query: str, candidates: list[Candidate], *, limit: int
    ) -> list[Candidate]: ...


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


def tokenize(text: str) -> list[str]:
    """No dictionaries, stemming, synonyms or data-dependent preprocessing."""
    tokens = []
    for run in TOKEN_PATTERN.findall(unicodedata.normalize("NFKC", text).casefold()):
        if "\u3400" <= run[0] <= "\u9fff":
            tokens.extend(run)
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        else:
            tokens.append(run)
    return tokens


class LocalBM25Retriever:
    """Eval-only BM25 with fixed positive Robertson/Lucene-style IDF and deterministic ties."""

    def __init__(self, texts: dict[UUID, str]):
        if not texts:
            raise EvaluationError("BM25 requires a nonempty corpus")
        self.terms = {key: Counter(tokenize(text)) for key, text in texts.items()}
        self.lengths = {key: sum(terms.values()) for key, terms in self.terms.items()}
        self.avg_length = sum(self.lengths.values()) / len(texts)
        self.df = Counter(term for terms in self.terms.values() for term in terms)
        self.size = len(texts)

    async def search(self, query: str, *, limit: int = CANDIDATE_DEPTH) -> list[Candidate]:
        terms = sorted(set(tokenize(query)))  # Binary query term frequency is fixed for this eval.
        scores = []
        for key, counts in self.terms.items():
            score = 0.0
            for term in terms:
                tf = counts[term]
                if not tf:
                    continue
                df = self.df[term]
                idf = math.log1p((self.size - df + 0.5) / (df + 0.5))
                normalization = BM25_K1 * (
                    1 - BM25_B + BM25_B * self.lengths[key] / self.avg_length
                )
                score += idf * tf * (BM25_K1 + 1) / (tf + normalization)
            if score > 0:
                scores.append((key, score))
        scores.sort(key=lambda pair: (-pair[1], str(pair[0])))
        return [
            Candidate(key, score, sparse_rank=rank, sparse_score=score)
            for rank, (key, score) in enumerate(scores[:limit], 1)
        ]


def role_boost(
    candidates: list[Candidate], target_role: str | None, described_roles: dict[UUID, str | None]
) -> list[Candidate]:
    """No access-audience/requester filtering and no hospital/general authority ordering."""
    boosted = []
    for candidate in candidates:
        bonus = (
            ROLE_BOOST
            if target_role and described_roles[candidate.chunk_id] == target_role
            else 0.0
        )
        boosted.append(replace(candidate, score=candidate.score + bonus, metadata_boost=bonus))
    return sorted(
        boosted, key=lambda item: (-item.score, item.dense_rank or 10**9, str(item.chunk_id))
    )


def reciprocal_rank_fusion(
    dense: list[Candidate], sparse: list[Candidate], *, k: int = RRF_K
) -> list[Candidate]:
    if k <= 0:
        raise EvaluationError("RRF k must be positive")
    combined: dict[UUID, Candidate] = {}
    for name, ranking in (("dense", dense), ("sparse", sparse)):
        if len({item.chunk_id for item in ranking}) != len(ranking):
            raise EvaluationError("Duplicate candidates within a retriever ranking")
        for rank, candidate in enumerate(ranking, 1):
            old = combined.get(candidate.chunk_id, Candidate(candidate.chunk_id, 0.0))
            combined[candidate.chunk_id] = replace(
                old,
                score=old.score + 1 / (k + rank),
                **{f"{name}_rank": rank, f"{name}_score": candidate.score},
            )
    ordered = sorted(
        combined.values(),
        key=lambda item: (
            -item.score,
            item.dense_rank or 10**9,
            item.sparse_rank or 10**9,
            str(item.chunk_id),
        ),
    )
    return [replace(item, fused_rank=rank) for rank, item in enumerate(ordered, 1)]

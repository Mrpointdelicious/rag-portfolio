"""LlamaIndex's Qdrant integration with reproducible bilingual BM25 and explicit RRF."""

import math
from collections import Counter
from dataclasses import dataclass

from rag_portfolio.services.retrieval_algorithms import tokenize


@dataclass
class SparseEncoder:
    vocabulary: dict[str, int]
    frequencies: dict[str, int]
    count: int
    average_length: float

    @classmethod
    def build(cls, texts: list[str]):
        terms = [Counter(tokenize(text)) for text in texts]
        frequencies = Counter(term for document in terms for term in document)
        return cls(
            {term: i for i, term in enumerate(sorted(frequencies))},
            dict(frequencies),
            len(terms),
            sum(sum(row.values()) for row in terms) / max(1, len(terms)),
        )

    def documents(self, texts):
        indices, values = [], []
        for text in texts:
            counts = Counter(tokenize(text))
            length = sum(counts.values())
            pairs = []
            for term, tf in counts.items():
                if term not in self.vocabulary:
                    continue
                df = self.frequencies[term]
                idf = math.log1p((self.count - df + 0.5) / (df + 0.5))
                score = (
                    idf
                    * tf
                    * 2.2
                    / (tf + 1.2 * (0.25 + 0.75 * length / max(self.average_length, 1)))
                )
                pairs.append((self.vocabulary[term], score))
            pairs.sort()
            indices.append([pair[0] for pair in pairs])
            values.append([pair[1] for pair in pairs])
        return indices, values

    def queries(self, texts):
        indices = [
            sorted({self.vocabulary[t] for t in tokenize(text) if t in self.vocabulary})
            for text in texts
        ]
        return indices, [[1.0] * len(row) for row in indices]


def rrf_fusion(dense, sparse, *, alpha=0.5, top_k=40):
    from llama_index.core.vector_stores import VectorStoreQueryResult

    scores, nodes = {}, {}
    for result in (dense, sparse):
        for rank, node in enumerate(result.nodes or [], 1):
            nodes[node.node_id] = node
            scores[node.node_id] = scores.get(node.node_id, 0.0) + 1 / (60 + rank)
    ids = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
    return VectorStoreQueryResult(
        nodes=[nodes[key] for key in ids], ids=ids, similarities=[scores[key] for key in ids]
    )


def vector_store(client, collection: str, encoder: SparseEncoder):
    from llama_index.vector_stores.qdrant import QdrantVectorStore

    return QdrantVectorStore(
        collection_name=collection,
        aclient=client,
        enable_hybrid=True,
        dense_vector_name="text-dense",
        sparse_vector_name="text-sparse-new",
        sparse_doc_fn=encoder.documents,
        sparse_query_fn=encoder.queries,
        hybrid_fusion_fn=rrf_fusion,
    )


async def query_candidates(client, release, query, embedding, allowed_versions, strategy, limit):
    from llama_index.core.vector_stores import (
        FilterOperator,
        MetadataFilter,
        MetadataFilters,
        VectorStoreQuery,
    )
    from llama_index.core.vector_stores.types import VectorStoreQueryMode

    encoder = SparseEncoder(**release.manifest_json["sparse"])
    modes = {
        "dense": VectorStoreQueryMode.DEFAULT,
        "sparse": VectorStoreQueryMode.SPARSE,
        "hybrid": VectorStoreQueryMode.HYBRID,
    }
    store = vector_store(client, release.collection_name, encoder)
    return await store.aquery(
        VectorStoreQuery(
            query_str=query,
            query_embedding=embedding,
            mode=modes[strategy],
            similarity_top_k=limit,
            sparse_top_k=limit,
            hybrid_top_k=limit,
            filters=MetadataFilters(
                filters=[
                    MetadataFilter(key="tenant_id", value=release.tenant_id),
                    MetadataFilter(key="release_id", value=str(release.id)),
                    MetadataFilter(
                        key="version_id",
                        value=[str(v) for v in allowed_versions],
                        operator=FilterOperator.IN,
                    ),
                ]
            ),
        )
    )

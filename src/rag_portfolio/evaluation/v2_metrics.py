"""Evidence metrics and diagnostic answerability sweeps; no production thresholds."""

import math
from statistics import mean, median
from uuid import UUID

from rag_portfolio.evaluation.v2_schema import EvalCaseV2

QUALITY_KEYS = (
    "Recall@1",
    "Recall@3",
    "Recall@5",
    "MRR@5",
    "DocHit@1",
    "PreferredHit@1",
    "PreferredHit@3",
    "PreferredHit@5",
)
TAXONOMY = {
    "retrieval failure": "No acceptable evidence in Top-5 (candidate recall failure).",
    "ranking ambiguity": "Acceptable evidence below rank 1, or preferred below rank 1.",
    "source duplication": "Exact chunk-body duplicates across sources; no authority inference.",
    "source conflict": "Two annotated incompatible source claims; not a retrieval error.",
    "gold-definition issue": "Old Top1 excluded by old Gold but independently sufficient in v2.",
    "metadata issue": "Role case Top1 comes from outside acceptable documents; diagnostic signal.",
    "no-answer issue": "No corpus answer; retrieved related evidence is not answerability proof.",
}


def evaluate_case(case: EvalCaseV2, hits: list[dict]) -> dict:
    hits = hits[:5]
    ids = [UUID(hit["chunk_id"]) for hit in hits]

    def first_rank(evidence):
        return next((rank for rank, key in enumerate(ids, 1) if key in evidence), None)

    rank = first_rank(case.acceptable_chunk_ids)
    preferred = first_rank(case.preferred_chunk_ids)
    row = {
        **case.model_dump(mode="json"),
        "acceptable_rank": rank,
        "preferred_rank": preferred,
        "old_gold_rank": first_rank(case.old_gold_chunk_ids),
        "metrics": None,
        "top5": hits,
        "top1_score": hits[0]["score"] if hits else None,
        "top2_score": hits[1]["score"] if len(hits) > 1 else None,
        "top1_top2_margin": hits[0]["score"] - hits[1]["score"] if len(hits) > 1 else None,
        "conflict_metrics": None,
        "taxonomy": [],
    }
    if case.scored:
        row["metrics"] = {
            **{f"Recall@{k}": int(rank is not None and rank <= k) for k in (1, 3, 5)},
            "MRR@5": 1 / rank if rank else 0.0,
            "DocHit@1": int(
                bool(hits) and hits[0]["external_key"] in case.acceptable_document_keys
            ),
            **{
                f"PreferredHit@{k}": int(preferred is not None and preferred <= k)
                for k in (1, 3, 5)
            },
        }
        if rank is None:
            row["taxonomy"].append("retrieval failure")
        elif rank != 1 or preferred != 1:
            row["taxonomy"].append("ranking ambiguity")
        if case.query_context.target_role and not row["metrics"]["DocHit@1"]:
            row["taxonomy"].append("metadata issue")
    elif case.conflict_sources:
        row["conflict_metrics"] = {
            f"ConflictRecall@{k}": int(
                all(set(group.chunk_ids) & set(ids[:k]) for group in case.conflict_sources)
            )
            for k in (2, 3, 5)
        }
        row["conflict_group_ranks"] = [
            {
                "external_key": group.external_key,
                "rank": first_rank(group.chunk_ids),
                "claim": group.claim,
            }
            for group in case.conflict_sources
        ]
        row["taxonomy"].append("source conflict")
    else:
        row["taxonomy"].append("no-answer issue")
    return row


def aggregate(rows: list[dict]) -> dict:
    scored = [row for row in rows if row["metrics"] is not None]
    return {
        "count": len(scored),
        **{
            key: mean(row["metrics"][key] for row in scored) if scored else None
            for key in QUALITY_KEYS
        },
    }


def threshold_metrics(positives: list[float], negatives: list[float], threshold: float) -> dict:
    tp = sum(score >= threshold for score in positives)
    fp = sum(score >= threshold for score in negatives)
    fn, tn = len(positives) - tp, len(negatives) - fp
    return {
        "threshold": threshold,
        "TP": tp,
        "FP": fp,
        "TN": tn,
        "FN": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / len(positives) if positives else None,
        "false_accept_rate": fp / len(negatives) if negatives else None,
        "false_reject_rate": fn / len(positives) if positives else None,
    }


def answerability_report(rows: list[dict], score_type: str) -> dict:
    positives = [{"id": row["id"], "score": row["top1_score"]} for row in rows if row["scored"]]
    negatives = [
        {
            "id": row["id"],
            "score": row["top1_score"],
            "top2_score": row["top2_score"],
            "margin": row["top1_top2_margin"],
        }
        for row in rows
        if row["category"] == "no_answer"
    ]
    positive_scores = [row["score"] for row in positives]
    negative_scores = [row["score"] for row in negatives]
    values = sorted(set(positive_scores + negative_scores))
    # Every distinct accept/reject partition, including ties and accept/reject-all.
    thresholds = [
        math.nextafter(values[0], -math.inf),
        *values,
        math.nextafter(values[-1], math.inf),
    ]
    sweep = [
        threshold_metrics(positive_scores, negative_scores, threshold) for threshold in thresholds
    ]

    def distribution(scores):
        return {
            "count": len(scores),
            "min": min(scores),
            "max": max(scores),
            "mean": mean(scores),
            "median": median(scores),
        }

    return {
        "score_type": score_type,
        "policy": (
            "Diagnostic in-sample sweep only. Accept iff Top1 "
            "score >= threshold; C01 excluded. No deployed threshold."
        ),
        "positives": positives,
        "negatives": negatives,
        "positive_distribution": distribution(positive_scores),
        "negative_distribution": distribution(negative_scores),
        "strictly_separable": min(positive_scores) > max(negative_scores),
        "threshold_candidates": sweep,
        "max_recall_at_zero_false_accept": max(
            (item for item in sweep if item["FP"] == 0), key=lambda item: item["recall"]
        ),
        "min_false_accept_at_full_recall": min(
            (item for item in sweep if item["FN"] == 0), key=lambda item: item["false_accept_rate"]
        ),
    }

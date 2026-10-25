"""Versioned, model-specific score admission; fusion scores are never compared."""

import json
import math
from importlib.resources import files

from rag_portfolio.services.documents import DocumentServiceError


def policy(model):
    row = json.loads(files("rag_portfolio").joinpath("relevance_policy.json").read_text())
    if row["rerank_model"] != model:
        raise DocumentServiceError("relevance_policy_model_mismatch", 503)
    if any(not math.isfinite(v) or not 0 <= v <= 1 for v in row["thresholds"].values()):
        raise DocumentServiceError("relevance_policy_invalid", 503)
    return row


def admit(ranked, codes, rules):
    unknown = set(codes.values()) - rules["thresholds"].keys()
    if unknown:
        raise DocumentServiceError("relevance_policy_missing_for_knowledge_base", 503)
    return [item for item in ranked if item.score >= rules["thresholds"][codes[item.chunk_id]]]


def coverage_order(groups):
    """Give each accepted subquestion a first result before adding extra passages."""
    result, seen = [], set()
    for ordinal in range(max((len(group) for group in groups), default=0)):
        for group in groups:
            if ordinal < len(group) and group[ordinal].chunk_id not in seen:
                result.append(group[ordinal])
                seen.add(group[ordinal].chunk_id)
    return result

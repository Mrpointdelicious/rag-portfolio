"""Shared validation exception identity for runtime and existing evaluation callers."""


class EvaluationError(RuntimeError):
    """Invalid corpus/algorithm inputs; never substitute silent results."""

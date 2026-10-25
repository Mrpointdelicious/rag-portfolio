"""Public knowledge-search v1 contract, independent of retrieval implementation details."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Domain = Literal["health", "product", "help"]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class KnowledgeScope(RequestModel):
    space_id: str | None = Field(default=None, min_length=1, max_length=200)


class KnowledgeLimits(RequestModel):
    max_context_tokens: int = Field(default=4000, ge=128, le=16000)


class KnowledgeSearchRequest(RequestModel):
    query: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    domain: Domain = "health"
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    limits: KnowledgeLimits = Field(default_factory=KnowledgeLimits)


class KnowledgeSource(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=500)
    locator: str = Field(min_length=1, max_length=2000)
    ref: str = Field(min_length=1, max_length=1000)


class KnowledgeSearchResponse(BaseModel):
    # Consumers can tolerate additive metadata; status semantics are versioned.
    model_config = ConfigDict(
        json_schema_extra={
            "allOf": [
                {
                    "if": {"properties": {"status": {"const": "no_evidence"}}},
                    "then": {
                        "properties": {"context": {"maxLength": 0}, "sources": {"maxItems": 0}}
                    },
                },
                {
                    "if": {"properties": {"status": {"enum": ["ok", "partial"]}}},
                    "then": {
                        "properties": {"context": {"pattern": r"\S"}, "sources": {"minItems": 1}}
                    },
                },
                {
                    "if": {"properties": {"status": {"const": "partial"}}},
                    "then": {"properties": {"gaps": {"minItems": 1}}},
                },
            ],
        }
    )
    status: Literal["ok", "partial", "no_evidence"]
    context: str = Field(max_length=200000)
    sources: list[KnowledgeSource] = Field(max_length=20)
    gaps: list[str] = Field(max_length=20)
    trace_id: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def evidence_matches_status(self):
        if self.status == "no_evidence":
            if self.context or self.sources:
                raise ValueError("no_evidence must not contain evidence")
        elif not self.context.strip() or not self.sources:
            raise ValueError("usable evidence requires context and sources")
        if self.status == "partial" and not self.gaps:
            raise ValueError("partial requires an evidence gap")
        if len({source.id for source in self.sources}) != len(self.sources):
            raise ValueError("source IDs must be unique within a response")
        return self


class KnowledgeError(BaseModel):
    code: str
    retryable: bool


class KnowledgeErrorResponse(BaseModel):
    error: KnowledgeError
    request_id: str

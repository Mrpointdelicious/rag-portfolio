from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, str_strip_whitespace=True)


class KnowledgeBaseCreate(Contract):
    code: Literal["rehab_kb", "scene_kb", "clinical_kb", "device_kb", "hospital_kb", "sop_kb"]
    scope_key: str = Field(default="shared", min_length=1, max_length=100)
    hospital_id: str | None = Field(default=None, max_length=100)
    space_id: UUID | None = None

    @model_validator(mode="after")
    def scene_requires_space(self) -> "KnowledgeBaseCreate":
        if self.code == "scene_kb" and self.space_id is None:
            raise ValueError("scene_kb requires a registered space_id")
        return self


class KnowledgeBaseView(KnowledgeBaseCreate):
    id: UUID
    enabled: bool
    active_release_id: UUID | None
    row_version: int


class JobView(Contract):
    id: UUID
    kind: str
    status: str
    attempts: int
    last_error_code: str | None
    result_json: dict | None
    created_at: datetime
    finished_at: datetime | None


class QueryFilters(Contract):
    device_type: str | None = Field(default=None, max_length=50)
    disease: str | None = Field(default=None, max_length=100)


class KnowledgeQuery(Contract):
    q: str = Field(min_length=1, max_length=2000)
    filters: QueryFilters = Field(default_factory=QueryFilters)


class RetrieveRequest(Contract):
    kb_ids: list[UUID] = Field(min_length=1, max_length=6)
    queries: list[KnowledgeQuery] = Field(min_length=1, max_length=4)
    top_k: int = Field(default=8, ge=1, le=20)
    registry_snapshot_id: UUID | None = None


class Evidence(Contract):
    document_id: UUID
    version_id: UUID
    chunk_id: UUID
    release_id: UUID
    title: str
    section: str
    source_locator: str
    text: str
    score: float
    retrieved_at: datetime
    registry_snapshot_id: UUID | None = None
    upstream_catalog_version: int | None = None


class RetrieveResponse(Contract):
    evidence: list[Evidence]
    notes: list[str]

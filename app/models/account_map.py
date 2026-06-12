"""Version 2 account-map data contract.

This module is intentionally diagram-free for the first v2 slice. Diagrams can
be added later as a separate optional artifact without weakening the account-map
grounding contract.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class Confidence(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class Provenance(str, Enum):
    researched = "researched"
    retrieved = "retrieved"
    repaired = "repaired"
    user_edited = "user_edited"
    gap = "gap"
    imported_v1 = "imported_v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    id: str
    claim: str
    source_title: str
    source_url: HttpUrl
    source_date: Optional[str] = None
    confidence: Confidence
    url_verified: Optional[bool] = None


class Signal(StrictModel):
    id: str
    signal: str
    why_it_matters: str
    evidence_ids: list[str]
    confidence: Confidence

    @field_validator("evidence_ids")
    @classmethod
    def require_evidence_ids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("Signal.evidence_ids must contain at least one evidence id")
        return value


class ProductFit(StrictModel):
    slug: str
    product: str
    fit_reason: str
    capabilities_used: list[str] = Field(default_factory=list)
    confidence: Confidence
    capability_evidence_refs: list[str] = Field(default_factory=list)


class DeliveryExperience(StrictModel):
    title: str
    customer_type: str
    anonymous: bool = False
    products: list[str] = Field(default_factory=list)
    relevance: str
    outcome: str = ""
    source_url: str = ""
    retrieval_score: Optional[float] = None
    provenance: Provenance = Provenance.retrieved


class SectionMeta(StrictModel):
    provenance: Provenance = Provenance.researched
    model: Optional[str] = None
    generated_at: Optional[datetime] = None
    notes: Optional[str] = None


class UseCase(StrictModel):
    id: str
    rank: int = Field(ge=1)
    title: str
    account_trigger: str
    problem: str
    problem_narrative: str = ""
    solution_narrative: str = ""
    business_value: str
    business_value_narrative: str = ""
    conversation_starter: str = ""
    implementation_flow: list[str] = Field(default_factory=list)
    stakeholders: list[str] = Field(default_factory=list)
    discovery_questions: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    deployment_hypothesis: str = ""
    opswat_products: list[ProductFit]
    delivery_experience: list[DeliveryExperience] = Field(default_factory=list)
    evidence_ids: list[str]
    confidence: Confidence
    meta: SectionMeta = Field(default_factory=SectionMeta)

    @field_validator("opswat_products")
    @classmethod
    def require_products(cls, value: list[ProductFit]) -> list[ProductFit]:
        if not value:
            raise ValueError("UseCase.opswat_products must contain at least one product")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def require_evidence_ids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("UseCase.evidence_ids must contain at least one evidence id")
        return value


class BuyerPersona(StrictModel):
    persona: str
    likely_concerns: list[str] = Field(default_factory=list)
    message_angle: str


class Outreach(StrictModel):
    opening_angle: str
    email_subjects: list[str] = Field(default_factory=list)
    first_call_agenda: list[str] = Field(default_factory=list)


class AssumptionGap(StrictModel):
    item: str
    how_to_validate: str


class TargetAccount(StrictModel):
    name: str
    website: Optional[str] = None
    sector: str
    country: Optional[str] = None
    summary: str


class GenerationMeta(StrictModel):
    schema_version: int = 2
    provider: str
    model: str
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    generated_at: datetime
    target_input: str
    focus: str = ""
    requested_use_cases: int
    stage_timings_s: dict[str, float] = Field(default_factory=dict)
    retrieval: dict[str, Any] = Field(default_factory=dict)


class AccountMap(StrictModel):
    id: str
    target_account: TargetAccount
    research_evidence: list[Evidence]
    account_signals: list[Signal]
    recommended_use_cases: list[UseCase]
    buyer_map: list[BuyerPersona]
    outreach: Outreach
    assumptions_and_gaps: list[AssumptionGap] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    meta: GenerationMeta

    @model_validator(mode="after")
    def validate_references(self) -> "AccountMap":
        evidence_ids = {evidence.id for evidence in self.research_evidence}
        errors = []
        for signal in self.account_signals:
            missing = sorted(set(signal.evidence_ids) - evidence_ids)
            if missing:
                errors.append(f"{signal.id} references unknown evidence ids: {', '.join(missing)}")
        for use_case in self.recommended_use_cases:
            missing = sorted(set(use_case.evidence_ids) - evidence_ids)
            if missing:
                errors.append(f"{use_case.id} references unknown evidence ids: {', '.join(missing)}")
        if errors:
            raise ValueError("; ".join(errors))
        return self

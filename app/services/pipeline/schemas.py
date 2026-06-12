"""Structured stage outputs for the v2 generation pipeline."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

from app.models.account_map import AssumptionGap, BuyerPersona, Confidence, Outreach, TargetAccount


class StageModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


def first_present(data: dict, *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


class EvidenceCandidate(StageModel):
    claim: str
    source_title: str
    source_url: HttpUrl
    source_date: Optional[str] = None
    confidence: Confidence


class ResearchOutput(StageModel):
    target_account: TargetAccount
    research_evidence: list[EvidenceCandidate] = Field(min_length=1)


class SignalCandidate(StageModel):
    signal: str
    why_it_matters: str
    evidence_ids: list[str]
    confidence: Confidence


class SignalsOutput(StageModel):
    account_signals: list[SignalCandidate] = Field(default_factory=list)


class UseCaseSkeleton(StageModel):
    id: str = ""
    rank: int = Field(ge=1)
    title: str
    account_trigger: str
    problem: str
    signal_ids: list[str]
    product_slugs: list[str]
    confidence: Confidence

    @model_validator(mode="before")
    @classmethod
    def normalize_llm_field_variants(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("title"):
            data["title"] = first_present(data, "use_case", "name", "headline")
        if not data.get("account_trigger"):
            data["account_trigger"] = first_present(data, "trigger", "account_signal", "why_now")
        if not data.get("problem"):
            data["problem"] = first_present(data, "problem_statement", "pain", "challenge")
        return data


class SelectOutput(StageModel):
    use_cases: list[UseCaseSkeleton] = Field(default_factory=list)


class NarrativeProductFit(StageModel):
    slug: str
    fit_reason: str = ""
    capabilities_used: list[str] = Field(default_factory=list)
    confidence: Confidence = Confidence.medium


class NarrativeOutput(StageModel):
    problem_narrative: str = ""
    solution_narrative: str = ""
    business_value: str = ""
    business_value_narrative: str = ""
    conversation_starter: str = ""
    implementation_flow: list[str] = Field(default_factory=list)
    stakeholders: list[str] = Field(default_factory=list)
    discovery_questions: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    deployment_hypothesis: str = ""
    opswat_products: list[NarrativeProductFit] = Field(default_factory=list)


class NarrativeRepairOutput(StageModel):
    problem_narrative: str = ""
    solution_narrative: str = ""
    business_value: str = ""
    business_value_narrative: str = ""
    conversation_starter: str = ""
    implementation_flow: list[str] = Field(default_factory=list)
    stakeholders: list[str] = Field(default_factory=list)
    discovery_questions: list[str] = Field(default_factory=list)
    deployment_hypothesis: str = ""


class AssembleOutput(StageModel):
    buyer_map: list[BuyerPersona] = Field(default_factory=list)
    outreach: Outreach
    assumptions_and_gaps: list[AssumptionGap] = Field(default_factory=list)

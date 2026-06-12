"""Legacy v1 import helpers.

The converter accepts real v1 account-map JSON and produces a v2 AccountMap.
Dry-run prompt previews are intentionally rejected because they are not account
maps and should not appear as valid production records.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.models.account_map import (
    AccountMap,
    AssumptionGap,
    BuyerPersona,
    Confidence,
    DeliveryExperience,
    Evidence,
    GenerationMeta,
    Outreach,
    ProductFit,
    Provenance,
    SectionMeta,
    Signal,
    TargetAccount,
    UseCase,
)


class LegacyConversionError(ValueError):
    """Raised when a v1 payload is not a convertible account map."""


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "account"


def parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def map_id_from_legacy(data: dict[str, Any], map_id: Optional[str]) -> str:
    if map_id:
        return slugify(map_id)
    meta = dict_value(data.get("_meta"))
    target = dict_value(data.get("target_account"))
    target_input = str(meta.get("target_input") or target.get("name") or data.get("target") or "account")
    generated_at = parse_datetime(meta.get("generated_at"))
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return f"{slugify(target_input)}-{stamp}"


def dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def list_value(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return [value]


def string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in list_value(value) if str(item).strip()]


def confidence(value: Any) -> Confidence:
    normalized = str(value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return Confidence(normalized)
    return Confidence.medium


def is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def build_evidence(data: dict[str, Any]) -> tuple[list[Evidence], dict[str, str]]:
    evidence_items: list[Evidence] = []
    url_to_id: dict[str, str] = {}
    seen_urls: set[str] = set()
    for raw in list_value(data.get("research_evidence")):
        item = dict_value(raw)
        source_url = item.get("source_url")
        claim = str(item.get("claim") or "").strip()
        if not is_http_url(source_url) or not claim or source_url in seen_urls:
            continue
        evidence_id = f"ev-{len(evidence_items) + 1:03d}"
        evidence_items.append(
            Evidence(
                id=evidence_id,
                claim=claim,
                source_title=str(item.get("source_title") or source_url),
                source_url=source_url,
                source_date=item.get("source_date") if item.get("source_date") else None,
                confidence=confidence(item.get("confidence")),
            )
        )
        seen_urls.add(source_url)
        url_to_id[source_url] = evidence_id
    if not evidence_items:
        raise LegacyConversionError("v1 payload has no convertible research_evidence URLs")
    return evidence_items, url_to_id


def refs_to_evidence_ids(refs: Any, url_to_id: dict[str, str]) -> list[str]:
    ids: list[str] = []
    for ref in string_list(refs):
        evidence_id = url_to_id.get(ref)
        if evidence_id and evidence_id not in ids:
            ids.append(evidence_id)
    return ids


def build_signals(data: dict[str, Any], url_to_id: dict[str, str]) -> list[Signal]:
    signals: list[Signal] = []
    for raw in list_value(data.get("account_signals")):
        item = dict_value(raw)
        signal = str(item.get("signal") or "").strip()
        if not signal:
            continue
        evidence_ids = refs_to_evidence_ids(item.get("evidence_refs"), url_to_id)
        if not evidence_ids:
            continue
        signals.append(
            Signal(
                id=f"sig-{len(signals) + 1:03d}",
                signal=signal,
                why_it_matters=str(item.get("why_it_matters") or ""),
                evidence_ids=evidence_ids,
                confidence=confidence(item.get("confidence")),
            )
        )
    return signals


def build_products(raw_products: Any) -> list[ProductFit]:
    products: list[ProductFit] = []
    for raw in list_value(raw_products):
        item = dict_value(raw)
        slug = str(item.get("slug") or "").strip()
        product = str(item.get("product") or slug).strip()
        if not slug or not product:
            continue
        products.append(
            ProductFit(
                slug=slug,
                product=product,
                fit_reason=str(item.get("fit_reason") or item.get("product_fit") or item.get("reason") or ""),
                capabilities_used=string_list(
                    item.get("capabilities_used") or item.get("matched_capabilities") or item.get("capabilities")
                ),
                confidence=confidence(item.get("confidence")),
                capability_evidence_refs=string_list(
                    item.get("capability_evidence_refs")
                    or item.get("matched_evidence_refs")
                    or item.get("evidence_refs")
                ),
            )
        )
    return products


def build_delivery_experience(raw_delivery: Any) -> list[DeliveryExperience]:
    delivery: list[DeliveryExperience] = []
    for raw in list_value(raw_delivery):
        item = dict_value(raw)
        title = str(item.get("title") or "").strip()
        relevance = str(item.get("relevance") or "").strip()
        if not title or not relevance:
            continue
        delivery.append(
            DeliveryExperience(
                title=title,
                customer_type=str(item.get("customer_type") or "Similar customer environment"),
                anonymous=bool(item.get("anonymous", False)),
                products=string_list(item.get("products")),
                relevance=relevance,
                outcome=str(item.get("outcome") or ""),
                source_url=str(item.get("source_url") or ""),
                retrieval_score=float(item["score"]) if item.get("score") is not None else None,
                provenance=Provenance.retrieved,
            )
        )
    return delivery


def gap_if_missing(gaps: list[str], use_case_id: str, item: dict[str, Any], field_name: str) -> None:
    value = item.get(field_name)
    if value is None or value == "" or value == []:
        gaps.append(f"{use_case_id}.{field_name}")


def build_use_cases(data: dict[str, Any], url_to_id: dict[str, str], imported_meta: SectionMeta) -> tuple[list[UseCase], list[str]]:
    gaps: list[str] = []
    use_cases: list[UseCase] = []
    for index, raw in enumerate(list_value(data.get("recommended_use_cases")), start=1):
        item = dict_value(raw)
        use_case_id = f"uc-{index}"
        title = str(item.get("title") or item.get("use_case") or "").strip()
        products = build_products(item.get("opswat_products"))
        evidence_ids = refs_to_evidence_ids(item.get("evidence_refs"), url_to_id)
        if not title or not products:
            gaps.append(f"{use_case_id}.import_skipped")
            continue
        if not evidence_ids:
            gaps.append(f"{use_case_id}.evidence_ids")
            continue

        for field_name in [
            "problem_narrative",
            "solution_narrative",
            "business_value_narrative",
            "conversation_starter",
            "implementation_flow",
            "stakeholders",
            "discovery_questions",
            "deployment_hypothesis",
        ]:
            gap_if_missing(gaps, use_case_id, item, field_name)

        use_cases.append(
            UseCase(
                id=use_case_id,
                rank=int(item.get("rank") or index),
                title=title,
                account_trigger=str(item.get("account_trigger") or item.get("signal_link") or ""),
                problem=str(item.get("problem") or item.get("signal_link") or ""),
                problem_narrative=str(item.get("problem_narrative") or ""),
                solution_narrative=str(item.get("solution_narrative") or ""),
                business_value=str(item.get("business_value") or item.get("product_fit") or ""),
                business_value_narrative=str(item.get("business_value_narrative") or ""),
                conversation_starter=str(item.get("conversation_starter") or ""),
                implementation_flow=string_list(item.get("implementation_flow")),
                stakeholders=string_list(item.get("stakeholders")),
                discovery_questions=string_list(item.get("discovery_questions")),
                inferences=string_list(item.get("inferences")),
                deployment_hypothesis=str(item.get("deployment_hypothesis") or ""),
                opswat_products=products,
                delivery_experience=build_delivery_experience(item.get("delivery_experience")),
                evidence_ids=evidence_ids,
                confidence=confidence(item.get("confidence")),
                meta=imported_meta,
            )
        )
    if not use_cases:
        raise LegacyConversionError("v1 payload has no convertible recommended_use_cases")
    return use_cases, gaps


def build_buyer_map(data: dict[str, Any]) -> list[BuyerPersona]:
    buyer_map: list[BuyerPersona] = []
    for raw in list_value(data.get("buyer_map")):
        item = dict_value(raw)
        persona = str(item.get("persona") or item.get("role") or "").strip()
        if not persona:
            continue
        buyer_map.append(
            BuyerPersona(
                persona=persona,
                likely_concerns=string_list(item.get("likely_concerns") or item.get("talking_points")),
                message_angle=str(item.get("message_angle") or item.get("why_relevant") or ""),
            )
        )
    return buyer_map


def build_outreach(data: dict[str, Any], gaps: list[str]) -> Outreach:
    outreach = dict_value(data.get("outreach"))
    if not outreach.get("opening_angle"):
        gaps.append("outreach.opening_angle")
    return Outreach(
        opening_angle=str(outreach.get("opening_angle") or ""),
        email_subjects=string_list(outreach.get("email_subjects")),
        first_call_agenda=string_list(outreach.get("first_call_agenda") or outreach.get("discovery_questions")),
    )


def build_assumption_gaps(data: dict[str, Any]) -> list[AssumptionGap]:
    assumptions: list[AssumptionGap] = []
    for raw in list_value(data.get("assumptions_and_gaps")):
        item = dict_value(raw)
        if item:
            assumptions.append(
                AssumptionGap(
                    item=str(item.get("item") or ""),
                    how_to_validate=str(item.get("how_to_validate") or ""),
                )
            )
        elif isinstance(raw, str) and raw.strip():
            assumptions.append(AssumptionGap(item=raw.strip(), how_to_validate=""))
    return assumptions


def from_v1(data: dict[str, Any], map_id: Optional[str] = None) -> AccountMap:
    if data.get("dry_run") is True or "prompt_preview" in data:
        raise LegacyConversionError("dry-run prompt preview is not a convertible account map")
    target = dict_value(data.get("target_account"))
    if not target:
        raise LegacyConversionError("v1 payload missing target_account")

    meta = dict_value(data.get("_meta"))
    generated_at = parse_datetime(meta.get("generated_at"))
    imported_meta = SectionMeta(
        provenance=Provenance.imported_v1,
        model=str(meta.get("model") or data.get("model") or "unknown"),
        generated_at=generated_at,
        notes="Imported from v1 account-map JSON.",
    )
    evidence, url_to_id = build_evidence(data)
    use_cases, gaps = build_use_cases(data, url_to_id, imported_meta)
    outreach = build_outreach(data, gaps)

    account_map = AccountMap(
        id=map_id_from_legacy(data, map_id),
        target_account=TargetAccount(
            name=str(target.get("name") or meta.get("target_input") or "Unknown account"),
            website=str(target.get("website")) if target.get("website") else None,
            sector=str(target.get("sector") or "Unknown"),
            country=str(target.get("country")) if target.get("country") else None,
            summary=str(target.get("summary") or ""),
        ),
        research_evidence=evidence,
        account_signals=build_signals(data, url_to_id),
        recommended_use_cases=use_cases,
        buyer_map=build_buyer_map(data),
        outreach=outreach,
        assumptions_and_gaps=build_assumption_gaps(data),
        gaps=sorted(set(gaps)),
        meta=GenerationMeta(
            provider=str(meta.get("provider") or data.get("provider") or "unknown"),
            model=str(meta.get("model") or data.get("model") or "unknown"),
            prompt_versions={},
            generated_at=generated_at,
            target_input=str(meta.get("target_input") or target.get("name") or data.get("target") or ""),
            focus=str(meta.get("focus") or data.get("focus") or ""),
            requested_use_cases=len(use_cases),
            stage_timings_s={},
            retrieval=dict_value(meta.get("customer_story_context")),
        ),
    )
    return account_map


def from_v1_file(path: Path) -> AccountMap:
    import json

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LegacyConversionError(f"{path} does not contain a JSON object")
    return from_v1(data, map_id=path.stem)

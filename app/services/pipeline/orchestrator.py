"""No-diagram v2 generation pipeline orchestration."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from app.models.account_map import (
    AccountMap,
    AssumptionGap,
    Confidence,
    Evidence,
    GenerationMeta,
    ProductFit,
    Provenance,
    SectionMeta,
    Signal,
    UseCase,
)
from app.models.legacy import slugify
from app.services.capability_map import CapabilityMap
from app.services.grounding import ground_account_map
from app.services.pipeline.schemas import (
    AssembleOutput,
    NarrativeOutput,
    NarrativeRepairOutput,
    ResearchOutput,
    SelectOutput,
    SignalsOutput,
    UseCaseSkeleton,
)
from app.services.prompts import PromptTemplate, load_prompt
from app.services.providers import LLMProvider, RawCall
from app.services.retrieval import (
    CustomerStoryRetriever,
    NullCustomerStoryRetriever,
    RetrievalResult,
    compact_customer_story_context,
    customer_story_query,
    delivery_experience_from_examples,
)


ProgressCallback = Callable[[str, float, str], None]
REPAIRABLE_NARRATIVE_FIELDS = [
    "problem_narrative",
    "solution_narrative",
    "business_value",
    "business_value_narrative",
    "conversation_starter",
    "implementation_flow",
    "stakeholders",
    "discovery_questions",
    "deployment_hypothesis",
]


@dataclass
class PipelineRequest:
    target: str
    focus: str = ""
    use_cases: int = 5
    provider: str = "anthropic"
    model: str = "claude-opus-4-8"
    fast_model: Optional[str] = None
    max_tokens: int = 30000
    narrative_concurrency: int = 3
    artifact_dir: Optional[Path] = None


@dataclass
class PipelineResult:
    account_map: AccountMap
    raw_calls: dict[str, RawCall]
    warnings: tuple[str, ...] = ()


@dataclass
class PipelineState:
    request: PipelineRequest
    llm: LLMProvider
    capability_map: CapabilityMap
    customer_story_retriever: CustomerStoryRetriever
    prompt_versions: dict[str, str] = field(default_factory=dict)
    raw_calls: dict[str, RawCall] = field(default_factory=dict)
    retrieval_meta: dict[str, object] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    gaps: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def model_for(self, stage: str) -> str:
        if stage in {"signals"} and self.request.fast_model:
            return self.request.fast_model
        return self.request.model


def default_progress(stage: str, pct: float, message: str) -> None:
    del stage, pct, message


async def run_pipeline(
    request: PipelineRequest,
    llm: LLMProvider,
    capability_map: CapabilityMap,
    progress: Optional[ProgressCallback] = None,
    customer_story_retriever: Optional[CustomerStoryRetriever] = None,
) -> PipelineResult:
    progress = progress or default_progress
    state = PipelineState(
        request=request,
        llm=llm,
        capability_map=capability_map,
        customer_story_retriever=customer_story_retriever or NullCustomerStoryRetriever(),
    )

    progress("research", 0.05, "Researching target account")
    research = await timed_stage(state, "research", run_research)
    progress("signals", 0.2, "Extracting account signals")
    signals = await timed_stage(state, "signals", lambda s: run_signals(s, research))
    progress("select", 0.35, "Selecting OPSWAT plays")
    skeletons = await timed_stage(state, "select", lambda s: run_select(s, research, signals))
    progress("narrative", 0.55, "Writing use-case narratives")
    use_cases = await timed_stage(state, "narrative", lambda s: run_narratives(s, research, signals, skeletons))
    progress("assemble", 0.85, "Assembling buyer map and outreach")
    assemble = await timed_stage(state, "assemble", lambda s: run_assemble(s, research, signals, use_cases))

    account_map = build_account_map(state, research, signals, use_cases, assemble)
    grounding_report = ground_account_map(account_map, capability_map)
    state.warnings.extend(grounding_report.warnings)
    account_map = grounding_report.account_map.model_copy(
        update={
            "meta": grounding_report.account_map.meta.model_copy(update={"stage_timings_s": state.timings}),
        }
    )
    account_map = AccountMap.model_validate(account_map.model_dump())
    if request.artifact_dir:
        write_artifacts(request.artifact_dir, account_map, state.raw_calls)
    progress("complete", 1.0, "Account map complete")
    return PipelineResult(account_map=account_map, raw_calls=state.raw_calls, warnings=tuple(state.warnings))


async def timed_stage(state: PipelineState, name: str, fn: Callable[[PipelineState], object]) -> object:
    started = time.monotonic()
    result = fn(state)
    if asyncio.iscoroutine(result):
        result = await result
    state.timings[name] = round(time.monotonic() - started, 4)
    return result


async def structured_stage(
    state: PipelineState,
    *,
    stage: str,
    prompt: PromptTemplate,
    system: str,
    schema: type,
    values: dict[str, object],
    web_search: bool = False,
) -> object:
    rendered = prompt.render(values)
    state.prompt_versions[stage] = prompt.short_hash
    parsed, raw_call = await state.llm.structured(
        system=system,
        prompt=rendered,
        schema=schema,
        model=state.model_for(stage),
        max_tokens=state.request.max_tokens,
        web_search=web_search,
        stage=stage,
    )
    state.raw_calls[stage] = raw_call
    return parsed


async def retrieve_customer_stories(
    state: PipelineState,
    *,
    stage: str,
    query: str,
    top_k: int,
) -> RetrievalResult:
    result = await state.customer_story_retriever.search(query=query, top_k=top_k)
    state.retrieval_meta[stage] = result.summary()
    if result.configured and result.error:
        state.warnings.append(f"{stage}: customer-story retrieval unavailable: {result.error}")
    return result


async def run_research(state: PipelineState) -> tuple[ResearchOutput, list[Evidence]]:
    system = load_prompt("system_base")
    prompt = load_prompt("stage_research")
    parsed = await structured_stage(
        state,
        stage="research",
        prompt=prompt,
        system=system.text,
        schema=ResearchOutput,
        values={"target": state.request.target, "focus": state.request.focus or "No special focus."},
        web_search=True,
    )
    research = parsed if isinstance(parsed, ResearchOutput) else ResearchOutput.model_validate(parsed)
    evidence = [
        Evidence(
            id=f"ev-{index:03d}",
            claim=item.claim,
            source_title=item.source_title,
            source_url=item.source_url,
            source_date=item.source_date,
            confidence=item.confidence,
        )
        for index, item in enumerate(research.research_evidence, start=1)
    ]
    return research, evidence


async def run_signals(state: PipelineState, research: tuple[ResearchOutput, list[Evidence]]) -> list[Signal]:
    system = load_prompt("system_base")
    prompt = load_prompt("stage_signals")
    _, evidence = research
    parsed = await structured_stage(
        state,
        stage="signals",
        prompt=prompt,
        system=system.text,
        schema=SignalsOutput,
        values={"evidence_json": json.dumps([item.model_dump(mode="json") for item in evidence], indent=2)},
    )
    output = parsed if isinstance(parsed, SignalsOutput) else SignalsOutput.model_validate(parsed)
    valid_evidence_ids = {item.id for item in evidence}
    signals: list[Signal] = []
    for index, item in enumerate(output.account_signals, start=1):
        evidence_ids = [evidence_id for evidence_id in item.evidence_ids if evidence_id in valid_evidence_ids]
        if not evidence_ids:
            state.gaps.add(f"sig-{index:03d}.evidence_ids")
            continue
        signals.append(
            Signal(
                id=f"sig-{len(signals) + 1:03d}",
                signal=item.signal,
                why_it_matters=item.why_it_matters,
                evidence_ids=evidence_ids,
                confidence=item.confidence,
            )
        )
    return signals


async def run_select(
    state: PipelineState,
    research: tuple[ResearchOutput, list[Evidence]],
    signals: list[Signal],
) -> list[UseCaseSkeleton]:
    system = load_prompt("system_base")
    prompt = load_prompt("stage_select")
    story_result = await retrieve_customer_stories(
        state,
        stage="select",
        query=customer_story_query(target=state.request.target, focus=state.request.focus),
        top_k=6,
    )
    parsed = await structured_stage(
        state,
        stage="select",
        prompt=prompt,
        system=system.text,
        schema=SelectOutput,
        values={
            "target_json": json.dumps(research[0].target_account.model_dump(mode="json"), indent=2),
            "signals_json": json.dumps([item.model_dump(mode="json") for item in signals], indent=2),
            "capability_map_json": json.dumps(state.capability_map.compact(), indent=2),
            "customer_story_examples_json": json.dumps(
                compact_customer_story_context(story_result.examples, limit=6),
                indent=2,
            ),
            "use_cases": state.request.use_cases,
        },
    )
    output = parsed if isinstance(parsed, SelectOutput) else SelectOutput.model_validate(parsed)
    valid_signal_ids = {signal.id for signal in signals}
    valid_slugs = state.capability_map.valid_slugs()
    skeletons: list[UseCaseSkeleton] = []
    for index, skeleton in enumerate(output.use_cases[: state.request.use_cases], start=1):
        product_slugs = [slug for slug in skeleton.product_slugs if slug in valid_slugs]
        invalid_slugs = sorted(set(skeleton.product_slugs) - valid_slugs)
        for slug in invalid_slugs:
            state.gaps.add(f"{skeleton.id or f'uc-{index}'}.product_slugs.{slug}")
        signal_ids = [signal_id for signal_id in skeleton.signal_ids if signal_id in valid_signal_ids]
        use_case_id = skeleton.id or f"uc-{index}"
        title = skeleton.title.strip()
        if not title:
            state.gaps.add(f"{use_case_id}.title")
            title = skeleton.problem.strip() or skeleton.account_trigger.strip() or f"Use case {index}"
        if not product_slugs:
            state.gaps.add(f"{use_case_id}.product_slugs")
            continue
        if not signal_ids:
            state.gaps.add(f"{use_case_id}.signal_ids")
            continue
        skeletons.append(
            skeleton.model_copy(
                update={
                    "id": use_case_id,
                    "rank": len(skeletons) + 1,
                    "title": title,
                    "signal_ids": signal_ids,
                    "product_slugs": product_slugs,
                }
            )
        )
    return skeletons


async def run_narratives(
    state: PipelineState,
    research: tuple[ResearchOutput, list[Evidence]],
    signals: list[Signal],
    skeletons: list[UseCaseSkeleton],
) -> list[UseCase]:
    semaphore = asyncio.Semaphore(state.request.narrative_concurrency)

    async def one(skeleton: UseCaseSkeleton) -> Optional[UseCase]:
        async with semaphore:
            return await run_narrative(state, research, signals, skeleton)

    results = await asyncio.gather(*(one(skeleton) for skeleton in skeletons))
    return [item for item in results if item is not None]


async def run_narrative(
    state: PipelineState,
    research: tuple[ResearchOutput, list[Evidence]],
    signals: list[Signal],
    skeleton: UseCaseSkeleton,
) -> Optional[UseCase]:
    system = load_prompt("system_base")
    prompt = load_prompt("stage_narrative")
    signal_by_id = {signal.id: signal for signal in signals}
    selected_signals = [signal_by_id[signal_id] for signal_id in skeleton.signal_ids if signal_id in signal_by_id]
    evidence_ids = sorted({evidence_id for signal in selected_signals for evidence_id in signal.evidence_ids})
    if not evidence_ids:
        state.gaps.add(f"{skeleton.id}.evidence_ids")
        return None

    product_entries = [state.capability_map.require(slug).raw for slug in skeleton.product_slugs]
    story_result = await retrieve_customer_stories(
        state,
        stage=f"narrative_{skeleton.id}",
        query=customer_story_query(
            target=state.request.target,
            focus=state.request.focus,
            use_case=skeleton,
            product_names=[str(item.get("product") or "") for item in product_entries],
        ),
        top_k=3,
    )
    parsed = await structured_stage(
        state,
        stage=f"narrative_{skeleton.id}",
        prompt=prompt,
        system=system.text,
        schema=NarrativeOutput,
        values={
            "target_json": json.dumps(research[0].target_account.model_dump(mode="json"), indent=2),
            "skeleton_json": json.dumps(skeleton.model_dump(mode="json"), indent=2),
            "signals_json": json.dumps([item.model_dump(mode="json") for item in selected_signals], indent=2),
            "products_json": json.dumps(product_entries, indent=2),
            "customer_story_examples_json": json.dumps(
                compact_customer_story_context(story_result.examples, limit=3),
                indent=2,
            ),
        },
    )
    narrative = parsed if isinstance(parsed, NarrativeOutput) else NarrativeOutput.model_validate(parsed)
    initial_missing_fields = missing_narrative_fields(narrative)
    repaired_fields: list[str] = []
    if initial_missing_fields:
        narrative, repaired_fields = await repair_narrative(
            state,
            research,
            selected_signals,
            skeleton,
            narrative,
            initial_missing_fields,
        )
    product_fits_by_slug = {item.slug: item for item in narrative.opswat_products}
    product_fits: list[ProductFit] = []
    for slug in skeleton.product_slugs:
        capability_product = state.capability_map.require(slug)
        fit = product_fits_by_slug.get(slug)
        if fit is None:
            state.gaps.add(f"{skeleton.id}.opswat_products.{slug}.fit_reason")
            product_fits.append(
                ProductFit(
                    slug=slug,
                    product=capability_product.product,
                    fit_reason="",
                    capabilities_used=[],
                    confidence=Confidence.medium,
                    capability_evidence_refs=[],
                )
            )
            continue
        product_fits.append(
            ProductFit(
                slug=slug,
                product=capability_product.product,
                fit_reason=fit.fit_reason,
                capabilities_used=fit.capabilities_used,
                confidence=fit.confidence,
                capability_evidence_refs=[],
            )
        )

    for field_name in missing_narrative_fields(narrative):
        state.gaps.add(f"{skeleton.id}.{field_name}")

    provenance = Provenance.repaired if repaired_fields else Provenance.researched
    notes = f"Repaired fields: {', '.join(repaired_fields)}" if repaired_fields else None

    return UseCase(
        id=skeleton.id,
        rank=skeleton.rank,
        title=skeleton.title,
        account_trigger=skeleton.account_trigger,
        problem=skeleton.problem,
        problem_narrative=narrative.problem_narrative,
        solution_narrative=narrative.solution_narrative,
        business_value=narrative.business_value,
        business_value_narrative=narrative.business_value_narrative,
        conversation_starter=narrative.conversation_starter,
        implementation_flow=narrative.implementation_flow,
        stakeholders=narrative.stakeholders,
        discovery_questions=narrative.discovery_questions,
        inferences=narrative.inferences,
        deployment_hypothesis=narrative.deployment_hypothesis,
        opswat_products=product_fits,
        delivery_experience=delivery_experience_from_examples(story_result.examples, limit=3),
        evidence_ids=evidence_ids,
        confidence=skeleton.confidence,
        meta=SectionMeta(
            provenance=provenance,
            model=state.model_for(f"narrative_{skeleton.id}"),
            generated_at=datetime.now(timezone.utc),
            notes=notes,
        ),
    )


def missing_narrative_fields(narrative: NarrativeOutput) -> list[str]:
    missing: list[str] = []
    for field_name in REPAIRABLE_NARRATIVE_FIELDS:
        value = getattr(narrative, field_name)
        if value == "" or value == []:
            missing.append(field_name)
    return missing


async def repair_narrative(
    state: PipelineState,
    research: tuple[ResearchOutput, list[Evidence]],
    selected_signals: list[Signal],
    skeleton: UseCaseSkeleton,
    narrative: NarrativeOutput,
    missing_fields: list[str],
) -> tuple[NarrativeOutput, list[str]]:
    system = load_prompt("system_base")
    prompt = load_prompt("stage_repair_narrative")
    parsed = await structured_stage(
        state,
        stage=f"repair_narrative_{skeleton.id}",
        prompt=prompt,
        system=system.text,
        schema=NarrativeRepairOutput,
        values={
            "target_json": json.dumps(research[0].target_account.model_dump(mode="json"), indent=2),
            "skeleton_json": json.dumps(skeleton.model_dump(mode="json"), indent=2),
            "signals_json": json.dumps([item.model_dump(mode="json") for item in selected_signals], indent=2),
            "draft_json": json.dumps(narrative.model_dump(mode="json"), indent=2),
            "missing_fields_json": json.dumps(missing_fields, indent=2),
        },
    )
    repair = parsed if isinstance(parsed, NarrativeRepairOutput) else NarrativeRepairOutput.model_validate(parsed)
    updates: dict[str, object] = {}
    repaired_fields: list[str] = []
    for field_name in missing_fields:
        value = getattr(repair, field_name)
        if value == "" or value == []:
            continue
        updates[field_name] = value
        repaired_fields.append(field_name)
    if not updates:
        return narrative, []
    return narrative.model_copy(update=updates), repaired_fields


async def run_assemble(
    state: PipelineState,
    research: tuple[ResearchOutput, list[Evidence]],
    signals: list[Signal],
    use_cases: list[UseCase],
) -> AssembleOutput:
    system = load_prompt("system_base")
    prompt = load_prompt("stage_assemble")
    parsed = await structured_stage(
        state,
        stage="assemble",
        prompt=prompt,
        system=system.text,
        schema=AssembleOutput,
        values={
            "target_json": json.dumps(research[0].target_account.model_dump(mode="json"), indent=2),
            "signals_json": json.dumps([item.model_dump(mode="json") for item in signals], indent=2),
            "use_cases_json": json.dumps(
                [
                    {
                        "id": item.id,
                        "title": item.title,
                        "business_value": item.business_value,
                        "business_value_narrative": item.business_value_narrative,
                    }
                    for item in use_cases
                ],
                indent=2,
            ),
        },
    )
    output = parsed if isinstance(parsed, AssembleOutput) else AssembleOutput.model_validate(parsed)
    if not output.buyer_map:
        state.gaps.add("buyer_map")
    if not output.outreach.opening_angle:
        state.gaps.add("outreach.opening_angle")
    return output


def build_account_map(
    state: PipelineState,
    research: tuple[ResearchOutput, list[Evidence]],
    signals: list[Signal],
    use_cases: list[UseCase],
    assemble: AssembleOutput,
) -> AccountMap:
    research_output, evidence = research
    generated_at = datetime.now(timezone.utc)
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    account_id = f"{slugify(research_output.target_account.name or state.request.target)}-{stamp}"
    return AccountMap(
        id=account_id,
        target_account=research_output.target_account,
        research_evidence=evidence,
        account_signals=signals,
        recommended_use_cases=use_cases,
        buyer_map=assemble.buyer_map,
        outreach=assemble.outreach,
        assumptions_and_gaps=assemble.assumptions_and_gaps
        + [AssumptionGap(item=gap, how_to_validate="Regenerate or edit this section.") for gap in sorted(state.gaps)],
        gaps=sorted(state.gaps),
        meta=GenerationMeta(
            provider=state.llm.name,
            model=state.request.model,
            prompt_versions=state.prompt_versions,
            generated_at=generated_at,
            target_input=state.request.target,
            focus=state.request.focus,
            requested_use_cases=state.request.use_cases,
            stage_timings_s=state.timings,
            retrieval=state.retrieval_meta,
        ),
    )


def write_artifacts(artifact_dir: Path, account_map: AccountMap, raw_calls: dict[str, RawCall]) -> None:
    target_dir = artifact_dir / account_map.id
    runs_dir = target_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "account_map.json").write_text(account_map.model_dump_json(indent=2) + "\n", encoding="utf-8")
    for stage, raw_call in raw_calls.items():
        safe_stage = slugify(stage)
        (runs_dir / f"{safe_stage}.json").write_text(
            json.dumps(
                {
                    "provider": raw_call.provider,
                    "model": raw_call.model,
                    "request_hash": raw_call.request_hash,
                    "response_text": raw_call.response_text,
                    "latency_s": raw_call.latency_s,
                    "tokens_in": raw_call.tokens_in,
                    "tokens_out": raw_call.tokens_out,
                    "ok": raw_call.ok,
                    "error": raw_call.error,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

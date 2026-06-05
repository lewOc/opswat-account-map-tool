#!/usr/bin/env python3
"""Generate a sourced OPSWAT account map for a target company.

The script is intentionally a CLI first. It gives us a fast feedback loop before
we wire the workflow into the existing React UI.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


PROJECT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT / "data"
OUTPUTS_DIR = PROJECT / "outputs"
DEFAULT_CAPABILITY_MAP = DATA_DIR / "capability_map.json"
DEFAULT_PROVIDER = "anthropic"
DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OPENAI_MODEL = "gpt-5.5"
DEFAULT_OPENAI_REASONING = "medium"
DEFAULT_MODEL = DEFAULT_ANTHROPIC_MODEL
DEFAULT_MODEL_TIMEOUT_SECONDS = int(os.environ.get("MODEL_REQUEST_TIMEOUT_SECONDS", "85"))
logger = logging.getLogger("opswat_account_map")


SYSTEM_PROMPT = """You are an OPSWAT account-mapping analyst for enterprise and critical-infrastructure sales teams.

Your job is to research a target account and create a practical, source-grounded sales account map.

Rules:
- Use web research for company-specific facts. Prefer the company's own website, annual reports, investor/regulatory pages, official newsrooms, and credible public sources.
- Use the supplied OPSWAT capability map as the allowed product universe. Do not recommend OPSWAT products that are not in that map.
- Every recommended use case must connect a company signal to a specific OPSWAT product fit.
- Separate facts from inferences. If something is likely but not directly evidenced, mark it as an inference.
- Do not invent customer infrastructure, incidents, vendors, regulations, facilities, or budgets.
- Use concise, consultative sales language. The output is for an account manager preparing a real outreach or discovery call.
- Return valid JSON only. No Markdown fences, no commentary outside JSON.
"""


JSON_SHAPE = {
    "target_account": {
        "name": "string",
        "website": "string or null",
        "sector": "string",
        "summary": "2-4 sentence account summary",
    },
    "research_evidence": [
        {
            "claim": "company fact or externally sourced observation",
            "source_title": "string",
            "source_url": "string",
            "confidence": "high|medium|low",
        }
    ],
    "account_signals": [
        {
            "signal": "specific business, operational, cyber, regulatory, or technology signal",
            "why_it_matters": "sales relevance",
            "evidence_refs": ["source_url or short source id"],
            "confidence": "high|medium|low",
        }
    ],
    "recommended_use_cases": [
        {
            "rank": 1,
            "title": "short sales-friendly use case title",
            "account_trigger": "why this account needs it",
            "problem": "specific risk or pain",
            "opswat_products": [
                {
                    "product": "official product name from capability map",
                    "slug": "capability map slug",
                    "fit_reason": "why this product fits this use case",
                    "capabilities_used": ["capability names from map"],
                    "confidence": "high|medium|low",
                    "capability_evidence_refs": ["source_path from capability map evidence"],
                }
            ],
            "deployment_hypothesis": "where it would sit in the environment; mark as inference if needed",
            "business_value": "buyer-facing value",
            "discovery_questions": ["specific question"],
            "evidence_refs": ["company source_url and/or product source_path"],
            "confidence": "high|medium|low",
        }
    ],
    "buyer_map": [
        {
            "persona": "CISO / OT Director / Infrastructure / Cloud / SOC / Compliance / Procurement",
            "likely_concerns": ["concern"],
            "message_angle": "how to frame value",
        }
    ],
    "outreach": {
        "opening_angle": "one concise outreach hook",
        "email_subjects": ["subject"],
        "first_call_agenda": ["agenda item"],
    },
    "assumptions_and_gaps": [
        {
            "item": "what remains unknown",
            "how_to_validate": "question or source to check",
        }
    ],
}


ACCOUNT_MAP_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": True,
    "required": [
        "target_account",
        "research_evidence",
        "account_signals",
        "recommended_use_cases",
        "buyer_map",
        "outreach",
        "assumptions_and_gaps",
    ],
    "properties": {
        "target_account": {
            "type": "object",
            "additionalProperties": True,
            "required": ["name", "sector", "summary"],
            "properties": {
                "name": {"type": "string"},
                "website": {"type": ["string", "null"]},
                "sector": {"type": "string"},
                "summary": {"type": "string"},
            },
        },
        "research_evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True,
                "properties": {
                    "claim": {"type": "string"},
                    "source_title": {"type": "string"},
                    "source_url": {"type": "string"},
                    "confidence": {"type": "string"},
                },
            },
        },
        "account_signals": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "recommended_use_cases": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "buyer_map": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
        "outreach": {"type": "object", "additionalProperties": True},
        "assumptions_and_gaps": {"type": "array", "items": {"type": "object", "additionalProperties": True}},
    },
}


def load_dotenv_files() -> None:
    """Load simple KEY=VALUE files without requiring python-dotenv."""
    for path in [
        PROJECT / ".env",
        Path("opswat_docs_full/opswat_docs_downloads/.env"),
        Path("rag-pipeline/.env"),
    ]:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            os.environ.setdefault(key, value)


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "account"


def load_capability_map(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    products = data.get("products")
    if not isinstance(products, list) or not products:
        raise SystemExit(f"Capability map at {path} does not contain products.")
    return data


def compact_capability_map(capability_map: dict[str, Any], max_evidence: int = 5) -> dict[str, Any]:
    """Keep prompt size down while preserving source-backed product fit."""
    products = []
    for product in capability_map.get("products", []):
        products.append(
            {
                "slug": product.get("slug"),
                "product": product.get("product"),
                "family": product.get("family"),
                "confidence": product.get("confidence"),
                "what_it_protects": product.get("what_it_protects"),
                "deployment_zones": product.get("deployment_zones", []),
                "best_fit_use_cases": product.get("best_fit_use_cases", []),
                "buyer_problems": product.get("buyer_problems", []),
                "threat_paths": product.get("threat_paths", []),
                "capabilities": product.get("capabilities", []),
                "protocols_and_integrations": product.get("protocols_and_integrations", []),
                "industries": product.get("industries", []),
                "compliance_drivers": product.get("compliance_drivers", []),
                "account_triggers": product.get("account_triggers", []),
                "evidence": [
                    {
                        "title": evidence.get("title"),
                        "category": evidence.get("category"),
                        "source_path": evidence.get("source_path"),
                        "snippet": evidence.get("snippet"),
                    }
                    for evidence in product.get("evidence", [])[:max_evidence]
                ],
            }
        )
    return {"products": products}


def build_user_prompt(target: str, capability_map: dict[str, Any], focus: str, use_cases: int) -> str:
    compact_map = compact_capability_map(capability_map)
    focus_block = focus.strip() if focus.strip() else "No special focus. Identify the highest-value sales angles."
    return f"""Target account:
{target}

User focus:
{focus_block}

Allowed OPSWAT capability map:
{json.dumps(compact_map, indent=2)}

Task:
1. Research the target account using web search.
2. Extract concrete account signals relevant to cyber, critical infrastructure, OT, IT, cloud, file movement, compliance, third-party access, and operational risk.
3. Match the strongest signals to OPSWAT products from the capability map only.
4. Generate exactly {use_cases} recommended use cases.
5. Cite company web evidence with URLs and product capability evidence with capability-map source_path values.
6. Mark unsupported environment-specific statements as inference.
7. When ready, call the write_account_map tool with the completed account map.

The write_account_map tool input must follow this shape:
{json.dumps(JSON_SHAPE, indent=2)}
"""


def extract_text(response: Any) -> str:
    parts = []
    for block in getattr(response, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "".join(parts).strip()


def extract_tool_input(response: Any, name: str) -> Optional[dict[str, Any]]:
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == name:
            value = getattr(block, "input", None)
            if isinstance(value, dict):
                return value
    return None


def account_map_tool() -> dict[str, Any]:
    return {
        "name": "write_account_map",
        "description": "Write the final source-grounded OPSWAT account map as structured data.",
        "input_schema": ACCOUNT_MAP_SCHEMA,
    }


def parse_json_response(text: str, provider: str = "model") -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise SystemExit(f"{provider} returned non-JSON output: {exc}") from exc


def completeness_gaps(account_map: dict[str, Any], min_use_cases: int) -> list[str]:
    gaps = []
    if not account_map.get("target_account"):
        gaps.append("missing target_account")
    if not account_map.get("research_evidence"):
        gaps.append("missing research_evidence")
    if not account_map.get("account_signals"):
        gaps.append("missing account_signals")
    use_cases = account_map.get("recommended_use_cases") or []
    if len(use_cases) < min_use_cases:
        gaps.append(f"only {len(use_cases)} recommended_use_cases; expected at least {min_use_cases}")
    for idx, use_case in enumerate(use_cases, 1):
        if not (use_case.get("title") or use_case.get("use_case")):
            gaps.append(f"use case {idx} missing title/use_case")
        if not (use_case.get("problem") or use_case.get("signal_link")):
            gaps.append(f"use case {idx} missing problem/signal_link")
    buyer_map = account_map.get("buyer_map") or []
    if not buyer_map:
        gaps.append("missing buyer_map")
    for idx, persona in enumerate(buyer_map, 1):
        if not (persona.get("persona") or persona.get("role")):
            gaps.append(f"buyer_map item {idx} missing persona/role")
    outreach = account_map.get("outreach") or {}
    if not outreach.get("opening_angle"):
        gaps.append("missing outreach.opening_angle")
    return gaps


def repair_required_gaps(account_map: dict[str, Any], min_use_cases: int) -> list[str]:
    """Return only gaps that need another model call rather than local defaults."""
    gaps = []
    if not account_map.get("target_account"):
        gaps.append("missing target_account")
    if not account_map.get("research_evidence"):
        gaps.append("missing research_evidence")
    use_cases = account_map.get("recommended_use_cases") or []
    if len(use_cases) < min_use_cases:
        gaps.append(f"only {len(use_cases)} recommended_use_cases; expected at least {min_use_cases}")
    for idx, use_case in enumerate(use_cases, 1):
        if not (use_case.get("title") or use_case.get("use_case")):
            gaps.append(f"use case {idx} missing title/use_case")
        if not (use_case.get("problem") or use_case.get("signal_link") or use_case.get("account_trigger")):
            gaps.append(f"use case {idx} missing problem/signal_link")
    return gaps


def finalize_account_map(
    client: Any,
    args: argparse.Namespace,
    capability_map: dict[str, Any],
    draft: dict[str, Any],
    gaps: list[str],
) -> dict[str, Any]:
    prompt = f"""The first account-map draft was incomplete.

Target account: {args.target}
Sales focus: {args.focus or "None supplied"}
Required use cases: {args.use_cases}

Completeness gaps:
{json.dumps(gaps, indent=2)}

Draft account research and partial output:
{json.dumps(draft, indent=2, ensure_ascii=False)}

Allowed OPSWAT capability map:
{json.dumps(capability_map, indent=2, ensure_ascii=False)}

Create the final complete account map now. Use only the company evidence already in the draft plus clearly marked inferences from that evidence. Use only product slugs and capabilities present in the capability map. Call write_account_map with the complete final structure.
"""
    response = client.messages.create(
        model=args.model,
        max_tokens=args.max_tokens,
        system=SYSTEM_PROMPT,
        tools=[account_map_tool()],
        tool_choice={"type": "tool", "name": "write_account_map"},
        messages=[{"role": "user", "content": prompt}],
    )
    tool_input = extract_tool_input(response, "write_account_map")
    if tool_input is None:
        tool_input = parse_json_response(extract_text(response))
    tool_input.setdefault("_raw_text", extract_text(response))
    tool_input.setdefault("_structured_output", "forced_tool_use")
    return tool_input


def openai_json_format() -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": "opswat_account_map",
            "schema": ACCOUNT_MAP_SCHEMA,
            "strict": False,
        }
    }


def generate_with_openai(args: argparse.Namespace, capability_map: dict[str, Any], prompt: str) -> dict[str, Any]:
    api_key = getattr(args, "openai_api_key", None) or os.environ.get("OPENAI_API_KEY")
    if not api_key and not args.dry_run:
        raise SystemExit("Missing OPENAI_API_KEY. Add it to .env or enter an OpenAI API key in the app.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Missing openai package. Run this in a venv with openai installed.") from exc

    client = OpenAI(api_key=api_key, timeout=DEFAULT_MODEL_TIMEOUT_SECONDS)
    response = client.responses.create(
        model=args.model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        tools=[{"type": "web_search_preview"}],
        reasoning={"effort": args.openai_reasoning},
        text=openai_json_format(),
        max_output_tokens=args.max_tokens,
    )
    raw_text = getattr(response, "output_text", "") or ""
    if not raw_text:
        try:
            raw_text = response.model_dump_json()
        except Exception:
            raw_text = str(response)
    parsed = parse_json_response(raw_text, "OpenAI")
    parsed.setdefault("_raw_text", raw_text)
    parsed.setdefault("_structured_output", "openai_responses")
    return parsed


def finalize_account_map_openai(
    args: argparse.Namespace,
    capability_map: dict[str, Any],
    draft: dict[str, Any],
    gaps: list[str],
) -> dict[str, Any]:
    api_key = getattr(args, "openai_api_key", None) or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Missing OPENAI_API_KEY. Add it to .env or enter an OpenAI API key in the app.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Missing openai package. Run this in a venv with openai installed.") from exc

    prompt = f"""The first account-map draft was incomplete.

Target account: {args.target}
Sales focus: {args.focus or "None supplied"}
Required use cases: {args.use_cases}

Completeness gaps:
{json.dumps(gaps, indent=2)}

Draft account research and partial output:
{json.dumps(draft, indent=2, ensure_ascii=False)}

Allowed OPSWAT capability map:
{json.dumps(capability_map, indent=2, ensure_ascii=False)}

Create the final complete account map now. Use only the company evidence already in the draft plus clearly marked inferences from that evidence. Use only product slugs and capabilities present in the capability map. Return only the complete final JSON structure.
"""
    client = OpenAI(api_key=api_key, timeout=DEFAULT_MODEL_TIMEOUT_SECONDS)
    response = client.responses.create(
        model=args.model,
        input=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        reasoning={"effort": args.openai_reasoning},
        text=openai_json_format(),
        max_output_tokens=args.max_tokens,
    )
    raw_text = getattr(response, "output_text", "") or ""
    parsed = parse_json_response(raw_text, "OpenAI")
    parsed.setdefault("_raw_text", raw_text)
    parsed.setdefault("_structured_output", "openai_repair")
    return parsed


def evidence_refs_for_product(product: dict[str, Any], capability_map: dict[str, Any]) -> list[str]:
    slug = product.get("slug")
    capability_names = set(product.get("capabilities_used") or [])
    for mapped in capability_map.get("products", []):
        if mapped.get("slug") != slug:
            continue
        refs = []
        for evidence in mapped.get("evidence", []):
            matched = set(evidence.get("matched_capabilities") or [])
            if capability_names and matched and not (matched & capability_names):
                continue
            source_path = evidence.get("source_path")
            if source_path and source_path not in refs:
                refs.append(source_path)
        if not refs:
            for evidence in mapped.get("evidence", [])[:4]:
                source_path = evidence.get("source_path")
                if source_path and source_path not in refs:
                    refs.append(source_path)
        return refs[:4]
    return []


def normalize_account_map(account_map: dict[str, Any], capability_map: dict[str, Any]) -> dict[str, Any]:
    account_map = json.loads(json.dumps(account_map))
    if not isinstance(account_map.get("recommended_use_cases"), list):
        account_map["recommended_use_cases"] = []
    if not isinstance(account_map.get("research_evidence"), list):
        account_map["research_evidence"] = []
    if not isinstance(account_map.get("account_signals"), list):
        account_map["account_signals"] = []
    if not isinstance(account_map.get("buyer_map"), list):
        account_map["buyer_map"] = []
    account_discovery_questions = (account_map.get("outreach") or {}).get("discovery_questions") or []
    for idx, use_case in enumerate(account_map.get("recommended_use_cases") or [], 1):
        use_case.setdefault("rank", idx)
        if "title" not in use_case and use_case.get("use_case"):
            use_case["title"] = use_case["use_case"]
        if "account_trigger" not in use_case and use_case.get("signal_link"):
            use_case["account_trigger"] = use_case["signal_link"]
        if "problem" not in use_case and use_case.get("signal_link"):
            use_case["problem"] = use_case["signal_link"]
        if "business_value" not in use_case and use_case.get("product_fit"):
            use_case["business_value"] = use_case["product_fit"]
        if "deployment_hypothesis" not in use_case:
            use_case["deployment_hypothesis"] = "Inference: validate the exact deployment point during discovery."
        if not use_case.get("discovery_questions"):
            title = use_case.get("title") or use_case.get("use_case") or "this use case"
            fallback_questions = [
                f"Where would {title.lower()} create the most immediate operational value?",
                "Who owns the current control and exception process for this workflow?",
                "What evidence would you need to show the control is working for audit or assurance?",
            ]
            use_case["discovery_questions"] = account_discovery_questions[idx - 1 : idx] or fallback_questions
        if isinstance(use_case.get("discovery_questions"), str):
            use_case["discovery_questions"] = [use_case["discovery_questions"]]
        use_case.setdefault("evidence_refs", [])
        if isinstance(use_case.get("evidence_refs"), str):
            use_case["evidence_refs"] = [use_case["evidence_refs"]]
        if not isinstance(use_case.get("opswat_products"), list):
            use_case["opswat_products"] = []
        normalized_products = []
        for product in use_case.get("opswat_products") or []:
            if not isinstance(product, dict):
                continue
            product.setdefault("fit_reason", use_case.get("product_fit", ""))
            product.setdefault("confidence", use_case.get("confidence", "medium"))
            if not product.get("capability_evidence_refs"):
                product["capability_evidence_refs"] = evidence_refs_for_product(product, capability_map)
            normalized_products.append(product)
        use_case["opswat_products"] = normalized_products
    if not account_map.get("account_signals"):
        for use_case in account_map.get("recommended_use_cases") or []:
            signal = use_case.get("account_trigger") or use_case.get("problem")
            if signal:
                account_map["account_signals"].append(
                    {
                        "signal": signal,
                        "why_it_matters": use_case.get("business_value") or use_case.get("problem") or "",
                        "evidence_refs": use_case.get("evidence_refs") or [],
                        "confidence": use_case.get("confidence", "medium"),
                    }
                )
    if not account_map.get("buyer_map"):
        account_map["buyer_map"] = [
            {
                "persona": "CISO / OT Security Lead",
                "likely_concerns": [
                    "Reducing risk from untrusted files, third-party access, and operational disruption",
                    "Showing evidence that security controls align to critical-infrastructure requirements",
                ],
                "message_angle": "Use the mapped OPSWAT controls as a discovery-led way to reduce file and media risk around critical operations.",
            }
        ]
    for persona in account_map.get("buyer_map") or []:
        if "persona" not in persona and persona.get("role"):
            persona["persona"] = persona["role"]
        if "likely_concerns" not in persona:
            persona["likely_concerns"] = persona.get("talking_points", [])
        if "message_angle" not in persona:
            persona["message_angle"] = persona.get("why_relevant", "")
    outreach = account_map.setdefault("outreach", {})
    if not outreach.get("opening_angle"):
        target_name = (account_map.get("target_account") or {}).get("name", "the account")
        first_use_case = (account_map.get("recommended_use_cases") or [{}])[0]
        hook = first_use_case.get("title") or "critical file and media security"
        outreach["opening_angle"] = f"Explore where {hook.lower()} could reduce operational cyber risk for {target_name}."
    if "first_call_agenda" not in outreach:
        agenda = []
        if outreach.get("recommended_first_meeting"):
            agenda.append(outreach["recommended_first_meeting"])
        agenda.extend(outreach.get("discovery_questions", [])[:4])
        outreach["first_call_agenda"] = agenda
    outreach.setdefault("email_subjects", [])
    if not outreach["email_subjects"]:
        target_name = (account_map.get("target_account") or {}).get("name", "your OT estate")
        outreach["email_subjects"] = [
            f"Secure file movement for {target_name}",
            "CAF-aligned controls for OT file ingress",
            "Reducing vendor media risk at operational sites",
        ]
    for gap in account_map.get("assumptions_and_gaps") or []:
        if not gap.get("how_to_validate"):
            gap["how_to_validate"] = "Validate with the account team or during discovery."
    return account_map


def account_map_to_markdown(account_map: dict[str, Any]) -> str:
    target = account_map.get("target_account", {})
    lines = [
        f"# Account Map: {target.get('name') or 'Unknown Account'}",
        "",
        f"- Website: {target.get('website') or 'Unknown'}",
        f"- Sector: {target.get('sector') or 'Unknown'}",
        "",
        target.get("summary", "").strip(),
        "",
        "## Account Signals",
    ]
    for signal in account_map.get("account_signals", []):
        lines.extend(
            [
                f"- **{signal.get('signal', '')}** ({signal.get('confidence', 'unknown')})",
                f"  - {signal.get('why_it_matters', '')}",
            ]
        )
    lines.extend(["", "## Recommended Use Cases"])
    for use_case in account_map.get("recommended_use_cases", []):
        lines.extend(
            [
                f"### {use_case.get('rank')}. {use_case.get('title')}",
                "",
                f"**Trigger:** {use_case.get('account_trigger', '')}",
                "",
                f"**Problem:** {use_case.get('problem', '')}",
                "",
                f"**Deployment Hypothesis:** {use_case.get('deployment_hypothesis', '')}",
                "",
                f"**Business Value:** {use_case.get('business_value', '')}",
                "",
                "**OPSWAT Product Fit:**",
            ]
        )
        for product in use_case.get("opswat_products", []):
            lines.extend(
                [
                    f"- {product.get('product')} (`{product.get('slug')}`) - {product.get('fit_reason')}",
                    f"  - Capabilities: {', '.join(product.get('capabilities_used', []))}",
                    f"  - Confidence: {product.get('confidence')}",
                    f"  - Product evidence: {', '.join(product.get('capability_evidence_refs', []))}",
                ]
            )
        lines.extend(["", "**Discovery Questions:**"])
        for question in use_case.get("discovery_questions", []):
            lines.append(f"- {question}")
        diagram = use_case.get("diagram") or {}
        if diagram.get("svg_url"):
            lines.extend(["", f"**Diagram:** [{diagram.get('title') or 'Open diagram'}]({diagram.get('svg_url')})"])
        elif diagram.get("error"):
            lines.extend(["", f"**Diagram:** generation failed - {diagram.get('error')}"])
        lines.append("")
    lines.extend(["## Buyer Map"])
    for persona in account_map.get("buyer_map", []):
        lines.extend(
            [
                f"- **{persona.get('persona', '')}** - {persona.get('message_angle', '')}",
                f"  - Concerns: {', '.join(persona.get('likely_concerns', []))}",
            ]
        )
    outreach = account_map.get("outreach", {})
    lines.extend(
        [
            "",
            "## Outreach",
            "",
            f"**Opening angle:** {outreach.get('opening_angle', '')}",
            "",
            "**Email subjects:**",
        ]
    )
    for subject in outreach.get("email_subjects", []):
        lines.append(f"- {subject}")
    lines.extend(["", "**First call agenda:**"])
    for item in outreach.get("first_call_agenda", []):
        lines.append(f"- {item}")
    lines.extend(["", "## Assumptions And Gaps"])
    for gap in account_map.get("assumptions_and_gaps", []):
        lines.extend([f"- {gap.get('item', '')}", f"  - Validate: {gap.get('how_to_validate', '')}"])
    lines.extend(["", "## Research Evidence"])
    for evidence in account_map.get("research_evidence", []):
        url = evidence.get("source_url", "")
        title = evidence.get("source_title", "") or url
        lines.append(f"- {evidence.get('claim', '')} ({evidence.get('confidence', '')}) - [{title}]({url})")
    return "\n".join(lines).strip() + "\n"


def write_outputs(account_map: dict[str, Any], target: str, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{slugify(target)}-{stamp}"
    json_path = out_dir / f"{base}.json"
    md_path = out_dir / f"{base}.md"
    json_path.write_text(json.dumps(account_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(account_map_to_markdown(account_map), encoding="utf-8")
    return json_path, md_path


def generate_account_map(args: argparse.Namespace) -> dict[str, Any]:
    load_dotenv_files()
    provider = (getattr(args, "provider", None) or DEFAULT_PROVIDER).lower()
    if provider not in {"anthropic", "openai"}:
        raise SystemExit(f"Unsupported provider: {provider}")
    if not getattr(args, "model", None):
        args.model = DEFAULT_OPENAI_MODEL if provider == "openai" else DEFAULT_ANTHROPIC_MODEL
    if not getattr(args, "openai_reasoning", None):
        args.openai_reasoning = DEFAULT_OPENAI_REASONING
    capability_map = load_capability_map(Path(args.capability_map))
    prompt = build_user_prompt(args.target, capability_map, args.focus or "", args.use_cases)

    if args.dry_run:
        return {
            "dry_run": True,
            "provider": provider,
            "model": args.model,
            "openai_reasoning": args.openai_reasoning if provider == "openai" else None,
            "target": args.target,
            "prompt_preview": prompt,
        }

    if provider == "openai":
        parsed = generate_with_openai(args, capability_map, prompt)
        parsed = normalize_account_map(parsed, capability_map)
        gaps = repair_required_gaps(parsed, args.use_cases)
        if gaps:
            logger.info("openai_account_map_repair_required gaps=%s", "; ".join(gaps))
            parsed = finalize_account_map_openai(args, capability_map, parsed, gaps)
            parsed = normalize_account_map(parsed, capability_map)
            gaps = repair_required_gaps(parsed, args.use_cases)
            if gaps:
                raise SystemExit(f"OpenAI returned an incomplete account map after repair: {', '.join(gaps)}")
        parsed.setdefault("_meta", {})
        parsed["_meta"].update(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "provider": provider,
                "model": args.model,
                "openai_reasoning": args.openai_reasoning,
                "capability_map": str(Path(args.capability_map).resolve()),
                "target_input": args.target,
            }
        )
        return parsed

    api_key = getattr(args, "anthropic_api_key", None) or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Missing ANTHROPIC_API_KEY. Add it to .env or enter an Anthropic API key in the app.")

    try:
        import anthropic
    except ImportError as exc:
        raise SystemExit("Missing anthropic package. Run this in a venv with anthropic installed.") from exc

    client = anthropic.Anthropic(api_key=api_key, timeout=DEFAULT_MODEL_TIMEOUT_SECONDS)
    response = client.messages.create(
        model=args.model,
        max_tokens=args.max_tokens,
        system=SYSTEM_PROMPT,
        tools=[{"type": args.web_search_tool, "name": "web_search"}, account_map_tool()],
        messages=[{"role": "user", "content": prompt}],
    )
    tool_input = extract_tool_input(response, "write_account_map")
    if tool_input is not None:
        parsed = tool_input
        parsed.setdefault("_raw_text", extract_text(response))
        parsed.setdefault("_structured_output", "tool_use")
    else:
        raw_text = extract_text(response)
        parsed = parse_json_response(raw_text)
    parsed = normalize_account_map(parsed, capability_map)
    gaps = repair_required_gaps(parsed, args.use_cases)
    if gaps:
        logger.info("anthropic_account_map_repair_required gaps=%s", "; ".join(gaps))
        parsed = finalize_account_map(client, args, capability_map, parsed, gaps)
        parsed = normalize_account_map(parsed, capability_map)
        gaps = repair_required_gaps(parsed, args.use_cases)
        if gaps:
            raise SystemExit(f"Claude returned an incomplete account map after repair: {', '.join(gaps)}")
    parsed.setdefault("_meta", {})
    parsed["_meta"].update(
        {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "provider": provider,
            "model": args.model,
            "capability_map": str(Path(args.capability_map).resolve()),
            "target_input": args.target,
        }
    )
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate an OPSWAT account map for a company.")
    parser.add_argument("target", help="Company name, domain, or URL. Example: 'SSE energy company' or sse.com")
    parser.add_argument("--focus", default="", help="Optional sales focus, pain, product family, or compliance driver.")
    parser.add_argument("--use-cases", type=int, default=5, help="Number of use cases to generate.")
    parser.add_argument("--provider", choices=["anthropic", "openai"], default=os.environ.get("ACCOUNT_MAP_PROVIDER", DEFAULT_PROVIDER))
    parser.add_argument("--model", default=None)
    parser.add_argument("--anthropic-api-key", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--openai-api-key", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--openai-reasoning", default=os.environ.get("OPENAI_REASONING_EFFORT", DEFAULT_OPENAI_REASONING))
    parser.add_argument("--max-tokens", type=int, default=9000)
    parser.add_argument("--web-search-tool", default="web_search_20250305")
    parser.add_argument("--capability-map", default=str(DEFAULT_CAPABILITY_MAP))
    parser.add_argument("--out-dir", default=str(OUTPUTS_DIR / "account_maps"))
    parser.add_argument("--dry-run", action="store_true", help="Write the prompt preview without calling Claude.")
    parser.add_argument("--print-json", action="store_true", help="Print JSON to stdout as well as writing files.")
    return parser


def main() -> int:
    parser = build_arg_parser()
    args = parser.parse_args()
    account_map = generate_account_map(args)
    json_path, md_path = write_outputs(account_map, args.target, Path(args.out_dir))
    if args.print_json:
        print(json.dumps(account_map, indent=2, ensure_ascii=False))
    print(f"Wrote JSON: {json_path}")
    print(f"Wrote Markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

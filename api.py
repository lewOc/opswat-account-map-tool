#!/usr/bin/env python3
"""FastAPI wrapper for the OPSWAT account-map tool."""

from __future__ import annotations

import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error as urllib_error
import urllib.request as urllib_request
from pathlib import Path
from threading import Lock
from typing import Any, Optional
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.v2 import router as v2_router


PROJECT = Path(__file__).resolve().parent
UI_DIR = PROJECT / "ui"
OUTPUT_DIR = Path(os.environ.get("ACCOUNT_MAP_OUTPUT_DIR", PROJECT / "outputs" / "account_maps"))
DECK_OUTPUT_DIR = Path(os.environ.get("DECK_OUTPUT_DIR", PROJECT / "outputs" / "decks"))
DIAGRAM_OUTPUT_DIR = Path(os.environ.get("DIAGRAM_OUTPUT_DIR", PROJECT / "outputs" / "diagrams"))
IMAGE_DIAGRAM_OUTPUT_DIR = Path(os.environ.get("IMAGE_DIAGRAM_OUTPUT_DIR", PROJECT / "outputs" / "image_diagrams"))
REFERENCE_DIAGRAM_DIR = Path(os.environ.get("REFERENCE_DIAGRAM_DIR", PROJECT / "assets" / "references" / "diagrams"))
PRODUCT_ICON_DIR = Path(os.environ.get("PRODUCT_ICON_DIR", PROJECT / "assets" / "product_icons"))
CAPABILITY_MAP = Path(os.environ.get("CAPABILITY_MAP_PATH", PROJECT / "data" / "capability_map.json"))
ACCOUNT_MAP_SCRIPT = PROJECT / "scripts" / "account_map.py"
DECK_SCRIPT = PROJECT / "scripts" / "export_deck.mjs"
DIAGRAM_SCRIPT = PROJECT / "scripts" / "diagram_generator.py"
NODE_BIN = Path(os.environ.get("NODE_BIN") or shutil.which("node") or "node")
TEMPLATE_PPTX = Path(os.environ.get("PRESENTATION_TEMPLATE_PATH", PROJECT / "templates" / "presentation_template.pptx"))
EXPORT_API_URL = os.environ.get("EXPORT_API_URL", "").rstrip("/")
DEFAULT_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
logger = logging.getLogger("opswat_account_map_api")
logger.setLevel(logging.INFO)
GENERATION_WORKERS = int(os.environ.get("GENERATION_WORKERS", "2"))
JOB_RETENTION_SECONDS = int(os.environ.get("GENERATION_JOB_RETENTION_SECONDS", "7200"))
generation_executor = ThreadPoolExecutor(max_workers=GENERATION_WORKERS)
generation_jobs: dict[str, dict[str, Any]] = {}
generation_jobs_lock = Lock()


def generation_log(event: str, **fields: Any) -> None:
    parts = [f"{key}={value!r}" for key, value in fields.items()]
    print(f"ACCOUNT_MAP {event} {' '.join(parts)}", flush=True)


def prune_generation_jobs() -> None:
    cutoff = time.time() - JOB_RETENTION_SECONDS
    with generation_jobs_lock:
        expired = [
            job_id
            for job_id, job in generation_jobs.items()
            if job.get("updated_at", job.get("created_at", 0)) < cutoff and job.get("status") in {"completed", "failed"}
        ]
        for job_id in expired:
            generation_jobs.pop(job_id, None)


def update_generation_job(job_id: str, **fields: Any) -> None:
    with generation_jobs_lock:
        job = generation_jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def generation_job_snapshot(job_id: str) -> dict[str, Any]:
    with generation_jobs_lock:
        job = generation_jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Generation job not found")
        return {
            key: value
            for key, value in job.items()
            if key not in {"future"}
        }


def run_generation_job(job_id: str, payload: GenerateRequest) -> None:
    update_generation_job(job_id, status="running", message="Researching account and mapping OPSWAT use cases")
    try:
        result = run_generation(payload)
        update_generation_job(job_id, status="completed", message="Complete", result=result)
    except BaseException as exc:
        update_generation_job(job_id, status="failed", message="Failed", error=str(exc) or repr(exc))


def load_account_map_module() -> Any:
    spec = importlib.util.spec_from_file_location("account_map_generator", ACCOUNT_MAP_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {ACCOUNT_MAP_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_diagram_module() -> Any:
    spec = importlib.util.spec_from_file_location("diagram_generator", DIAGRAM_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {DIAGRAM_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = load_account_map_module()
diagram_generator = load_diagram_module()

app = FastAPI(title="OPSWAT Account Map Tool", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/assets", StaticFiles(directory=UI_DIR), name="assets")
app.include_router(v2_router)


class GenerateRequest(BaseModel):
    target: str = Field(..., min_length=2, max_length=200)
    focus: str = Field(default="", max_length=600)
    use_cases: int = Field(default=5, ge=1, le=8)
    provider: str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    model: Optional[str] = None
    anthropic_api_key: Optional[str] = Field(default=None, max_length=1000)
    openai_api_key: Optional[str] = Field(default=None, max_length=1000)
    openai_reasoning: str = Field(default="medium", pattern="^(low|medium|high)$")
    max_tokens: int = Field(default=30000, ge=2000, le=30000)
    diagram_renderer: str = Field(default="svg", pattern="^(svg|gpt_image)$")
    diagram_openai_api_key: Optional[str] = Field(default=None, max_length=1000)
    diagram_image_quality: str = Field(default="high", pattern="^(low|medium|high|auto)$")
    dry_run: bool = False


def map_id_from_path(path: Path) -> str:
    return path.stem


def paths_for_id(map_id: str) -> tuple[Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", map_id):
        raise HTTPException(status_code=404, detail="Account map not found")
    json_path = OUTPUT_DIR / f"{map_id}.json"
    md_path = OUTPUT_DIR / f"{map_id}.md"
    if not json_path.exists():
        raise HTTPException(status_code=404, detail="Account map not found")
    return json_path, md_path


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_map(json_path: Path) -> dict[str, Any]:
    data = read_json(json_path)
    target = data.get("target_account") or {}
    use_cases = data.get("recommended_use_cases") or []
    evidence = data.get("research_evidence") or []
    meta = data.get("_meta") or {}
    diagram_count = sum(
        1
        for use_case in use_cases
        if (use_case.get("diagram") or {}).get("svg_url") or (use_case.get("diagram") or {}).get("image_url")
    )
    map_id = map_id_from_path(json_path)
    deck_path = DECK_OUTPUT_DIR / f"{map_id}-account-map.pptx"
    return {
        "id": map_id,
        "target_name": target.get("name") or json_path.stem,
        "sector": target.get("sector"),
        "generated_at": meta.get("generated_at"),
        "target_input": meta.get("target_input"),
        "use_case_count": len(use_cases),
        "diagram_count": diagram_count,
        "evidence_count": len(evidence),
        "json_url": f"/api/account-maps/{map_id}",
        "markdown_url": f"/api/account-maps/{map_id}/markdown",
        "deck_url": f"/api/decks/{deck_path.name}" if deck_path.exists() else None,
    }


def diagram_file_exists(diagram: dict[str, Any]) -> bool:
    svg_url = diagram.get("svg_url")
    image_url = diagram.get("image_url")
    if isinstance(svg_url, str) and svg_url:
        filename = Path(svg_url).name
        return bool(re.fullmatch(r"[A-Za-z0-9_.-]+\.svg", filename)) and (DIAGRAM_OUTPUT_DIR / filename).exists()
    if isinstance(image_url, str) and image_url:
        filename = Path(image_url).name
        return safe_image_filename(filename) and (IMAGE_DIAGRAM_OUTPUT_DIR / filename).exists()
    return False


def safe_image_filename(filename: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+\.(png|jpg|jpeg|webp|json)", filename))


def image_asset_paths(directory: Path, limit: int) -> list[Path]:
    if limit <= 0 or not directory.exists():
        return []
    return [
        path
        for path in sorted(directory.iterdir(), key=lambda item: item.name.lower())
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    ][:limit]


PRODUCT_ICON_RULES = [
    (("media validation", "validation"), "mobile_validation.png"),
    (("media firewall", "firewall"), "media_firewall.png"),
    (("managed file transfer", "mft"), "managed_file_transfer_mft.png"),
    (("kiosk",), "kiosk_tower.png"),
    (("core", "scanning", "malware scan"), "on_premises.png"),
    (("data diode", "diode"), "transfer_guard.png"),
    (("drive",), "drive.png"),
    (("email",), "email_Security.png"),
    (("storage", "nas"), "secure_storage.png"),
    (("ot device", "plc", "rtu", "hmi"), "ot_Security.png"),
]


def product_icon_reference_paths(prompt: str, limit: int) -> list[Path]:
    if limit <= 0:
        return []
    text = prompt.lower()
    paths: list[Path] = []
    seen: set[Path] = set()
    for keywords, filename in PRODUCT_ICON_RULES:
        if not any(keyword in text for keyword in keywords):
            continue
        path = PRODUCT_ICON_DIR / filename
        if path.exists() and path not in seen:
            paths.append(path)
            seen.add(path)
        if len(paths) >= limit:
            break
    return paths


def build_image_diagram_brief(account_map: dict[str, Any], use_case: dict[str, Any]) -> str:
    target = account_map.get("target_account") or {}
    products = [
        product.get("product") or product.get("slug")
        for product in use_case.get("opswat_products") or []
        if isinstance(product, dict) and (product.get("product") or product.get("slug"))
    ]
    flow = use_case.get("implementation_flow") or []
    if not isinstance(flow, list):
        flow = [str(flow)]
    return f"""Account: {target.get("name") or "Account"}
Use case: {use_case.get("title") or "Use case"}

Problem context:
{use_case.get("problem_narrative") or use_case.get("problem") or use_case.get("account_trigger") or ""}

Proposed OPSWAT solution:
{use_case.get("solution_narrative") or use_case.get("deployment_hypothesis") or ""}

Products to show:
{", ".join(products) or "Relevant OPSWAT products from the use case"}

Implementation flow:
{json.dumps(flow, indent=2)}

Business value:
{use_case.get("business_value_narrative") or use_case.get("business_value") or ""}
"""


def build_image_generation_prompt(title: str, account_name: str, brief: str, reference_paths: list[Path], icon_paths: list[Path]) -> str:
    reference_names = "\n".join(f"- {path.name}" for path in reference_paths) or "- None"
    icon_names = "\n".join(f"- {path.name}" for path in icon_paths) or "- None"
    return f"""Create a polished OPSWAT-style light technical architecture diagram.

Title: {title}
Account/project context: {account_name}

Use the attached manual diagrams as visual style references. Match their clean white background, dark navy linework, OPSWAT blue product treatments, balanced spacing, readable labels, and sales-engineering architecture style.

Reference diagrams attached:
{reference_names}

Product/icon references attached:
{icon_names}

Diagram brief:
{brief}

Hard requirements:
- Keep all labels legible and correctly spelled.
- Use real OPSWAT product names exactly as provided in the brief.
- Prefer straight thin navy arrows for left-to-right flow.
- Use restrained right-angle connectors only where split/merge routing is necessary.
- Use white cards for external people, media, systems, and destinations.
- Use the attached OPSWAT product icon references where relevant; do not invent unrelated product icons.
- Do not add threat, quarantine, malware, or blocked-file paths unless explicitly requested by the brief.
- Produce one complete presentation-ready diagram, not a collage of the references.
"""


def create_image_diagram_for_use_case(
    account_map: dict[str, Any],
    use_case: dict[str, Any],
    api_key: str,
    quality: str = "high",
) -> dict[str, Any]:
    if not api_key:
        raise RuntimeError("OpenAI API key is required for GPT Image diagrams")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed.") from exc

    title = use_case.get("title") or "OPSWAT use case diagram"
    account_name = (account_map.get("target_account") or {}).get("name") or "Account"
    brief = build_image_diagram_brief(account_map, use_case)
    reference_paths = image_asset_paths(REFERENCE_DIAGRAM_DIR, 6)
    remaining_slots = max(0, 16 - len(reference_paths))
    icon_paths = product_icon_reference_paths(brief, min(8, remaining_slots))
    image_paths = reference_paths + icon_paths
    if not image_paths:
        raise RuntimeError(f"No reference images found in {REFERENCE_DIAGRAM_DIR}")

    prompt = build_image_generation_prompt(title, account_name, brief, reference_paths, icon_paths)
    client = OpenAI(api_key=api_key, timeout=600, max_retries=0)
    opened_files = [path.open("rb") for path in image_paths]
    try:
        result = client.images.edit(
            model=DEFAULT_IMAGE_MODEL,
            image=opened_files,
            prompt=prompt,
            size="1536x1024",
            quality=quality,
            output_format="png",
            n=1,
        )
    finally:
        for file_handle in opened_files:
            file_handle.close()

    encoded = getattr(result.data[0], "b64_json", None)
    if not encoded:
        raise RuntimeError("OpenAI image response did not include b64_json")
    image_bytes = base64.b64decode(encoded)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    diagram_id = f"{diagram_generator.slugify(title)}-{stamp}"
    IMAGE_DIAGRAM_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    image_path = IMAGE_DIAGRAM_OUTPUT_DIR / f"{diagram_id}.png"
    metadata_path = IMAGE_DIAGRAM_OUTPUT_DIR / f"{diagram_id}.json"
    image_path.write_bytes(image_bytes)
    metadata = {
        "id": diagram_id,
        "model": DEFAULT_IMAGE_MODEL,
        "quality": quality,
        "size": "1536x1024",
        "output_format": "png",
        "prompt": prompt,
        "reference_diagrams": [path.name for path in reference_paths],
        "product_icons": [path.name for path in icon_paths],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return {
        **metadata,
        "image_url": f"/api/image-diagrams/{image_path.name}",
        "json_url": f"/api/image-diagrams/{metadata_path.name}",
    }


def diagram_payload_for_use_case(account_map: dict[str, Any], use_case: dict[str, Any], index: int) -> dict[str, Any]:
    target = account_map.get("target_account") or {}
    account_name = target.get("name") or (account_map.get("_meta") or {}).get("target_input") or "Account"
    title = use_case.get("title") or use_case.get("use_case") or f"Use case {index + 1}"
    return {
        "title": title,
        "subtitle": "SECURING THE FLOW OF DATA",
        "account_name": account_name,
        "pattern": "auto",
        "include_purdue": True,
        "use_case": use_case,
        "products": use_case.get("opswat_products") or [],
    }


def enrich_account_map_with_diagrams(
    account_map: dict[str, Any],
    renderer: str = "svg",
    openai_api_key: str = "",
    image_quality: str = "high",
) -> int:
    use_cases = account_map.get("recommended_use_cases")
    if not isinstance(use_cases, list) or not use_cases:
        return 0

    generated = 0
    for index, use_case in enumerate(use_cases):
        if not isinstance(use_case, dict):
            continue
        existing = use_case.get("diagram")
        if isinstance(existing, dict) and diagram_file_exists(existing):
            continue
        try:
            if renderer == "gpt_image":
                image_result = create_image_diagram_for_use_case(account_map, use_case, openai_api_key, image_quality)
                use_case["diagram"] = {
                    "id": image_result["id"],
                    "title": use_case.get("title"),
                    "pattern": "gpt_image",
                    "renderer": "gpt_image",
                    "image_url": image_result["image_url"],
                    "json_url": image_result["json_url"],
                    "model": image_result.get("model"),
                }
            else:
                artifact = diagram_generator.generate_diagram(diagram_payload_for_use_case(account_map, use_case, index))
                json_path, svg_path = diagram_generator.write_diagram(artifact, DIAGRAM_OUTPUT_DIR)
                use_case["diagram"] = {
                    "id": artifact.diagram_id,
                    "title": artifact.spec.get("title"),
                    "pattern": artifact.spec.get("pattern"),
                    "renderer": "svg",
                    "svg_url": f"/api/diagrams/{svg_path.name}",
                    "json_url": f"/api/diagrams/{json_path.name}",
                }
            generated += 1
        except Exception as exc:
            if renderer == "gpt_image":
                try:
                    artifact = diagram_generator.generate_diagram(diagram_payload_for_use_case(account_map, use_case, index))
                    json_path, svg_path = diagram_generator.write_diagram(artifact, DIAGRAM_OUTPUT_DIR)
                    use_case["diagram"] = {
                        "id": artifact.diagram_id,
                        "title": artifact.spec.get("title"),
                        "pattern": artifact.spec.get("pattern"),
                        "renderer": "svg",
                        "svg_url": f"/api/diagrams/{svg_path.name}",
                        "json_url": f"/api/diagrams/{json_path.name}",
                        "fallback_reason": str(exc),
                    }
                    generated += 1
                except Exception as fallback_exc:
                    use_case["diagram"] = {"error": f"GPT Image failed: {exc}; SVG fallback failed: {fallback_exc}"}
            else:
                use_case["diagram"] = {
                    "error": str(exc),
                }

    account_map.setdefault("_meta", {})
    account_map["_meta"]["diagram_generation"] = {
        "generated_count": sum(
            1
            for use_case in use_cases
            if (use_case.get("diagram") or {}).get("svg_url") or (use_case.get("diagram") or {}).get("image_url")
        ),
        "generator": "gpt-image" if renderer == "gpt_image" else "opswat-2023-light-flow",
    }
    return generated


def write_account_map_files(account_map: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(account_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(generator.account_map_to_markdown(account_map), encoding="utf-8")


def run_generation(payload: GenerateRequest) -> dict[str, Any]:
    started = time.monotonic()
    generation_log(
        "generation_started",
        provider=payload.provider,
        target=payload.target,
        use_cases=payload.use_cases,
        dry_run=payload.dry_run,
    )
    if payload.provider == "openai" and not payload.openai_api_key:
        raise ValueError("Enter your OpenAI API key before generating.")
    if payload.provider == "anthropic" and not payload.anthropic_api_key:
        raise ValueError("Enter your Anthropic API key before generating.")
    diagram_openai_key = payload.diagram_openai_api_key or payload.openai_api_key or ""
    if payload.diagram_renderer == "gpt_image" and not payload.dry_run and not diagram_openai_key:
        raise ValueError("Enter an OpenAI API key for GPT Image diagrams.")
    args = argparse.Namespace(
        target=payload.target,
        focus=payload.focus,
        use_cases=payload.use_cases,
        provider=payload.provider,
        model=payload.model,
        anthropic_api_key=payload.anthropic_api_key,
        openai_api_key=payload.openai_api_key,
        openai_reasoning=payload.openai_reasoning,
        max_tokens=payload.max_tokens,
        web_search_tool="web_search_20250305",
        capability_map=str(CAPABILITY_MAP),
        out_dir=str(OUTPUT_DIR),
        dry_run=payload.dry_run,
        print_json=False,
    )
    try:
        account_map = generator.generate_account_map(args)
        generation_log(
            "model_complete",
            provider=payload.provider,
            target=payload.target,
            elapsed=round(time.monotonic() - started, 1),
        )
        enrich_account_map_with_diagrams(
            account_map,
            renderer=payload.diagram_renderer,
            openai_api_key=diagram_openai_key,
            image_quality=payload.diagram_image_quality,
        )
        json_path, md_path = generator.write_outputs(account_map, args.target, OUTPUT_DIR)
        result = {
            "summary": summarize_map(json_path),
            "account_map": account_map,
            "json_path": str(json_path),
            "markdown_path": str(md_path),
        }
        generation_log(
            "generation_completed",
            provider=payload.provider,
            target=payload.target,
            map_id=result["summary"].get("id"),
            elapsed=round(time.monotonic() - started, 1),
        )
        return result
    except BaseException as exc:
        generation_log(
            "generation_failed",
            provider=payload.provider,
            target=payload.target,
            elapsed=round(time.monotonic() - started, 1),
            error=str(exc) or repr(exc),
        )
        logger.exception("account_map_generation_failed")
        raise


def export_deck_for_map(map_id: str) -> dict[str, Any]:
    json_path, _ = paths_for_id(map_id)
    md_path = OUTPUT_DIR / f"{map_id}.md"
    data = read_json(json_path)
    if enrich_account_map_with_diagrams(data):
        write_account_map_files(data, json_path, md_path)
    if EXPORT_API_URL:
        return export_deck_via_api(map_id, data, json_path)
    if not NODE_BIN.exists():
        raise RuntimeError(f"Node runtime not found: {NODE_BIN}")
    if not TEMPLATE_PPTX.exists():
        raise RuntimeError(f"Presentation template not found: {TEMPLATE_PPTX}")
    if not DECK_SCRIPT.exists():
        raise RuntimeError(f"Deck export script not found: {DECK_SCRIPT}")

    DECK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    deck_path = DECK_OUTPUT_DIR / f"{map_id}-account-map.pptx"
    command = [
        str(NODE_BIN),
        str(DECK_SCRIPT),
        "--input",
        str(json_path),
        "--output",
        str(deck_path),
        "--template",
        str(TEMPLATE_PPTX),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "Deck export failed")

    return {
        "summary": summarize_map(json_path),
        "deck_url": f"/api/decks/{deck_path.name}",
        "deck_path": str(deck_path),
    }


def export_deck_via_api(map_id: str, data: dict[str, Any], json_path: Path) -> dict[str, Any]:
    payload = {
        "content": data,
        "options": {
            "filename_prefix": f"{map_id}-account-map",
        },
    }
    request = urllib_request.Request(
        f"{EXPORT_API_URL}/api/exports/account-map-deck",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=180) as response:
            export_result = json.loads(response.read().decode("utf-8"))
        file_url = export_result.get("file_url")
        if not isinstance(file_url, str) or not file_url:
            raise RuntimeError("Export API response did not include file_url")
        download_url = f"{EXPORT_API_URL}{file_url}" if file_url.startswith("/") else file_url
        with urllib_request.urlopen(download_url, timeout=180) as response:
            deck_bytes = response.read()
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Export API failed: {detail or exc}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"Export API unavailable: {exc}") from exc

    DECK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    deck_path = DECK_OUTPUT_DIR / f"{map_id}-account-map.pptx"
    deck_path.write_bytes(deck_bytes)
    return {
        "summary": summarize_map(json_path),
        "deck_url": f"/api/decks/{deck_path.name}",
        "deck_path": str(deck_path),
        "export_api": {
            "url": EXPORT_API_URL,
            "export_id": export_result.get("export_id"),
            "file_url": export_result.get("file_url"),
        },
    }


def create_diagram(payload: dict[str, Any]) -> dict[str, Any]:
    artifact = diagram_generator.generate_diagram(payload)
    json_path, svg_path = diagram_generator.write_diagram(artifact, DIAGRAM_OUTPUT_DIR)
    return {
        "id": artifact.diagram_id,
        "spec": artifact.spec,
        "svg": artifact.svg,
        "json_url": f"/api/diagrams/{json_path.name}",
        "svg_url": f"/api/diagrams/{svg_path.name}",
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "capability_map": str(CAPABILITY_MAP),
        "capability_map_exists": CAPABILITY_MAP.exists(),
        "outputs": str(OUTPUT_DIR),
        "model": generator.DEFAULT_MODEL,
        "default_provider": generator.DEFAULT_PROVIDER,
        "anthropic_model": generator.DEFAULT_ANTHROPIC_MODEL,
        "openai_model": generator.DEFAULT_OPENAI_MODEL,
        "openai_reasoning": generator.DEFAULT_OPENAI_REASONING,
        "openai_image_model": DEFAULT_IMAGE_MODEL,
        "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "image_diagram_references": str(REFERENCE_DIAGRAM_DIR),
        "image_diagram_references_exist": REFERENCE_DIAGRAM_DIR.exists(),
        "deck_export_configured": bool(EXPORT_API_URL) or (NODE_BIN.exists() and TEMPLATE_PPTX.exists()),
        "export_api_configured": bool(EXPORT_API_URL),
        "export_api_url": EXPORT_API_URL,
        "template": str(TEMPLATE_PPTX),
    }


@app.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    data = read_json(CAPABILITY_MAP)
    products = data.get("products") or []
    return {
        "product_count": len(products),
        "products": [
            {
                "slug": product.get("slug"),
                "product": product.get("product"),
                "family": product.get("family"),
                "confidence": product.get("confidence"),
                "capabilities": product.get("capabilities") or [],
                "best_fit_use_cases": product.get("best_fit_use_cases") or [],
            }
            for product in products
        ],
    }


@app.get("/api/account-maps")
def list_account_maps() -> dict[str, Any]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(OUTPUT_DIR.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return {"items": [summarize_map(path) for path in files]}


@app.post("/api/account-maps/jobs")
def create_account_map_job(payload: GenerateRequest) -> dict[str, Any]:
    if payload.provider == "openai" and not payload.openai_api_key:
        raise HTTPException(status_code=400, detail="Enter your OpenAI API key before generating.")
    if payload.provider == "anthropic" and not payload.anthropic_api_key:
        raise HTTPException(status_code=400, detail="Enter your Anthropic API key before generating.")
    if payload.diagram_renderer == "gpt_image" and not payload.dry_run and not (payload.diagram_openai_api_key or payload.openai_api_key):
        raise HTTPException(status_code=400, detail="Enter an OpenAI API key for GPT Image diagrams.")
    prune_generation_jobs()
    job_id = uuid4().hex
    now = time.time()
    with generation_jobs_lock:
        generation_jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "message": "Queued",
            "provider": payload.provider,
            "target": payload.target,
            "use_cases": payload.use_cases,
            "dry_run": payload.dry_run,
            "created_at": now,
            "updated_at": now,
        }
        generation_jobs[job_id]["future"] = generation_executor.submit(run_generation_job, job_id, payload)
    generation_log("job_created", job_id=job_id, provider=payload.provider, target=payload.target, use_cases=payload.use_cases)
    return generation_job_snapshot(job_id)


@app.get("/api/account-maps/jobs/{job_id}")
def get_account_map_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=404, detail="Generation job not found")
    return generation_job_snapshot(job_id)


@app.get("/api/account-maps/{map_id}")
def get_account_map(map_id: str) -> dict[str, Any]:
    json_path, md_path = paths_for_id(map_id)
    data = read_json(json_path)
    if enrich_account_map_with_diagrams(data):
        write_account_map_files(data, json_path, md_path)
    return {"summary": summarize_map(json_path), "account_map": data}


@app.get("/api/account-maps/{map_id}/markdown")
def get_account_map_markdown(map_id: str) -> FileResponse:
    _, md_path = paths_for_id(map_id)
    if not md_path.exists():
        raise HTTPException(status_code=404, detail="Markdown not found")
    return FileResponse(md_path, media_type="text/markdown")


@app.post("/api/account-maps/{map_id}/deck")
async def export_account_map_deck(map_id: str) -> dict[str, Any]:
    try:
        return await run_in_threadpool(export_deck_for_map, map_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/decks/{filename}")
def get_deck(filename: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.pptx", filename):
        raise HTTPException(status_code=404, detail="Deck not found")
    deck_path = DECK_OUTPUT_DIR / filename
    if not deck_path.exists():
        raise HTTPException(status_code=404, detail="Deck not found")
    return FileResponse(
        deck_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=filename,
    )


@app.post("/api/diagrams")
async def generate_diagram(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    try:
        return await run_in_threadpool(create_diagram, payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/diagrams/{filename}")
def get_diagram(filename: str) -> FileResponse:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(svg|json)", filename):
        raise HTTPException(status_code=404, detail="Diagram not found")
    path = DIAGRAM_OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Diagram not found")
    if filename.endswith(".svg"):
        return FileResponse(path, media_type="image/svg+xml")
    return FileResponse(path, media_type="application/json")


@app.get("/api/image-diagrams/{filename}")
def get_image_diagram(filename: str) -> FileResponse:
    if not safe_image_filename(filename):
        raise HTTPException(status_code=404, detail="Image diagram not found")
    path = IMAGE_DIAGRAM_OUTPUT_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image diagram not found")
    if filename.endswith(".json"):
        return FileResponse(path, media_type="application/json")
    media_type = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@app.post("/api/account-maps")
async def generate(payload: GenerateRequest) -> dict[str, Any]:
    try:
        return await run_in_threadpool(run_generation, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

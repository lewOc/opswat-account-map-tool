#!/usr/bin/env python3
"""FastAPI wrapper for the OPSWAT account-map tool."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool


PROJECT = Path(__file__).resolve().parent
UI_DIR = PROJECT / "ui"
OUTPUT_DIR = Path(os.environ.get("ACCOUNT_MAP_OUTPUT_DIR", PROJECT / "outputs" / "account_maps"))
DECK_OUTPUT_DIR = Path(os.environ.get("DECK_OUTPUT_DIR", PROJECT / "outputs" / "decks"))
DIAGRAM_OUTPUT_DIR = Path(os.environ.get("DIAGRAM_OUTPUT_DIR", PROJECT / "outputs" / "diagrams"))
CAPABILITY_MAP = Path(os.environ.get("CAPABILITY_MAP_PATH", PROJECT / "data" / "capability_map.json"))
ACCOUNT_MAP_SCRIPT = PROJECT / "scripts" / "account_map.py"
DECK_SCRIPT = PROJECT / "scripts" / "export_deck.mjs"
DIAGRAM_SCRIPT = PROJECT / "scripts" / "diagram_generator.py"
NODE_BIN = Path(os.environ.get("NODE_BIN") or shutil.which("node") or "node")
TEMPLATE_PPTX = Path(os.environ.get("PRESENTATION_TEMPLATE_PATH", PROJECT / "templates" / "presentation_template.pptx"))


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


class GenerateRequest(BaseModel):
    target: str = Field(..., min_length=2, max_length=200)
    focus: str = Field(default="", max_length=600)
    use_cases: int = Field(default=5, ge=1, le=8)
    provider: str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    model: Optional[str] = None
    anthropic_api_key: Optional[str] = Field(default=None, max_length=300)
    openai_api_key: Optional[str] = Field(default=None, max_length=300)
    openai_reasoning: str = Field(default="medium", pattern="^(low|medium|high)$")
    max_tokens: int = Field(default=9000, ge=2000, le=20000)
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
    diagram_count = sum(1 for use_case in use_cases if (use_case.get("diagram") or {}).get("svg_url"))
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
    if not isinstance(svg_url, str) or not svg_url:
        return False
    filename = Path(svg_url).name
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]+\.svg", filename)) and (DIAGRAM_OUTPUT_DIR / filename).exists()


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


def enrich_account_map_with_diagrams(account_map: dict[str, Any]) -> int:
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
            artifact = diagram_generator.generate_diagram(diagram_payload_for_use_case(account_map, use_case, index))
            json_path, svg_path = diagram_generator.write_diagram(artifact, DIAGRAM_OUTPUT_DIR)
            use_case["diagram"] = {
                "id": artifact.diagram_id,
                "title": artifact.spec.get("title"),
                "pattern": artifact.spec.get("pattern"),
                "svg_url": f"/api/diagrams/{svg_path.name}",
                "json_url": f"/api/diagrams/{json_path.name}",
            }
            generated += 1
        except Exception as exc:
            use_case["diagram"] = {
                "error": str(exc),
            }

    account_map.setdefault("_meta", {})
    account_map["_meta"]["diagram_generation"] = {
        "generated_count": sum(1 for use_case in use_cases if (use_case.get("diagram") or {}).get("svg_url")),
        "generator": "opswat-2023-light-flow",
    }
    return generated


def write_account_map_files(account_map: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.write_text(json.dumps(account_map, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(generator.account_map_to_markdown(account_map), encoding="utf-8")


def run_generation(payload: GenerateRequest) -> dict[str, Any]:
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
    account_map = generator.generate_account_map(args)
    enrich_account_map_with_diagrams(account_map)
    json_path, md_path = generator.write_outputs(account_map, args.target, OUTPUT_DIR)
    return {
        "summary": summarize_map(json_path),
        "account_map": account_map,
        "json_path": str(json_path),
        "markdown_path": str(md_path),
    }


def export_deck_for_map(map_id: str) -> dict[str, Any]:
    json_path, _ = paths_for_id(map_id)
    md_path = OUTPUT_DIR / f"{map_id}.md"
    data = read_json(json_path)
    if enrich_account_map_with_diagrams(data):
        write_account_map_files(data, json_path, md_path)
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
        "anthropic_configured": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai_configured": bool(os.environ.get("OPENAI_API_KEY")),
        "deck_export_configured": NODE_BIN.exists() and TEMPLATE_PPTX.exists(),
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


@app.post("/api/account-maps")
async def generate(payload: GenerateRequest) -> dict[str, Any]:
    try:
        return await run_in_threadpool(run_generation, payload)
    except SystemExit as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

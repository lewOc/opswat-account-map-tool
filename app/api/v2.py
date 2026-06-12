"""Versioned API surface for the v2 account-map pipeline."""

from __future__ import annotations

import asyncio
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.config import Settings, load_settings
from app.models.account_map import AccountMap
from app.services.capability_map import CapabilityMap
from app.services.pipeline.orchestrator import PipelineRequest, run_pipeline
from app.services.providers import AnthropicProvider, LLMProvider, OpenAIProvider
from app.services.retrieval import customer_story_retriever_from_settings


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CAPABILITY_MAP_PATH = PROJECT_ROOT / "data" / "capability_map.json"
V2_JOB_RETENTION_SECONDS = int(os.environ.get("V2_GENERATION_JOB_RETENTION_SECONDS", "7200"))
V2_GENERATION_WORKERS = int(os.environ.get("V2_GENERATION_WORKERS", "2"))

router = APIRouter(prefix="/api/v2", tags=["v2"])
generation_executor = ThreadPoolExecutor(max_workers=V2_GENERATION_WORKERS)
jobs: dict[str, dict[str, Any]] = {}
jobs_lock = Lock()


class V2GenerateRequest(BaseModel):
    target: str = Field(..., min_length=2, max_length=200)
    focus: str = Field(default="", max_length=600)
    use_cases: int = Field(default=2, ge=1, le=8)
    provider: str = Field(default="anthropic", pattern="^(anthropic|openai)$")
    model: Optional[str] = None
    fast_model: Optional[str] = None
    max_tokens: int = Field(default=30000, ge=2000, le=30000)


def resolve_project_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def artifact_dir_from_settings(settings: Settings) -> Path:
    return resolve_project_path(settings.artifact_dir)


def capability_map_path() -> Path:
    configured = os.environ.get("CAPABILITY_MAP_PATH")
    return resolve_project_path(Path(configured)) if configured else DEFAULT_CAPABILITY_MAP_PATH


def safe_map_id(map_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", map_id))


def account_map_path(map_id: str, settings: Optional[Settings] = None) -> Path:
    if not safe_map_id(map_id):
        raise HTTPException(status_code=404, detail="Account map not found")
    settings = settings or load_settings()
    return artifact_dir_from_settings(settings) / map_id / "account_map.json"


def read_account_map_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account map not found")
    return AccountMap.model_validate_json(path.read_text(encoding="utf-8")).model_dump(mode="json")


def summarize_account_map_payload(payload: dict[str, Any], path: Optional[Path] = None) -> dict[str, Any]:
    target = payload.get("target_account") or {}
    use_cases = payload.get("recommended_use_cases") or []
    evidence = payload.get("research_evidence") or []
    meta = payload.get("meta") or {}
    map_id = str(payload.get("id") or (path.parent.name if path else "account-map"))
    retrieval = meta.get("retrieval") if isinstance(meta, dict) else {}
    return {
        "id": map_id,
        "schema_version": meta.get("schema_version", 2) if isinstance(meta, dict) else 2,
        "target_name": target.get("name") or map_id,
        "sector": target.get("sector"),
        "generated_at": meta.get("generated_at") if isinstance(meta, dict) else None,
        "target_input": meta.get("target_input") if isinstance(meta, dict) else None,
        "provider": meta.get("provider") if isinstance(meta, dict) else None,
        "model": meta.get("model") if isinstance(meta, dict) else None,
        "use_case_count": len(use_cases),
        "diagram_count": 0,
        "evidence_count": len(evidence),
        "retrieval_count": len(retrieval) if isinstance(retrieval, dict) else 0,
        "json_url": f"/api/v2/account-maps/{map_id}/artifact",
        "markdown_url": None,
        "deck_url": None,
    }


def response_for_account_map(payload: dict[str, Any], path: Optional[Path] = None) -> dict[str, Any]:
    return {
        "summary": summarize_account_map_payload(payload, path),
        "account_map": payload,
    }


def list_account_map_paths(settings: Settings) -> list[Path]:
    artifact_dir = artifact_dir_from_settings(settings)
    if not artifact_dir.exists():
        return []
    paths = [path for path in artifact_dir.glob("*/account_map.json") if path.is_file()]
    return sorted(paths, key=lambda path: path.stat().st_mtime, reverse=True)


def prune_jobs() -> None:
    cutoff = time.time() - V2_JOB_RETENTION_SECONDS
    with jobs_lock:
        expired = [
            job_id
            for job_id, job in jobs.items()
            if job.get("updated_at", job.get("created_at", 0)) < cutoff and job.get("status") in {"completed", "failed"}
        ]
        for job_id in expired:
            jobs.pop(job_id, None)


def update_job(job_id: str, **fields: Any) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return
        job.update(fields)
        job["updated_at"] = time.time()


def job_snapshot(job_id: str) -> dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Generation job not found")
        return {key: value for key, value in job.items() if key != "future"}


def build_provider(payload: V2GenerateRequest, settings: Settings) -> tuple[LLMProvider, str, Optional[str]]:
    if payload.provider == "anthropic":
        api_key = settings.provider_api_key("anthropic")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for v2 Anthropic generation.")
        return (
            AnthropicProvider(
                api_key=api_key,
                web_search_tool=settings.anthropic_web_search_tool,
                timeout_s=settings.model_timeout_s,
            ),
            payload.model or settings.anthropic_model,
            payload.fast_model or settings.anthropic_fast_model,
        )
    if payload.provider == "openai":
        api_key = settings.provider_api_key("openai")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for v2 OpenAI generation.")
        return (
            OpenAIProvider(
                api_key=api_key,
                reasoning_effort=settings.openai_reasoning_effort,
                timeout_s=settings.model_timeout_s,
            ),
            payload.model or settings.openai_model,
            None,
        )
    raise ValueError(f"Unsupported provider: {payload.provider}")


def run_generation_job(job_id: str, payload: V2GenerateRequest) -> None:
    started = time.monotonic()
    try:
        settings = load_settings()
        provider, model, fast_model = build_provider(payload, settings)
        capability_map = CapabilityMap.from_path(capability_map_path())
        artifact_dir = artifact_dir_from_settings(settings)

        def progress(stage: str, pct: float, message: str) -> None:
            update_job(job_id, status="running", stage=stage, progress=pct, message=message)

        update_job(
            job_id,
            status="running",
            stage="queued",
            progress=0.0,
            message="Preparing v2 generation",
            provider=payload.provider,
            model=model,
        )
        request = PipelineRequest(
            target=payload.target,
            focus=payload.focus,
            use_cases=payload.use_cases,
            provider=payload.provider,
            model=model,
            fast_model=fast_model,
            max_tokens=payload.max_tokens,
            narrative_concurrency=settings.narrative_concurrency,
            artifact_dir=artifact_dir,
        )
        result = asyncio.run(
            run_pipeline(
                request,
                provider,
                capability_map,
                progress=progress,
                customer_story_retriever=customer_story_retriever_from_settings(settings),
            )
        )
        account_map_payload = result.account_map.model_dump(mode="json")
        artifact_path = artifact_dir / result.account_map.id / "account_map.json"
        update_job(
            job_id,
            status="completed",
            stage="complete",
            progress=1.0,
            message="Complete",
            result={**response_for_account_map(account_map_payload, artifact_path), "warnings": list(result.warnings)},
            map_id=result.account_map.id,
            artifact_path=str(artifact_path),
            elapsed_s=round(time.monotonic() - started, 1),
        )
    except BaseException as exc:
        update_job(
            job_id,
            status="failed",
            message=str(exc) or repr(exc),
            error=str(exc) or repr(exc),
            elapsed_s=round(time.monotonic() - started, 1),
        )


def validate_generation_config(payload: V2GenerateRequest, settings: Settings) -> None:
    if payload.provider == "anthropic" and settings.provider_api_key("anthropic") is None:
        raise HTTPException(status_code=400, detail="Server is missing ANTHROPIC_API_KEY.")
    if payload.provider == "openai" and settings.provider_api_key("openai") is None:
        raise HTTPException(status_code=400, detail="Server is missing OPENAI_API_KEY.")


@router.get("/health")
def health() -> dict[str, Any]:
    settings = load_settings()
    public = settings.public_safe_dict()
    return {
        **public,
        "ok": True,
        "version": "v2",
        "capability_map": str(capability_map_path()),
        "capability_map_exists": capability_map_path().exists(),
        "artifact_dir": str(artifact_dir_from_settings(settings)),
        "pinecone_configured": settings.pinecone_api_key is not None,
        "customer_story_retrieval_configured": settings.openai_api_key is not None and settings.pinecone_api_key is not None,
    }


@router.get("/account-maps")
def list_account_maps() -> dict[str, Any]:
    settings = load_settings()
    items = [
        summarize_account_map_payload(read_account_map_payload(path), path)
        for path in list_account_map_paths(settings)
    ]
    return {"items": items}


@router.post("/account-maps/jobs")
def create_account_map_job(payload: V2GenerateRequest) -> dict[str, Any]:
    settings = load_settings()
    validate_generation_config(payload, settings)
    prune_jobs()
    job_id = uuid4().hex
    now = time.time()
    with jobs_lock:
        jobs[job_id] = {
            "id": job_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0.0,
            "message": "Queued",
            "provider": payload.provider,
            "target": payload.target,
            "use_cases": payload.use_cases,
            "created_at": now,
            "updated_at": now,
        }
        jobs[job_id]["future"] = generation_executor.submit(run_generation_job, job_id, payload)
    return job_snapshot(job_id)


@router.get("/account-maps/jobs/{job_id}")
def get_account_map_job(job_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(status_code=404, detail="Generation job not found")
    return job_snapshot(job_id)


@router.get("/account-maps/{map_id}")
def get_account_map(map_id: str) -> dict[str, Any]:
    path = account_map_path(map_id)
    return response_for_account_map(read_account_map_payload(path), path)


@router.get("/account-maps/{map_id}/artifact")
def get_account_map_artifact(map_id: str) -> FileResponse:
    path = account_map_path(map_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Account map not found")
    return FileResponse(path, media_type="application/json", filename=f"{map_id}.json")

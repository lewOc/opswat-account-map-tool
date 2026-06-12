"""Run the v2 no-diagram account-map pipeline."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.config import Settings, load_settings
from app.services.capability_map import CapabilityMap
from app.services.pipeline.orchestrator import PipelineRequest, run_pipeline
from app.services.providers import AnthropicProvider, FakeProvider, LLMProvider, OpenAIProvider
from app.services.retrieval import customer_story_retriever_from_settings


def load_provider(args: argparse.Namespace, settings: Settings) -> LLMProvider:
    if args.fixture:
        data = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise SystemExit("Fake provider fixture must contain a JSON object")
        return FakeProvider(data)
    if args.provider == "anthropic":
        api_key = settings.provider_api_key("anthropic")
        if not api_key:
            raise SystemExit("ANTHROPIC_API_KEY is required for --provider anthropic")
        return AnthropicProvider(
            api_key=api_key,
            web_search_tool=settings.anthropic_web_search_tool,
            timeout_s=settings.model_timeout_s,
        )
    if args.provider == "openai":
        api_key = settings.provider_api_key("openai")
        if not api_key:
            raise SystemExit("OPENAI_API_KEY is required for --provider openai")
        return OpenAIProvider(
            api_key=api_key,
            reasoning_effort=settings.openai_reasoning_effort,
            timeout_s=settings.model_timeout_s,
        )
    raise SystemExit(f"Unsupported provider: {args.provider}")


def progress(stage: str, pct: float, message: str) -> None:
    print(f"{pct:>5.0%} {stage}: {message}")


async def async_main(args: argparse.Namespace) -> int:
    settings = load_settings()
    provider = load_provider(args, settings)
    capability_map = CapabilityMap.from_path(args.capability_map)
    model = args.model or (settings.openai_model if args.provider == "openai" else settings.anthropic_model)
    fast_model = args.fast_model or (None if args.provider == "openai" else settings.anthropic_fast_model)
    request = PipelineRequest(
        target=args.target,
        focus=args.focus,
        use_cases=args.use_cases,
        provider=args.provider,
        model=model,
        fast_model=fast_model,
        max_tokens=args.max_tokens,
        narrative_concurrency=settings.narrative_concurrency,
        artifact_dir=args.artifact_dir,
    )
    result = await run_pipeline(
        request,
        provider,
        capability_map,
        progress=progress,
        customer_story_retriever=customer_story_retriever_from_settings(settings),
    )
    print(result.account_map.model_dump_json(indent=2))
    if result.warnings:
        print("Warnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a v2 OPSWAT account map.")
    parser.add_argument("target", help="Target company name, domain, or URL.")
    parser.add_argument("--focus", default="", help="Optional sales focus.")
    parser.add_argument("--use-cases", type=int, default=5)
    parser.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    parser.add_argument("--model", default=None)
    parser.add_argument("--fast-model", default=None)
    parser.add_argument("--max-tokens", type=int, default=30000)
    parser.add_argument("--capability-map", type=Path, default=Path("data/capability_map.json"))
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument("--fixture", type=Path, default=None, help="Fixture JSON for FakeProvider.")
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

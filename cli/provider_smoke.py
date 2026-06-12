"""Smoke-test a v2 LLM provider structured call."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from pydantic import BaseModel

from app.config import Settings, load_settings
from app.services.providers import AnthropicProvider, FakeProvider, LLMProvider, OpenAIProvider


class SmokeResponse(BaseModel):
    summary: str
    confidence: str


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


async def async_main(args: argparse.Namespace) -> int:
    settings = load_settings()
    provider = load_provider(args, settings)
    model = args.model or (settings.openai_model if args.provider == "openai" else settings.anthropic_fast_model)
    result, raw = await provider.structured(
        system="Return concise, source-grounded structured data only.",
        prompt=args.prompt,
        schema=SmokeResponse,
        model=model,
        max_tokens=1000,
        web_search=args.web_search,
        stage="provider_smoke",
    )
    print(result.model_dump_json(indent=2))
    print(
        json.dumps(
            {
                "provider": raw.provider,
                "model": raw.model,
                "request_hash": raw.request_hash,
                "latency_s": raw.latency_s,
                "tokens_in": raw.tokens_in,
                "tokens_out": raw.tokens_out,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a v2 LLM provider structured call.")
    parser.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    parser.add_argument("--model", default=None)
    parser.add_argument("--web-search", action="store_true")
    parser.add_argument("--fixture", type=Path, default=None)
    parser.add_argument(
        "--prompt",
        default="Summarize OPSWAT in one sentence and assign confidence high, medium, or low.",
    )
    return asyncio.run(async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

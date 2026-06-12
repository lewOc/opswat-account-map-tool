"""LLM provider boundary for v2 staged generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol, TypeVar

from pydantic import BaseModel

from app.observability import redact_secrets


ModelT = TypeVar("ModelT", bound=BaseModel)


class ProviderError(RuntimeError):
    """Raised when a provider cannot return schema-valid structured output."""


@dataclass(frozen=True)
class RawCall:
    provider: str
    model: str
    request_hash: str
    response_text: str
    latency_s: float
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    ok: bool = True
    error: Optional[str] = None


class LLMProvider(Protocol):
    name: str

    async def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[ModelT],
        model: str,
        max_tokens: int,
        web_search: bool = False,
        temperature: Optional[float] = None,
        stage: Optional[str] = None,
    ) -> tuple[ModelT, RawCall]:
        """Return schema-valid structured output and redacted call metadata."""


def request_hash(*, provider: str, model: str, system: str, prompt: str, schema_name: str) -> str:
    payload = json.dumps(
        {
            "provider": provider,
            "model": model,
            "system": system,
            "prompt": prompt,
            "schema": schema_name,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def schema_tool_name(schema: type[BaseModel]) -> str:
    return f"write_{schema.__name__.lower()}"


def compact_json_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Return a provider-friendly JSON schema.

    Pydantic's schema is the source of truth, but titles/defaults are noisy and
    occasionally trip provider-side schema validators. Keep the actual
    structural contract and strip descriptive clutter.
    """

    return strip_schema_metadata(schema.model_json_schema())


def strip_schema_metadata(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: strip_schema_metadata(item)
            for key, item in value.items()
            if key not in {"title", "default", "examples"}
        }
    if isinstance(value, list):
        return [strip_schema_metadata(item) for item in value]
    return value


def final_tool_instruction(tool_name: str) -> str:
    return (
        f"\n\nWhen ready, call the `{tool_name}` tool with the final structured response. "
        "Do not return prose outside the tool input."
    )


def build_anthropic_request(
    *,
    model: str,
    max_tokens: int,
    system: str,
    prompt: str,
    schema: type[BaseModel],
    web_search: bool,
    web_search_tool: str,
    temperature: Optional[float],
) -> dict[str, Any]:
    tool_name = schema_tool_name(schema)
    tools: list[dict[str, Any]] = []
    if web_search:
        tools.append({"type": web_search_tool, "name": "web_search"})
    tools.append(
        {
            "name": tool_name,
            "description": f"Return the final {schema.__name__} object.",
            "input_schema": compact_json_schema(schema),
        }
    )
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system,
        "tools": tools,
        "messages": [{"role": "user", "content": prompt + final_tool_instruction(tool_name)}],
    }
    # If web search is enabled, Claude needs freedom to call web_search before
    # calling the final schema tool. For non-search calls, force the schema tool.
    if not web_search:
        request["tool_choice"] = {"type": "tool", "name": tool_name}
    if temperature is not None:
        request["temperature"] = temperature
    return request


def build_openai_request(
    *,
    model: str,
    max_tokens: int,
    system: str,
    prompt: str,
    schema: type[BaseModel],
    web_search: bool,
    reasoning_effort: str,
    temperature: Optional[float],
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": schema.__name__,
                "schema": compact_json_schema(schema),
                "strict": False,
            }
        },
        "max_output_tokens": max_tokens,
    }
    if web_search:
        request["tools"] = [{"type": "web_search_preview"}]
    if reasoning_effort:
        request["reasoning"] = {"effort": reasoning_effort}
    if temperature is not None:
        request["temperature"] = temperature
    return request


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        cleaned = cleaned.removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        parsed = json.loads(cleaned[start : end + 1])
    if not isinstance(parsed, dict):
        raise ProviderError("Provider returned JSON that was not an object")
    return parsed


def _anthropic_usage(response: Any) -> tuple[Optional[int], Optional[int]]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


def _openai_usage(response: Any) -> tuple[Optional[int], Optional[int]]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None, None
    return getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None)


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        web_search_tool: str = "web_search_20250305",
        timeout_s: int = 600,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._web_search_tool = web_search_tool
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    async def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[ModelT],
        model: str,
        max_tokens: int,
        web_search: bool = False,
        temperature: Optional[float] = None,
        stage: Optional[str] = None,
    ) -> tuple[ModelT, RawCall]:
        return await asyncio.to_thread(
            self._structured_sync,
            system=system,
            prompt=prompt,
            schema=schema,
            model=model,
            max_tokens=max_tokens,
            web_search=web_search,
            temperature=temperature,
            stage=stage,
        )

    def _structured_sync(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[ModelT],
        model: str,
        max_tokens: int,
        web_search: bool,
        temperature: Optional[float],
        stage: Optional[str],
    ) -> tuple[ModelT, RawCall]:
        del stage
        started = time.monotonic()
        req_hash = request_hash(
            provider=self.name,
            model=model,
            system=system,
            prompt=prompt,
            schema_name=schema.__name__,
        )
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError("Missing anthropic package") from exc

        tool_name = schema_tool_name(schema)
        request = build_anthropic_request(
            model=model,
            max_tokens=max_tokens,
            system=system,
            prompt=prompt,
            schema=schema,
            web_search=web_search,
            web_search_tool=self._web_search_tool,
            temperature=temperature,
        )

        client = anthropic.Anthropic(api_key=self._api_key, timeout=self._timeout_s, max_retries=self._max_retries)
        try:
            response = client.messages.create(**request)
            text_parts = [getattr(block, "text", "") for block in getattr(response, "content", []) or []]
            raw_text = "".join(text_parts).strip()
            payload: Optional[dict[str, Any]] = None
            for block in getattr(response, "content", []) or []:
                if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == tool_name:
                    candidate = getattr(block, "input", None)
                    if isinstance(candidate, dict):
                        payload = candidate
                        break
            if payload is None:
                payload = parse_json_object(raw_text)
            parsed = schema.model_validate(payload)
            tokens_in, tokens_out = _anthropic_usage(response)
            return parsed, RawCall(
                provider=self.name,
                model=model,
                request_hash=req_hash,
                response_text=redact_secrets(raw_text or json.dumps(payload, sort_keys=True)),
                latency_s=round(time.monotonic() - started, 4),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception as exc:
            error = redact_secrets(str(exc))
            raise ProviderError(error) from exc


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        reasoning_effort: str = "medium",
        timeout_s: int = 600,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._reasoning_effort = reasoning_effort
        self._timeout_s = timeout_s
        self._max_retries = max_retries

    async def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[ModelT],
        model: str,
        max_tokens: int,
        web_search: bool = False,
        temperature: Optional[float] = None,
        stage: Optional[str] = None,
    ) -> tuple[ModelT, RawCall]:
        return await asyncio.to_thread(
            self._structured_sync,
            system=system,
            prompt=prompt,
            schema=schema,
            model=model,
            max_tokens=max_tokens,
            web_search=web_search,
            temperature=temperature,
            stage=stage,
        )

    def _structured_sync(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[ModelT],
        model: str,
        max_tokens: int,
        web_search: bool,
        temperature: Optional[float],
        stage: Optional[str],
    ) -> tuple[ModelT, RawCall]:
        del stage
        started = time.monotonic()
        req_hash = request_hash(
            provider=self.name,
            model=model,
            system=system,
            prompt=prompt,
            schema_name=schema.__name__,
        )
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("Missing openai package") from exc

        request = build_openai_request(
            model=model,
            max_tokens=max_tokens,
            system=system,
            prompt=prompt,
            schema=schema,
            web_search=web_search,
            reasoning_effort=self._reasoning_effort,
            temperature=temperature,
        )

        client = OpenAI(api_key=self._api_key, timeout=self._timeout_s, max_retries=self._max_retries)
        try:
            response = client.responses.create(**request)
            raw_text = getattr(response, "output_text", "") or ""
            payload = parse_json_object(raw_text)
            parsed = schema.model_validate(payload)
            tokens_in, tokens_out = _openai_usage(response)
            return parsed, RawCall(
                provider=self.name,
                model=model,
                request_hash=req_hash,
                response_text=redact_secrets(raw_text),
                latency_s=round(time.monotonic() - started, 4),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
            )
        except Exception as exc:
            error = redact_secrets(str(exc))
            raise ProviderError(error) from exc


class FakeProvider:
    """Fixture-backed provider for tests and replay evals."""

    name = "fake"

    def __init__(self, fixtures: Mapping[str, Any]) -> None:
        self._fixtures = fixtures

    async def structured(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[ModelT],
        model: str,
        max_tokens: int,
        web_search: bool = False,
        temperature: Optional[float] = None,
        stage: Optional[str] = None,
    ) -> tuple[ModelT, RawCall]:
        del web_search, temperature, max_tokens
        key = stage or schema.__name__
        if key not in self._fixtures:
            raise ProviderError(f"FakeProvider fixture not found for key: {key}")
        payload = self._fixtures[key]
        if isinstance(payload, BaseModel):
            payload_dict = payload.model_dump(mode="json")
        elif isinstance(payload, dict):
            payload_dict = payload
        else:
            raise ProviderError(f"FakeProvider fixture for {key} must be a dict or BaseModel")
        parsed = schema.model_validate(payload_dict)
        raw_text = json.dumps(payload_dict, sort_keys=True)
        return parsed, RawCall(
            provider=self.name,
            model=model,
            request_hash=request_hash(
                provider=self.name,
                model=model,
                system=system,
                prompt=prompt,
                schema_name=schema.__name__,
            ),
            response_text=redact_secrets(raw_text),
            latency_s=0.0,
        )

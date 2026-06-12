"""Customer-story retrieval for v2 account-map generation.

Retrieval is helpful grounding context, not a hard dependency. This module
therefore returns empty, metadata-rich results when keys, SDKs, or remote
services are unavailable.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Optional, Protocol, Union

from pydantic import Field

from app.config import Settings
from app.models.account_map import Confidence, DeliveryExperience, Provenance, StrictModel


class CustomerStoryExample(StrictModel):
    title: str
    customer_type: str = "Customer story"
    anonymous: bool = False
    products: list[str] = Field(default_factory=list)
    relevance: str
    outcome: str = ""
    source_url: str = ""
    source_urls: list[str] = Field(default_factory=list)
    retrieval_score: Optional[float] = None
    confidence: Confidence = Confidence.medium
    provenance: Provenance = Provenance.retrieved

    def to_delivery_experience(self) -> DeliveryExperience:
        return DeliveryExperience(
            title=self.title,
            customer_type=self.customer_type,
            anonymous=self.anonymous,
            products=self.products,
            relevance=self.relevance,
            outcome=self.outcome,
            source_url=self.source_url,
            retrieval_score=self.retrieval_score,
            provenance=self.provenance,
        )


class RetrievalResult(StrictModel):
    query: str
    examples: list[CustomerStoryExample] = Field(default_factory=list)
    configured: bool = False
    disabled: bool = False
    index: str = ""
    namespace: str = ""
    error: Optional[str] = None

    def summary(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "configured": self.configured,
            "disabled": self.disabled,
            "index": self.index,
            "namespace": self.namespace,
            "example_count": len(self.examples),
            "top_scores": [
                example.retrieval_score
                for example in self.examples[:3]
                if example.retrieval_score is not None
            ],
            "error": self.error,
        }


class CustomerStoryRetriever(Protocol):
    async def search(self, query: str, top_k: int = 5) -> RetrievalResult:
        """Return relevant customer-story examples for a query."""


class NullCustomerStoryRetriever:
    def __init__(
        self,
        *,
        reason: str = "customer-story retrieval is not configured",
        index: str = "",
        namespace: str = "",
        disabled: bool = False,
    ) -> None:
        self._reason = reason
        self._index = index
        self._namespace = namespace
        self._disabled = disabled

    async def search(self, query: str, top_k: int = 5) -> RetrievalResult:
        del top_k
        return RetrievalResult(
            query=query,
            configured=False,
            disabled=self._disabled,
            index=self._index,
            namespace=self._namespace,
            error=self._reason,
        )


class FakeCustomerStoryRetriever:
    def __init__(self, examples: list[Union[CustomerStoryExample, Mapping[str, Any]]]) -> None:
        self.examples = [
            item if isinstance(item, CustomerStoryExample) else CustomerStoryExample.model_validate(item)
            for item in examples
        ]
        self.queries: list[str] = []

    async def search(self, query: str, top_k: int = 5) -> RetrievalResult:
        self.queries.append(query)
        return RetrievalResult(
            query=query,
            examples=self.examples[:top_k],
            configured=True,
            index="fake-customer-stories",
            namespace="test",
        )


class PineconeCustomerStoryRetriever:
    def __init__(
        self,
        *,
        openai_api_key: str,
        pinecone_api_key: str,
        index: str,
        namespace: str,
        embed_model: str,
    ) -> None:
        self._openai_api_key = openai_api_key
        self._pinecone_api_key = pinecone_api_key
        self._index = index
        self._namespace = namespace
        self._embed_model = embed_model

    async def search(self, query: str, top_k: int = 5) -> RetrievalResult:
        if not query.strip():
            return RetrievalResult(
                query=query,
                configured=True,
                index=self._index,
                namespace=self._namespace,
                error="empty retrieval query",
            )
        try:
            return await asyncio.to_thread(self._search_sync, query, top_k)
        except Exception as exc:  # pragma: no cover - defensive remote boundary
            return RetrievalResult(
                query=query,
                configured=True,
                index=self._index,
                namespace=self._namespace,
                error=f"{exc.__class__.__name__}: {exc}",
            )

    def _search_sync(self, query: str, top_k: int) -> RetrievalResult:
        try:
            from openai import OpenAI
        except ImportError as exc:
            return self._unavailable(query, f"openai SDK unavailable: {exc}")
        try:
            from pinecone import Pinecone
        except ImportError as exc:
            return self._unavailable(query, f"pinecone SDK unavailable: {exc}")

        openai_client = OpenAI(api_key=self._openai_api_key)
        embedding_response = openai_client.embeddings.create(model=self._embed_model, input=query)
        embedding = embedding_response.data[0].embedding

        pinecone = Pinecone(api_key=self._pinecone_api_key)
        index = pinecone.Index(self._index)
        response = index.query(
            vector=embedding,
            top_k=top_k,
            include_metadata=True,
            namespace=self._namespace,
        )
        examples = [example_from_match(match) for match in response_matches(response)]
        return RetrievalResult(
            query=query,
            examples=[example for example in examples if example is not None],
            configured=True,
            index=self._index,
            namespace=self._namespace,
        )

    def _unavailable(self, query: str, error: str) -> RetrievalResult:
        return RetrievalResult(
            query=query,
            configured=True,
            index=self._index,
            namespace=self._namespace,
            error=error,
        )


def customer_story_retriever_from_settings(settings: Settings) -> CustomerStoryRetriever:
    if settings.customer_story_rag_disabled:
        return NullCustomerStoryRetriever(
            reason="customer-story retrieval disabled by CUSTOMER_STORY_RAG_DISABLED",
            index=settings.customer_story_index,
            namespace=settings.customer_story_namespace,
            disabled=True,
        )
    openai_api_key = secret_value(settings.openai_api_key)
    pinecone_api_key = secret_value(settings.pinecone_api_key)
    if not openai_api_key or not pinecone_api_key:
        return NullCustomerStoryRetriever(
            reason="OPENAI_API_KEY and PINECONE_API_KEY are required for customer-story retrieval",
            index=settings.customer_story_index,
            namespace=settings.customer_story_namespace,
        )
    return PineconeCustomerStoryRetriever(
        openai_api_key=openai_api_key,
        pinecone_api_key=pinecone_api_key,
        index=settings.customer_story_index,
        namespace=settings.customer_story_namespace,
        embed_model=settings.customer_story_embed_model,
    )


def secret_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "get_secret_value"):
        return value.get_secret_value()
    return str(value)


def compact_customer_story_context(
    examples: list[CustomerStoryExample],
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    return [
        {
            "title": example.title,
            "customer_type": example.customer_type,
            "anonymous": example.anonymous,
            "products": example.products,
            "relevance": trim_text(example.relevance, 320),
            "outcome": trim_text(example.outcome, 220),
            "source_url": example.source_url,
            "confidence": example.confidence.value,
            "retrieval_score": example.retrieval_score,
        }
        for example in examples[:limit]
    ]


def delivery_experience_from_examples(
    examples: list[CustomerStoryExample],
    *,
    limit: int = 3,
) -> list[DeliveryExperience]:
    return [example.to_delivery_experience() for example in examples[:limit]]


def customer_story_query(
    *,
    target: str,
    focus: str = "",
    use_case: Optional[Any] = None,
    product_names: Optional[list[str]] = None,
    limit: int = 900,
) -> str:
    parts = [target, focus, "OPSWAT customer story relevant experience"]
    if use_case is not None:
        parts.extend(
            [
                string_attr(use_case, "title"),
                string_attr(use_case, "account_trigger"),
                string_attr(use_case, "problem"),
            ]
        )
    if product_names:
        parts.extend(product_names)
    return trim_text(" ".join(part for part in parts if part).strip(), limit)


def example_from_match(match: Any) -> Optional[CustomerStoryExample]:
    metadata = match_metadata(match)
    if not metadata:
        return None
    score = match_score(match)
    urls = list_from_metadata(metadata.get("urls") or metadata.get("source_urls"))
    source_url = str(metadata.get("source_url") or metadata.get("url") or (urls[0] if urls else "") or "")
    products = list_from_metadata(metadata.get("products_used") or metadata.get("products"))
    industry_hints = list_from_metadata(metadata.get("industry_hints"))
    text = str(
        metadata.get("text")
        or metadata.get("chunk_text")
        or metadata.get("snippet")
        or metadata.get("summary")
        or ""
    )
    title = str(metadata.get("title") or metadata.get("customer") or "Customer story example")
    customer_type = str(metadata.get("customer_type") or ", ".join(industry_hints) or "Customer story")
    return CustomerStoryExample(
        title=trim_text(title, 160),
        customer_type=trim_text(customer_type, 120),
        anonymous=bool(metadata.get("anonymous", False)),
        products=products,
        relevance=trim_text(text, 650),
        outcome=trim_text(str(metadata.get("outcome") or metadata.get("business_outcome") or ""), 360),
        source_url=source_url,
        source_urls=urls,
        retrieval_score=score,
        confidence=confidence_from_score(score),
    )


def response_matches(response: Any) -> list[Any]:
    if isinstance(response, Mapping):
        matches = response.get("matches") or []
    else:
        matches = getattr(response, "matches", [])
    return list(matches or [])


def match_metadata(match: Any) -> Mapping[str, Any]:
    if isinstance(match, Mapping):
        metadata = match.get("metadata") or {}
    else:
        metadata = getattr(match, "metadata", {}) or {}
    return metadata if isinstance(metadata, Mapping) else {}


def match_score(match: Any) -> Optional[float]:
    score = match.get("score") if isinstance(match, Mapping) else getattr(match, "score", None)
    if score is None:
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def confidence_from_score(score: Optional[float]) -> Confidence:
    if score is None:
        return Confidence.medium
    if score >= 0.55:
        return Confidence.high
    if score >= 0.35:
        return Confidence.medium
    return Confidence.low


def list_from_metadata(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        parsed = parse_json_list(value)
        if parsed is not None:
            return [str(item).strip() for item in parsed if str(item).strip()]
        for separator in ("|", ";", ","):
            if separator in value:
                return [item.strip() for item in value.split(separator) if item.strip()]
        return [value.strip()] if value.strip() else []
    return [str(value).strip()] if str(value).strip() else []


def parse_json_list(value: str) -> Optional[list[Any]]:
    stripped = value.strip()
    if not stripped.startswith("["):
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, list) else None


def trim_text(value: str, limit: int) -> str:
    cleaned = " ".join(str(value).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "..."


def string_attr(value: Any, attr: str) -> str:
    return str(getattr(value, attr, "") or "").strip()

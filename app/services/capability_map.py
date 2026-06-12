"""Capability-map registry for v2 grounding and prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable, Optional


DEFAULT_CAPABILITY_MAP_PATH = Path("data/capability_map.json")


@dataclass(frozen=True)
class CapabilityProduct:
    slug: str
    product: str
    family: str
    capabilities: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]
    raw: dict[str, Any]

    @property
    def capability_set(self) -> set[str]:
        return set(self.capabilities)


class CapabilityMap:
    def __init__(self, data: dict[str, Any]) -> None:
        products = data.get("products")
        if not isinstance(products, list) or not products:
            raise ValueError("Capability map must contain a non-empty products list")
        self._data = data
        self._products = tuple(self._parse_product(product) for product in products)

    @classmethod
    def from_path(cls, path: Path = DEFAULT_CAPABILITY_MAP_PATH) -> "CapabilityMap":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    @staticmethod
    def _parse_product(raw: Any) -> CapabilityProduct:
        if not isinstance(raw, dict):
            raise ValueError("Capability-map product entries must be objects")
        slug = str(raw.get("slug") or "").strip()
        product = str(raw.get("product") or "").strip()
        if not slug or not product:
            raise ValueError("Capability-map products must include slug and product")
        return CapabilityProduct(
            slug=slug,
            product=product,
            family=str(raw.get("family") or ""),
            capabilities=tuple(str(item) for item in raw.get("capabilities") or [] if item),
            evidence=tuple(item for item in raw.get("evidence") or [] if isinstance(item, dict)),
            raw=raw,
        )

    @cached_property
    def by_slug(self) -> dict[str, CapabilityProduct]:
        return {product.slug: product for product in self._products}

    @property
    def products(self) -> tuple[CapabilityProduct, ...]:
        return self._products

    def get(self, slug: str) -> Optional[CapabilityProduct]:
        return self.by_slug.get(slug)

    def require(self, slug: str) -> CapabilityProduct:
        product = self.get(slug)
        if product is None:
            raise KeyError(f"Unknown OPSWAT product slug: {slug}")
        return product

    def valid_slugs(self) -> set[str]:
        return set(self.by_slug)

    def compact(self, max_evidence: int = 5) -> dict[str, Any]:
        """Keep prompt size down while preserving source-backed product fit."""

        compact_products = []
        for product in self._products:
            raw = product.raw
            compact_products.append(
                {
                    "slug": product.slug,
                    "product": product.product,
                    "family": product.family,
                    "confidence": raw.get("confidence"),
                    "what_it_protects": raw.get("what_it_protects"),
                    "deployment_zones": raw.get("deployment_zones", []),
                    "best_fit_use_cases": raw.get("best_fit_use_cases", []),
                    "buyer_problems": raw.get("buyer_problems", []),
                    "threat_paths": raw.get("threat_paths", []),
                    "capabilities": list(product.capabilities),
                    "protocols_and_integrations": raw.get("protocols_and_integrations", []),
                    "industries": raw.get("industries", []),
                    "compliance_drivers": raw.get("compliance_drivers", []),
                    "account_triggers": raw.get("account_triggers", []),
                    "evidence": [
                        {
                            "title": evidence.get("title"),
                            "category": evidence.get("category"),
                            "source_path": evidence.get("source_path"),
                            "snippet": evidence.get("snippet"),
                        }
                        for evidence in product.evidence[:max_evidence]
                    ],
                }
            )
        return {"products": compact_products}

    def evidence_refs_for_product(self, slug: str, capabilities_used: Iterable[str], limit: int = 4) -> list[str]:
        product = self.get(slug)
        if product is None:
            return []
        capability_names = set(capabilities_used)
        refs: list[str] = []
        for evidence in product.evidence:
            matched = set(evidence.get("matched_capabilities") or [])
            if capability_names and matched and not (matched & capability_names):
                continue
            source_path = evidence.get("source_path")
            if source_path and source_path not in refs:
                refs.append(str(source_path))
        if not refs:
            for evidence in product.evidence:
                source_path = evidence.get("source_path")
                if source_path and source_path not in refs:
                    refs.append(str(source_path))
                if len(refs) >= limit:
                    break
        return refs[:limit]

    def evidence_ref_set_for_product(self, slug: str) -> set[str]:
        product = self.get(slug)
        if product is None:
            return set()
        return {str(evidence.get("source_path")) for evidence in product.evidence if evidence.get("source_path")}

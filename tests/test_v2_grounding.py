from __future__ import annotations

import unittest

from app.models.account_map import AccountMap
from app.models.legacy import from_v1
from app.services.capability_map import CapabilityMap
from app.services.grounding import ground_account_map
from tests.test_v2_legacy_import import v1_fixture


def capability_fixture() -> CapabilityMap:
    return CapabilityMap(
        {
            "products": [
                {
                    "slug": "mdcore",
                    "product": "MetaDefender Core",
                    "family": "MetaDefender Platform",
                    "confidence": "high",
                    "what_it_protects": "Files entering critical workflows.",
                    "capabilities": ["Multiscanning", "Deep CDR"],
                    "evidence": [
                        {
                            "title": "Core multiscanning",
                            "category": "operating",
                            "source_path": "docs/mdcore/multiscanning.md",
                            "snippet": "Core supports multiscanning.",
                            "matched_capabilities": ["Multiscanning"],
                        },
                        {
                            "title": "Core Deep CDR",
                            "category": "operating",
                            "source_path": "docs/mdcore/deep-cdr.md",
                            "snippet": "Core supports Deep CDR.",
                            "matched_capabilities": ["Deep CDR"],
                        },
                    ],
                },
                {
                    "slug": "mdmft",
                    "product": "MetaDefender Managed File Transfer",
                    "family": "MetaDefender Platform",
                    "capabilities": ["Managed File Transfer"],
                    "evidence": [],
                },
            ]
        }
    )


class V2GroundingTests(unittest.TestCase):
    def test_capability_map_compacts_prompt_payload(self) -> None:
        compact = capability_fixture().compact(max_evidence=1)

        self.assertEqual(len(compact["products"]), 2)
        self.assertEqual(compact["products"][0]["slug"], "mdcore")
        self.assertEqual(len(compact["products"][0]["evidence"]), 1)
        self.assertNotIn("raw", compact["products"][0])

    def test_grounding_canonicalizes_product_and_fills_evidence_refs(self) -> None:
        payload = from_v1(v1_fixture(), map_id="example-energy-map").model_dump()
        payload["recommended_use_cases"][0]["opswat_products"][0]["product"] = "Wrong name from model"
        payload["recommended_use_cases"][0]["opswat_products"][0]["capability_evidence_refs"] = []
        account_map = AccountMap.model_validate(payload)

        report = ground_account_map(account_map, capability_fixture())
        product = report.account_map.recommended_use_cases[0].opswat_products[0]

        self.assertEqual(product.product, "MetaDefender Core")
        self.assertEqual(product.capability_evidence_refs, ["docs/mdcore/multiscanning.md", "docs/mdcore/deep-cdr.md"])
        self.assertEqual(report.invalid_product_count, 0)

    def test_grounding_replaces_capability_refs_outside_registry(self) -> None:
        payload = from_v1(v1_fixture(), map_id="example-energy-map").model_dump()
        payload["recommended_use_cases"][0]["opswat_products"][0]["capability_evidence_refs"] = [
            "/Users/lewis/Documents/opswat_docs_full/core.md"
        ]
        account_map = AccountMap.model_validate(payload)

        report = ground_account_map(account_map, capability_fixture())
        product = report.account_map.recommended_use_cases[0].opswat_products[0]

        self.assertEqual(product.capability_evidence_refs, ["docs/mdcore/multiscanning.md", "docs/mdcore/deep-cdr.md"])
        self.assertIn("replaced capability evidence refs", " ".join(report.warnings))

    def test_grounding_warns_on_capabilities_outside_registry(self) -> None:
        payload = from_v1(v1_fixture(), map_id="example-energy-map").model_dump()
        payload["recommended_use_cases"][0]["opswat_products"][0]["capabilities_used"].append("Imaginary Capability")
        payload["recommended_use_cases"][0]["opswat_products"][0]["capability_evidence_refs"] = []
        account_map = AccountMap.model_validate(payload)

        report = ground_account_map(account_map, capability_fixture())

        self.assertEqual(report.invalid_capability_count, 1)
        self.assertIn("Imaginary Capability", " ".join(report.warnings))
        self.assertIn("grounding", report.account_map.meta.retrieval)

    def test_grounding_drops_use_case_when_all_product_slugs_are_invalid(self) -> None:
        payload = from_v1(v1_fixture(), map_id="example-energy-map").model_dump()
        payload["recommended_use_cases"][0]["opswat_products"][0]["slug"] = "not-a-real-product"
        account_map = AccountMap.model_validate(payload)

        report = ground_account_map(account_map, capability_fixture())

        self.assertEqual(report.invalid_product_count, 1)
        self.assertEqual(report.dropped_use_case_ids, ("uc-1",))
        self.assertEqual(report.account_map.recommended_use_cases, [])
        self.assertIn("uc-1.opswat_products", report.account_map.gaps)
        self.assertIn("uc-1.opswat_products.not-a-real-product", report.account_map.gaps)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.models.account_map import AccountMap
from app.models.legacy import LegacyConversionError, from_v1


def v1_fixture() -> dict:
    return {
        "_meta": {
            "generated_at": "2026-06-05T09:57:16.297981+00:00",
            "provider": "anthropic",
            "model": "claude-opus-4-8",
            "target_input": "Example Energy",
        },
        "target_account": {
            "name": "Example Energy",
            "website": "https://example.com",
            "sector": "Energy",
            "summary": "A regional energy utility with public operational resilience priorities.",
        },
        "research_evidence": [
            {
                "claim": "Example Energy operates regulated energy infrastructure.",
                "source_title": "Example Energy Annual Report",
                "source_url": "https://example.com/annual-report",
                "confidence": "high",
            },
            {
                "claim": "Example Energy works with third-party engineering partners.",
                "source_title": "Example Energy Partner Page",
                "source_url": "https://example.com/partners",
                "confidence": "medium",
            },
        ],
        "account_signals": [
            {
                "signal": "Regulated operational infrastructure",
                "why_it_matters": "OT file ingress needs traceable controls.",
                "evidence_refs": ["https://example.com/annual-report"],
                "confidence": "high",
            }
        ],
        "recommended_use_cases": [
            {
                "rank": 1,
                "use_case": "Secure supplier file ingress into OT",
                "signal_link": "Third-party engineering files enter sensitive environments.",
                "product_fit": "Use OPSWAT controls to inspect engineering files.",
                "opswat_products": [
                    {
                        "product": "MetaDefender Core",
                        "slug": "mdcore",
                        "fit_reason": "Central file analysis for untrusted engineering content.",
                        "capabilities_used": ["Multiscanning", "Deep CDR"],
                        "confidence": "high",
                        "capability_evidence_refs": ["opswat_docs_full/core.md"],
                    }
                ],
                "discovery_questions": ["How do suppliers submit engineering files today?"],
                "evidence_refs": ["https://example.com/annual-report", "opswat_docs_full/core.md"],
                "confidence": "high",
            }
        ],
        "buyer_map": [
            {
                "role": "OT Security Lead",
                "talking_points": ["Auditability", "Supplier risk"],
                "why_relevant": "Owns the control process.",
            }
        ],
        "outreach": {
            "opening_angle": "Discuss supplier file controls for Example Energy.",
            "email_subjects": ["Supplier file controls for Example Energy"],
            "first_call_agenda": ["Map current file-ingress paths"],
        },
        "assumptions_and_gaps": [
            {
                "item": "Exact transfer tooling is unknown.",
                "how_to_validate": "Ask the OT security team.",
            }
        ],
    }


class V2LegacyImportTests(unittest.TestCase):
    def test_from_v1_converts_aliases_and_assigns_ids(self) -> None:
        account_map = from_v1(v1_fixture(), map_id="example-energy-map")

        self.assertIsInstance(account_map, AccountMap)
        self.assertEqual(account_map.id, "example-energy-map")
        self.assertEqual(account_map.research_evidence[0].id, "ev-001")
        self.assertEqual(account_map.account_signals[0].evidence_ids, ["ev-001"])
        self.assertEqual(account_map.recommended_use_cases[0].id, "uc-1")
        self.assertEqual(account_map.recommended_use_cases[0].title, "Secure supplier file ingress into OT")
        self.assertEqual(account_map.recommended_use_cases[0].account_trigger, "Third-party engineering files enter sensitive environments.")
        self.assertEqual(account_map.recommended_use_cases[0].evidence_ids, ["ev-001"])
        self.assertEqual(account_map.recommended_use_cases[0].opswat_products[0].slug, "mdcore")
        self.assertEqual(account_map.buyer_map[0].persona, "OT Security Lead")
        self.assertEqual(account_map.meta.schema_version, 2)

    def test_from_v1_records_missing_sections_as_gaps_without_boilerplate(self) -> None:
        account_map = from_v1(v1_fixture(), map_id="example-energy-map")
        use_case = account_map.recommended_use_cases[0]

        self.assertIn("uc-1.problem_narrative", account_map.gaps)
        self.assertIn("uc-1.implementation_flow", account_map.gaps)
        self.assertEqual(use_case.problem_narrative, "")
        self.assertEqual(use_case.implementation_flow, [])
        self.assertNotIn("Route files, media, or traffic through the mapped OPSWAT control point", account_map.model_dump_json())

    def test_from_v1_rejects_dry_run_prompt_preview(self) -> None:
        with self.assertRaises(LegacyConversionError):
            from_v1({"dry_run": True, "prompt_preview": "Target account: SSE"})

    def test_account_map_rejects_unknown_evidence_reference(self) -> None:
        account_map = from_v1(v1_fixture(), map_id="example-energy-map")
        payload = account_map.model_dump()
        payload["recommended_use_cases"][0]["evidence_ids"] = ["ev-999"]

        with self.assertRaises(ValidationError):
            AccountMap.model_validate(payload)


if __name__ == "__main__":
    unittest.main()

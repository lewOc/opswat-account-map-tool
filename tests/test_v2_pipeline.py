from __future__ import annotations

import asyncio
import copy
import tempfile
import unittest
from pathlib import Path

from app.services.capability_map import CapabilityMap
from app.services.pipeline.orchestrator import PipelineRequest, run_pipeline
from app.services.providers import FakeProvider
from app.services.retrieval import FakeCustomerStoryRetriever


def capability_fixture() -> CapabilityMap:
    return CapabilityMap(
        {
            "products": [
                {
                    "slug": "mdcore",
                    "product": "MetaDefender Core",
                    "family": "MetaDefender Platform",
                    "capabilities": ["Multiscanning", "Deep CDR"],
                    "evidence": [
                        {
                            "title": "Core multiscanning",
                            "category": "operating",
                            "source_path": "docs/mdcore/multiscanning.md",
                            "snippet": "Core supports multiscanning.",
                            "matched_capabilities": ["Multiscanning"],
                        }
                    ],
                }
            ]
        }
    )


def fake_fixtures() -> dict:
    return {
        "research": {
            "target_account": {
                "name": "Example Energy",
                "website": "https://example.com",
                "sector": "Energy",
                "summary": "Example Energy operates regulated energy infrastructure.",
            },
            "research_evidence": [
                {
                    "claim": "Example Energy operates regulated energy infrastructure.",
                    "source_title": "Example Energy Annual Report",
                    "source_url": "https://example.com/annual-report",
                    "confidence": "high",
                }
            ],
        },
        "signals": {
            "account_signals": [
                {
                    "signal": "Regulated operational infrastructure",
                    "why_it_matters": "Untrusted files entering OT need inspection.",
                    "evidence_ids": ["ev-001"],
                    "confidence": "high",
                },
                {
                    "signal": "This bad signal will be dropped",
                    "why_it_matters": "It has no valid evidence.",
                    "evidence_ids": ["ev-999"],
                    "confidence": "medium",
                },
            ]
        },
        "select": {
            "use_cases": [
                {
                    "id": "uc-1",
                    "rank": 1,
                    "title": "Secure supplier file ingress into OT",
                    "account_trigger": "Supplier engineering files enter sensitive workflows.",
                    "problem": "Files may carry malware into regulated operational environments.",
                    "signal_ids": ["sig-001"],
                    "product_slugs": ["mdcore", "unknown-product"],
                    "confidence": "high",
                }
            ]
        },
        "narrative_uc-1": {
            "problem_narrative": "Supplier files can create malware and integrity risk at the OT boundary.",
            "solution_narrative": "",
            "business_value": "Reduce file-borne risk while preserving evidence for audit.",
            "business_value_narrative": "This gives security and OT teams a controlled path for approving supplier content.",
            "conversation_starter": "Where do supplier files cross into operational environments today?",
            "implementation_flow": ["Receive file", "Scan with MetaDefender Core", "Release or block"],
            "stakeholders": ["OT Security Lead", "Infrastructure Lead"],
            "discovery_questions": ["Which workflows accept supplier files today?"],
            "inferences": ["Inference: supplier workflows should be validated in discovery."],
            "deployment_hypothesis": "Inference: deploy at the file ingress boundary.",
            "opswat_products": [
                {
                    "slug": "mdcore",
                    "fit_reason": "Core provides file multiscanning and sanitization for ingress workflows.",
                    "capabilities_used": ["Multiscanning"],
                    "confidence": "high",
                }
            ],
        },
        "repair_narrative_uc-1": {
            "solution_narrative": "Route supplier files through MetaDefender Core for multiscanning before they are released to operational users."
        },
        "assemble": {
            "buyer_map": [
                {
                    "persona": "OT Security Lead",
                    "likely_concerns": ["Auditability", "Operational disruption"],
                    "message_angle": "Create a controlled evidence-backed path for supplier files.",
                }
            ],
            "outreach": {
                "opening_angle": "Discuss supplier file ingress controls for Example Energy.",
                "email_subjects": ["Supplier file controls for Example Energy"],
                "first_call_agenda": ["Map supplier file workflows"],
            },
            "assumptions_and_gaps": [{"item": "Exact file-transfer tooling is unknown.", "how_to_validate": "Ask in discovery."}],
        },
    }


class V2PipelineTests(unittest.TestCase):
    def test_fake_provider_pipeline_builds_grounded_account_map(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = asyncio.run(
                run_pipeline(
                    PipelineRequest(
                        target="Example Energy",
                        focus="OT file ingress",
                        use_cases=1,
                        provider="fake",
                        model="fake-model",
                        artifact_dir=Path(tmp),
                    ),
                    FakeProvider(fake_fixtures()),
                    capability_fixture(),
                    customer_story_retriever=FakeCustomerStoryRetriever(
                        [
                            {
                                "title": "Utility supplier-file scanning rollout",
                                "customer_type": "Energy",
                                "anonymous": True,
                                "products": ["MetaDefender Core"],
                                "relevance": "A regulated utility inspected supplier engineering files before release.",
                                "outcome": "Created an auditable file ingress path.",
                                "source_url": "https://opswat.example/customer-story",
                                "retrieval_score": 0.82,
                                "confidence": "high",
                            }
                        ]
                    ),
                )
            )

            account_map = result.account_map
            self.assertEqual(account_map.target_account.name, "Example Energy")
            self.assertEqual(len(account_map.research_evidence), 1)
            self.assertEqual(len(account_map.account_signals), 1)
            self.assertEqual(len(account_map.recommended_use_cases), 1)
            self.assertEqual(account_map.recommended_use_cases[0].opswat_products[0].product, "MetaDefender Core")
            self.assertEqual(account_map.recommended_use_cases[0].opswat_products[0].capability_evidence_refs, ["docs/mdcore/multiscanning.md"])
            self.assertIn("uc-1.product_slugs.unknown-product", account_map.gaps)
            self.assertNotIn("uc-1.solution_narrative", account_map.gaps)
            self.assertIn("sig-002.evidence_ids", account_map.gaps)
            self.assertEqual(account_map.recommended_use_cases[0].meta.provenance.value, "repaired")
            self.assertEqual(len(account_map.recommended_use_cases[0].delivery_experience), 1)
            self.assertEqual(
                account_map.recommended_use_cases[0].delivery_experience[0].title,
                "Utility supplier-file scanning rollout",
            )
            self.assertIn("select", account_map.meta.retrieval)
            self.assertIn("narrative_uc-1", account_map.meta.retrieval)
            self.assertEqual(account_map.meta.retrieval["narrative_uc-1"]["example_count"], 1)
            self.assertIn("solution_narrative", account_map.recommended_use_cases[0].meta.notes)
            self.assertIn("research", result.raw_calls)
            self.assertIn("repair_narrative_uc-1", result.raw_calls)
            self.assertIn("assemble", result.raw_calls)
            self.assertTrue((Path(tmp) / account_map.id / "account_map.json").exists())
            self.assertTrue((Path(tmp) / account_map.id / "runs" / "research.json").exists())

    def test_pipeline_keeps_gap_when_repair_cannot_fill_missing_field(self) -> None:
        fixtures = copy.deepcopy(fake_fixtures())
        fixtures["repair_narrative_uc-1"] = {}

        result = asyncio.run(
            run_pipeline(
                PipelineRequest(
                    target="Example Energy",
                    focus="OT file ingress",
                    use_cases=1,
                    provider="fake",
                    model="fake-model",
                ),
                FakeProvider(fixtures),
                capability_fixture(),
            )
        )

        account_map = result.account_map
        self.assertIn("uc-1.solution_narrative", account_map.gaps)
        self.assertEqual(account_map.recommended_use_cases[0].solution_narrative, "")
        self.assertEqual(account_map.recommended_use_cases[0].meta.provenance.value, "researched")
        self.assertIn("repair_narrative_uc-1", result.raw_calls)

    def test_pipeline_fills_empty_use_case_title_with_gap(self) -> None:
        fixtures = copy.deepcopy(fake_fixtures())
        fixtures["select"]["use_cases"][0]["title"] = ""

        result = asyncio.run(
            run_pipeline(
                PipelineRequest(
                    target="Example Energy",
                    focus="OT file ingress",
                    use_cases=1,
                    provider="fake",
                    model="fake-model",
                ),
                FakeProvider(fixtures),
                capability_fixture(),
            )
        )

        account_map = result.account_map
        self.assertEqual(
            account_map.recommended_use_cases[0].title,
            "Files may carry malware into regulated operational environments.",
        )
        self.assertIn("uc-1.title", account_map.gaps)


if __name__ == "__main__":
    unittest.main()

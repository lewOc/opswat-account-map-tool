from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfReader

from app.models.account_map import AccountMap
from app.services.pdf_export import export_account_map_pdf, use_case_display_title


def account_map_fixture() -> AccountMap:
    return AccountMap.model_validate(
        {
            "id": "example-energy-20260612T120000Z",
            "target_account": {
                "name": "Example Energy",
                "website": "https://example.com",
                "sector": "Energy",
                "country": "United Kingdom",
                "summary": "Example Energy operates regulated power infrastructure and needs safe file movement into OT.",
            },
            "research_evidence": [
                {
                    "id": "ev-001",
                    "claim": "Example Energy operates regulated critical infrastructure.",
                    "source_title": "Example Energy Annual Report",
                    "source_url": "https://example.com/annual-report",
                    "confidence": "high",
                }
            ],
            "account_signals": [
                {
                    "id": "sig-001",
                    "signal": "Supplier engineering files cross the IT/OT boundary.",
                    "why_it_matters": "Untrusted files need malware inspection and sanitization before they reach operational users.",
                    "evidence_ids": ["ev-001"],
                    "confidence": "high",
                }
            ],
            "recommended_use_cases": [
                {
                    "id": "uc-001",
                    "rank": 1,
                    "title": "Secure supplier file ingress into OT",
                    "account_trigger": "Contractor engineering documents need to move into operational sites.",
                    "problem": "Files can introduce malware or policy violations into sensitive environments.",
                    "problem_narrative": "Supplier and contractor files create a repeatable ingress risk at the OT boundary.",
                    "solution_narrative": "Route inbound files through MetaDefender Core for multiscanning and Deep CDR before release.",
                    "business_value": "Reduce file-borne risk while preserving audit evidence.",
                    "business_value_narrative": "Security and OT teams get a controlled path for approving content without slowing delivery.",
                    "conversation_starter": "Where do supplier files cross into operations today?",
                    "implementation_flow": [
                        "Receive supplier file",
                        "Inspect and sanitize with MetaDefender Core",
                        "Release approved content to the operational workflow",
                    ],
                    "stakeholders": ["OT Security Lead", "Infrastructure Manager"],
                    "discovery_questions": ["Which workflows accept supplier files today?"],
                    "inferences": ["Supplier file movement should be validated in discovery."],
                    "deployment_hypothesis": "Deploy at the file ingress boundary.",
                    "opswat_products": [
                        {
                            "slug": "mdcore",
                            "product": "MetaDefender Core",
                            "fit_reason": "Provides multiscanning and Deep CDR for inbound content.",
                            "capabilities_used": ["Multiscanning", "Deep CDR"],
                            "confidence": "high",
                            "capability_evidence_refs": ["docs/mdcore/multiscanning.md"],
                        }
                    ],
                    "delivery_experience": [
                        {
                            "title": "Utility supplier-file scanning rollout",
                            "customer_type": "Energy utility",
                            "anonymous": True,
                            "products": ["MetaDefender Core"],
                            "relevance": "A regulated utility inspected supplier files before release.",
                            "outcome": "Created an auditable file ingress path.",
                            "source_url": "https://opswat.example/customer-story",
                            "retrieval_score": 0.82,
                            "provenance": "retrieved",
                        }
                    ],
                    "evidence_ids": ["ev-001"],
                    "confidence": "high",
                }
            ],
            "buyer_map": [
                {
                    "persona": "OT Security Lead",
                    "likely_concerns": ["Operational disruption", "Auditability"],
                    "message_angle": "Create an evidence-backed path for supplier files.",
                }
            ],
            "outreach": {
                "opening_angle": "Discuss practical supplier file controls for Example Energy.",
                "email_subjects": ["Supplier file controls for Example Energy"],
                "first_call_agenda": ["Map supplier file workflows", "Validate OT boundary controls"],
            },
            "assumptions_and_gaps": [
                {"item": "Exact file-transfer tooling is unknown.", "how_to_validate": "Ask during discovery."}
            ],
            "gaps": [],
            "meta": {
                "schema_version": 2,
                "provider": "fake",
                "model": "fake-model",
                "generated_at": "2026-06-12T12:00:00Z",
                "target_input": "Example Energy",
                "focus": "OT file ingress",
                "requested_use_cases": 1,
            },
        }
    )


class V2PdfExportTests(unittest.TestCase):
    def test_use_case_title_falls_back_to_problem_for_older_maps(self) -> None:
        use_case = account_map_fixture().recommended_use_cases[0].model_copy(update={"title": ""})

        self.assertEqual(
            use_case_display_title(use_case),
            "Secure supplier and partner file exchange",
        )

    def test_exports_customer_ready_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp) / "account_map.pdf"

            export_account_map_pdf(account_map_fixture(), pdf_path)

            self.assertTrue(pdf_path.exists())
            self.assertTrue(pdf_path.read_bytes().startswith(b"%PDF"))
            reader = PdfReader(str(pdf_path))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
            normalized = " ".join(text.split())
            self.assertIn("OPSWAT ACCOUNT MAP", normalized)
            self.assertIn("How OPSWAT can help Example Energy", normalized)
            self.assertIn("Secure supplier file ingress into OT", normalized)
            self.assertIn("MetaDefender Core", normalized)
            self.assertIn("Evidence Appendix", normalized)


if __name__ == "__main__":
    unittest.main()

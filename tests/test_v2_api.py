from __future__ import annotations

import unittest
from pathlib import Path

from app.api.v2 import (
    V2GenerateRequest,
    artifact_dir_from_settings,
    safe_map_id,
    summarize_account_map_payload,
    validate_generation_config,
)
from app.config import Settings


class V2ApiTests(unittest.TestCase):
    def test_summarize_v2_account_map_payload(self) -> None:
        payload = {
            "id": "example-energy-20260612T120000Z",
            "target_account": {
                "name": "Example Energy",
                "sector": "Energy",
            },
            "research_evidence": [{"id": "ev-001"}],
            "recommended_use_cases": [{"id": "uc-1"}, {"id": "uc-2"}],
            "meta": {
                "schema_version": 2,
                "provider": "anthropic",
                "model": "claude-opus-4-8",
                "generated_at": "2026-06-12T12:00:00Z",
                "target_input": "Example Energy",
                "retrieval": {"select": {}, "narrative_uc-1": {}},
            },
        }

        summary = summarize_account_map_payload(payload)

        self.assertEqual(summary["id"], "example-energy-20260612T120000Z")
        self.assertEqual(summary["target_name"], "Example Energy")
        self.assertEqual(summary["use_case_count"], 2)
        self.assertEqual(summary["evidence_count"], 1)
        self.assertEqual(summary["retrieval_count"], 2)
        self.assertEqual(summary["json_url"], "/api/v2/account-maps/example-energy-20260612T120000Z/artifact")
        self.assertEqual(summary["pdf_url"], "/api/v2/account-maps/example-energy-20260612T120000Z/pdf")
        self.assertIsNone(summary["deck_url"])

    def test_safe_map_id_rejects_path_traversal(self) -> None:
        self.assertTrue(safe_map_id("example-energy-20260612T120000Z"))
        self.assertFalse(safe_map_id("../secret"))
        self.assertFalse(safe_map_id(""))

    def test_artifact_dir_resolves_relative_to_project_root(self) -> None:
        settings = Settings(artifact_dir=Path("var/artifacts"))
        path = artifact_dir_from_settings(settings)

        self.assertTrue(path.is_absolute())
        self.assertTrue(str(path).endswith("account-map-tool/var/artifacts"))

    def test_generation_config_requires_server_side_provider_key(self) -> None:
        with self.assertRaises(Exception):
            validate_generation_config(V2GenerateRequest(target="Example Energy"), Settings())

        validate_generation_config(
            V2GenerateRequest(target="Example Energy"),
            Settings.from_env({"ANTHROPIC_API_KEY": "sk-ant-test"}),
        )


if __name__ == "__main__":
    unittest.main()

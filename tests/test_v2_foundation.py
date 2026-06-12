from __future__ import annotations

import asyncio
import json
import unittest

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.observability import SecretRedactionFilter, redact_secrets
from app.services.pipeline.schemas import NarrativeOutput, UseCaseSkeleton
from app.services.prompts import PromptTemplate
from app.services.providers import FakeProvider, build_anthropic_request, build_openai_request, compact_json_schema


class ExampleResponse(BaseModel):
    name: str
    count: int


class V2FoundationTests(unittest.TestCase):
    def test_settings_keep_provider_keys_server_side(self) -> None:
        settings = Settings.from_env(
            {
                "APP_ENV": "dev",
                "ANTHROPIC_API_KEY": "sk-ant-testsecret",
                "OPENAI_API_KEY": "sk-testsecret",
                "PINECONE_API_KEY": "pcsk_testsecret",
            }
        )

        public = settings.public_safe_dict()

        self.assertTrue(public["anthropic_configured"])
        self.assertTrue(public["openai_configured"])
        self.assertNotIn("sk-testsecret", str(public))
        self.assertEqual(settings.provider_api_key("anthropic"), "sk-ant-testsecret")

    def test_prod_requires_secret_key(self) -> None:
        with self.assertRaises(ValidationError):
            Settings.from_env({"APP_ENV": "prod"})

    def test_redacts_provider_secret_patterns(self) -> None:
        text = "openai=sk-example_123 pinecone=pcsk_secret_456 other=visible"
        self.assertEqual(
            redact_secrets(text),
            "openai=[REDACTED] pinecone=[REDACTED] other=visible",
        )

    def test_logging_filter_redacts_message(self) -> None:
        import logging

        record = logging.LogRecord("test", logging.INFO, __file__, 1, "key=%s", ("sk-secret",), None)
        SecretRedactionFilter().filter(record)
        self.assertEqual(record.getMessage(), "key=[REDACTED]")

    def test_prompt_template_hash_and_render(self) -> None:
        prompt = PromptTemplate(name="example", text="Hello {target}: {focus}")

        self.assertEqual(prompt.render(target="SSE", focus="OT"), "Hello SSE: OT")
        self.assertEqual(len(prompt.short_hash), 12)

    def test_fake_provider_returns_schema_valid_model(self) -> None:
        provider = FakeProvider({"stage_research": {"name": "SSE", "count": 3}})

        result, raw = asyncio.run(
            provider.structured(
                system="system",
                prompt="prompt",
                schema=ExampleResponse,
                model="fake-model",
                max_tokens=100,
                stage="stage_research",
            )
        )

        self.assertEqual(result.name, "SSE")
        self.assertEqual(result.count, 3)
        self.assertEqual(raw.provider, "fake")
        self.assertEqual(len(raw.request_hash), 64)

    def test_provider_schema_is_compacted(self) -> None:
        schema = compact_json_schema(ExampleResponse)

        encoded = json.dumps(schema)
        self.assertNotIn('"title"', encoded)
        self.assertNotIn('"default"', encoded)
        self.assertEqual(schema["type"], "object")

    def test_anthropic_web_search_request_allows_search_before_final_tool(self) -> None:
        request = build_anthropic_request(
            model="claude-opus-4-8",
            max_tokens=1000,
            system="system",
            prompt="research",
            schema=ExampleResponse,
            web_search=True,
            web_search_tool="web_search_20250305",
            temperature=None,
        )

        self.assertNotIn("tool_choice", request)
        self.assertEqual(request["tools"][0], {"type": "web_search_20250305", "name": "web_search"})
        self.assertEqual(request["tools"][1]["name"], "write_exampleresponse")
        self.assertIn("write_exampleresponse", request["messages"][0]["content"])

    def test_anthropic_non_search_request_forces_schema_tool(self) -> None:
        request = build_anthropic_request(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system="system",
            prompt="signals",
            schema=ExampleResponse,
            web_search=False,
            web_search_tool="web_search_20250305",
            temperature=None,
        )

        self.assertEqual(request["tool_choice"], {"type": "tool", "name": "write_exampleresponse"})
        self.assertEqual(len(request["tools"]), 1)

    def test_openai_request_uses_compacted_json_schema(self) -> None:
        request = build_openai_request(
            model="gpt-5.5",
            max_tokens=1000,
            system="system",
            prompt="research",
            schema=ExampleResponse,
            web_search=True,
            reasoning_effort="medium",
            temperature=None,
        )

        self.assertEqual(request["tools"], [{"type": "web_search_preview"}])
        self.assertEqual(request["reasoning"], {"effort": "medium"})
        encoded = json.dumps(request["text"]["format"]["schema"])
        self.assertNotIn('"title"', encoded)

    def test_stage_schemas_ignore_extra_llm_note_fields(self) -> None:
        output = NarrativeOutput.model_validate(
            {
                "problem_narrative": "Validated problem narrative.",
                "problem_narrative_note": "The model should not have sent this.",
            }
        )

        self.assertEqual(output.problem_narrative, "Validated problem narrative.")
        self.assertNotIn("problem_narrative_note", output.model_dump())

    def test_use_case_skeleton_normalizes_common_llm_field_variants(self) -> None:
        skeleton = UseCaseSkeleton.model_validate(
            {
                "id": "uc-1",
                "rank": 1,
                "use_case": "Secure supplier file ingress",
                "trigger": "Supplier files enter operational workflows.",
                "problem_statement": "Untrusted files may introduce malware.",
                "signal_ids": ["sig-001"],
                "product_slugs": ["mdcore"],
                "confidence": "medium",
            }
        )

        self.assertEqual(skeleton.title, "Secure supplier file ingress")
        self.assertEqual(skeleton.account_trigger, "Supplier files enter operational workflows.")
        self.assertEqual(skeleton.problem, "Untrusted files may introduce malware.")


if __name__ == "__main__":
    unittest.main()

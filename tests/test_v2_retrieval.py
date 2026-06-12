from __future__ import annotations

import asyncio
import unittest

from app.config import Settings
from app.models.account_map import Confidence
from app.services.retrieval import (
    FakeCustomerStoryRetriever,
    NullCustomerStoryRetriever,
    compact_customer_story_context,
    customer_story_query,
    customer_story_retriever_from_settings,
    example_from_match,
)


class V2RetrievalTests(unittest.TestCase):
    def test_missing_keys_returns_null_retriever_result(self) -> None:
        retriever = customer_story_retriever_from_settings(Settings())
        result = asyncio.run(retriever.search("utility file ingress", top_k=3))

        self.assertFalse(result.configured)
        self.assertFalse(result.disabled)
        self.assertEqual(result.examples, [])
        self.assertIn("OPENAI_API_KEY", result.error or "")
        self.assertEqual(result.index, "opswat-docs")
        self.assertEqual(result.namespace, "customer_stories")

    def test_disabled_setting_returns_disabled_result(self) -> None:
        retriever = customer_story_retriever_from_settings(Settings(customer_story_rag_disabled=True))
        result = asyncio.run(retriever.search("utility file ingress", top_k=3))

        self.assertFalse(result.configured)
        self.assertTrue(result.disabled)
        self.assertIn("disabled", result.error or "")

    def test_example_from_pinecone_match_normalizes_metadata(self) -> None:
        example = example_from_match(
            {
                "score": 0.7,
                "metadata": {
                    "title": "Pipeline operator file security",
                    "products_used": "MetaDefender Core, MetaDefender Vault",
                    "industry_hints": ["Energy", "Critical infrastructure"],
                    "urls": ["https://opswat.example/story"],
                    "text": "The operator scanned untrusted files before release.",
                    "outcome": "Reduced manual review effort.",
                    "anonymous": True,
                },
            }
        )

        self.assertIsNotNone(example)
        assert example is not None
        self.assertEqual(example.title, "Pipeline operator file security")
        self.assertEqual(example.products, ["MetaDefender Core", "MetaDefender Vault"])
        self.assertEqual(example.customer_type, "Energy, Critical infrastructure")
        self.assertEqual(example.source_url, "https://opswat.example/story")
        self.assertEqual(example.confidence, Confidence.high)

    def test_fake_retriever_records_queries_and_compacts_context(self) -> None:
        retriever = FakeCustomerStoryRetriever(
            [
                {
                    "title": "Utility rollout",
                    "customer_type": "Energy",
                    "anonymous": True,
                    "products": ["MetaDefender Core"],
                    "relevance": " ".join(["relevant"] * 80),
                    "retrieval_score": 0.61,
                    "confidence": "high",
                }
            ]
        )
        query = customer_story_query(
            target="Example Energy",
            focus="OT file ingress",
            use_case=type("Skeleton", (), {"title": "Supplier files", "account_trigger": "OT ingress", "problem": "Risk"})(),
            product_names=["MetaDefender Core"],
        )
        result = asyncio.run(retriever.search(query, top_k=1))
        context = compact_customer_story_context(result.examples)

        self.assertIn("Example Energy", retriever.queries[0])
        self.assertIn("Supplier files", retriever.queries[0])
        self.assertEqual(context[0]["title"], "Utility rollout")
        self.assertLessEqual(len(context[0]["relevance"]), 323)

    def test_null_retriever_is_non_throwing(self) -> None:
        result = asyncio.run(NullCustomerStoryRetriever(reason="not ready").search("query"))

        self.assertEqual(result.examples, [])
        self.assertEqual(result.error, "not ready")


if __name__ == "__main__":
    unittest.main()

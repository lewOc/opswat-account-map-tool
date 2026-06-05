#!/usr/bin/env python3
"""Search customer-story chunks in Pinecone."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone


EMBED_MODEL = "text-embedding-3-large"
DEFAULT_INDEX = "opswat-docs"
DEFAULT_NAMESPACE = "customer_stories"
DEFAULT_SHARED_ENV = "/Users/lewis/Documents/opswat_docs_full/opswat_docs_downloads/.env"


def load_env() -> None:
    load_dotenv()
    shared_env = Path(os.getenv("OPSWAT_SHARED_ENV", DEFAULT_SHARED_ENV))
    if shared_env.exists():
        load_dotenv(shared_env, override=False)


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def trim(text: str, limit: int = 900) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def main() -> int:
    parser = argparse.ArgumentParser(description="Search OPSWAT customer-story chunks.")
    parser.add_argument("query", help="Natural-language customer-story search query.")
    parser.add_argument("--index", default=DEFAULT_INDEX, help="Pinecone index name.")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Pinecone namespace.")
    parser.add_argument("--top-k", type=int, default=5, help="Number of matches to return.")
    args = parser.parse_args()

    load_env()
    openai_client = OpenAI(api_key=require_env("OPENAI_API_KEY"))
    pinecone = Pinecone(api_key=require_env("PINECONE_API_KEY"))
    index = pinecone.Index(args.index)

    embedding = openai_client.embeddings.create(model=EMBED_MODEL, input=args.query).data[0].embedding
    results = index.query(
        namespace=args.namespace,
        vector=embedding,
        top_k=args.top_k,
        include_metadata=True,
    )

    print(f"Query: {args.query}")
    print(f"Namespace: {args.namespace}")
    print()
    for rank, match in enumerate(results.matches, start=1):
        metadata: dict[str, Any] = dict(match.metadata or {})
        print(f"{rank}. score={match.score:.4f}")
        print(f"   story: {metadata.get('title')}")
        print(f"   products: {metadata.get('products_used')}")
        print(f"   industries: {metadata.get('industry_hints')}")
        print(f"   anonymous: {metadata.get('anonymous')}")
        print(f"   urls: {metadata.get('urls')}")
        print(f"   text: {trim(str(metadata.get('text') or ''))}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

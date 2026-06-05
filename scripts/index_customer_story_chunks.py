#!/usr/bin/env python3
"""Embed and index customer-story RAG chunks into Pinecone."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from openai import OpenAI
from pinecone import Pinecone


EMBED_MODEL = "text-embedding-3-large"
MAX_EMBED_BATCH_CHARS = 240_000
DEFAULT_CHUNKS = Path("outputs/customer_story_chunks/customer_story_chunks.jsonl")
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


def load_chunks(path: Path, limit: int | None = None) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
            chunks.append(record)
            if limit and len(chunks) >= limit:
                break
    return chunks


def embedding_batches(items: list[dict[str, Any]], max_items: int) -> Iterable[list[dict[str, Any]]]:
    batch: list[dict[str, Any]] = []
    char_count = 0
    for item in items:
        item_chars = len(item["text"])
        if batch and (len(batch) >= max_items or char_count + item_chars > MAX_EMBED_BATCH_CHARS):
            yield batch
            batch = []
            char_count = 0
        batch.append(item)
        char_count += item_chars
    if batch:
        yield batch


def metadata_for_pinecone(record: dict[str, Any]) -> dict[str, Any]:
    source = record.get("metadata") or {}
    clean: dict[str, Any] = {}
    for key, value in source.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            clean[key] = value
        else:
            clean[key] = json.dumps(value, ensure_ascii=True)
    clean["text"] = record.get("text") or ""
    clean["corpus"] = "customer_stories"
    return clean


def embed_texts(client: OpenAI, texts: list[str]) -> list[list[float]]:
    response = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [item.embedding for item in response.data]


def main() -> int:
    parser = argparse.ArgumentParser(description="Index customer-story chunks into Pinecone.")
    parser.add_argument("--chunks", default=str(DEFAULT_CHUNKS), help="Path to customer_story_chunks.jsonl.")
    parser.add_argument("--index", default=DEFAULT_INDEX, help="Pinecone index name.")
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE, help="Pinecone namespace.")
    parser.add_argument("--batch-size", type=int, default=64, help="Embedding/upsert batch size.")
    parser.add_argument("--limit", type=int, default=None, help="Only index this many chunks.")
    args = parser.parse_args()

    load_env()
    openai_client = OpenAI(api_key=require_env("OPENAI_API_KEY"))
    pinecone = Pinecone(api_key=require_env("PINECONE_API_KEY"))
    if args.index not in pinecone.list_indexes().names():
        raise SystemExit(f"Pinecone index not found: {args.index}")
    index = pinecone.Index(args.index)

    chunks = load_chunks(Path(args.chunks), limit=args.limit)
    if not chunks:
        raise SystemExit("No chunks to index.")
    print(f"Loaded {len(chunks)} chunks from {args.chunks}")
    print(f"Index: {args.index}")
    print(f"Namespace: {args.namespace}")

    upserted = 0
    for batch_num, batch in enumerate(embedding_batches(chunks, args.batch_size), start=1):
        vectors = embed_texts(openai_client, [record["text"] for record in batch])
        items = []
        for record, vector in zip(batch, vectors):
            items.append(
                {
                    "id": record["id"],
                    "values": vector,
                    "metadata": metadata_for_pinecone(record),
                }
            )
        index.upsert(vectors=items, namespace=args.namespace)
        upserted += len(items)
        print(f"Batch {batch_num}: upserted {upserted}/{len(chunks)}")

    time.sleep(5)
    print("Index stats:")
    print(index.describe_index_stats())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Chunk local customer-story records for RAG indexing.

Input: one-story-per-line JSONL from scripts/ingest_local_customer_stories.py.
Output: one-chunk-per-line JSONL in a simple embedding shape:

{
  "id": "...",
  "text": "...",
  "metadata": { ... }
}

The chunker skips thin URL-only stories by default.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


DEFAULT_INPUT = Path("outputs/local_customer_stories/local_customer_stories.jsonl")
DEFAULT_OUTPUT_DIR = Path("outputs/customer_story_chunks")
URL_ONLY_KINDS = {"url", "video_url"}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"Invalid JSONL at {path}:{line_no}: {exc}") from exc
    return records


def token_count(text: str) -> int:
    return len(text.split())


def normalize_text(text: str) -> str:
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_large_paragraph(paragraph: str, max_tokens: int) -> list[str]:
    words = paragraph.split()
    if len(words) <= max_tokens:
        return [paragraph]
    chunks = []
    for index in range(0, len(words), max_tokens):
        chunks.append(" ".join(words[index : index + max_tokens]))
    return chunks


def paragraph_chunks(text: str, max_tokens: int, overlap_tokens: int) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n", normalize_text(text)):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        paragraphs.extend(split_large_paragraph(paragraph, max_tokens))

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for paragraph in paragraphs:
        paragraph_tokens = token_count(paragraph)
        if current and current_tokens + paragraph_tokens > max_tokens:
            chunks.append("\n\n".join(current).strip())
            if overlap_tokens > 0:
                overlap = trailing_words("\n\n".join(current), overlap_tokens)
                current = [overlap] if overlap else []
                current_tokens = token_count(overlap)
            else:
                current = []
                current_tokens = 0
        current.append(paragraph)
        current_tokens += paragraph_tokens

    if current:
        chunks.append("\n\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def trailing_words(text: str, count: int) -> str:
    words = text.split()
    if not words:
        return ""
    return " ".join(words[-count:])


def compact_list(values: Iterable[str], limit: int = 12) -> list[str]:
    result = []
    for value in values:
        if value and value not in result:
            result.append(value)
        if len(result) >= limit:
            break
    return result


def source_file_names(record: dict[str, Any]) -> list[str]:
    names = []
    for asset in record.get("assets") or []:
        path = asset.get("path") or ""
        name = Path(path).name if path else ""
        if name:
            names.append(name)
    return compact_list(names, limit=20)


def source_asset_kinds(record: dict[str, Any]) -> list[str]:
    return compact_list((asset.get("kind") or "" for asset in record.get("assets") or []), limit=12)


def is_url_only(record: dict[str, Any]) -> bool:
    asset_counts = record.get("asset_counts") or {}
    return bool(asset_counts) and set(asset_counts).issubset(URL_ONLY_KINDS)


def should_skip(record: dict[str, Any], include_url_only: bool, min_text_chars: int) -> tuple[bool, str | None]:
    if not include_url_only and is_url_only(record):
        return True, "url_only"
    if len(record.get("text") or "") < min_text_chars:
        return True, "too_short"
    return False, None


def chunk_prefix(record: dict[str, Any]) -> str:
    products = ", ".join(record.get("products_used") or []) or "Unknown product"
    industries = ", ".join(record.get("industry_hints") or []) or "Unknown industry"
    urls = ", ".join(record.get("urls") or []) or "No URL captured"
    anonymous = "yes" if record.get("anonymous") else "no"
    return (
        f"Story: {record.get('title')}\n"
        f"Products: {products}\n"
        f"Industry hints: {industries}\n"
        f"Anonymous/internal-style story: {anonymous}\n"
        f"Source URLs: {urls}\n\n"
    )


def build_chunks(
    record: dict[str, Any],
    max_tokens: int,
    overlap_tokens: int,
) -> list[dict[str, Any]]:
    prefix = chunk_prefix(record)
    prefix_tokens = token_count(prefix)
    body_max_tokens = max(250, max_tokens - prefix_tokens)
    body_overlap_tokens = min(overlap_tokens, max(0, body_max_tokens // 3))
    body_chunks = paragraph_chunks(
        record.get("text") or "",
        max_tokens=body_max_tokens,
        overlap_tokens=body_overlap_tokens,
    )
    chunks = []
    story_id = record.get("id")
    total = len(body_chunks)
    for index, body in enumerate(body_chunks):
        chunk_id = f"{story_id}::chunk-{index + 1:03d}"
        chunk_text = normalize_text(prefix + body)
        metadata = {
            "story_id": story_id,
            "chunk_index": index,
            "chunk_count": total,
            "title": record.get("title"),
            "source_type": record.get("source_type"),
            "source_folder": record.get("source_folder"),
            "anonymous": bool(record.get("anonymous")),
            "products_used": record.get("products_used") or [],
            "industry_hints": record.get("industry_hints") or [],
            "urls": record.get("urls") or [],
            "asset_counts": record.get("asset_counts") or {},
            "asset_kinds": source_asset_kinds(record),
            "source_file_names": source_file_names(record),
            "text_path": record.get("text_path"),
            "estimated_tokens": token_count(chunk_text),
            "char_count": len(chunk_text),
        }
        chunks.append({"id": chunk_id, "text": chunk_text, "metadata": metadata})
    return chunks


def write_outputs(chunks: list[dict[str, Any]], skipped: Counter[str], out_dir: Path, source_path: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = out_dir / "customer_story_chunks.jsonl"
    summary_path = out_dir / "summary.json"
    index_path = out_dir / "index.md"

    with chunks_path.open("w", encoding="utf-8") as file:
        for chunk in chunks:
            file.write(json.dumps(chunk, ensure_ascii=True) + "\n")

    product_counts: Counter[str] = Counter()
    industry_counts: Counter[str] = Counter()
    story_counts: Counter[str] = Counter()
    token_counts = []
    for chunk in chunks:
        meta = chunk["metadata"]
        product_counts.update(meta.get("products_used") or [])
        industry_counts.update(meta.get("industry_hints") or [])
        story_counts[meta.get("story_id")] += 1
        token_counts.append(meta.get("estimated_tokens") or 0)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_jsonl": str(source_path),
        "output_dir": str(out_dir),
        "chunk_count": len(chunks),
        "story_count": len(story_counts),
        "skipped": dict(skipped),
        "avg_estimated_tokens": round(sum(token_counts) / len(token_counts), 1) if token_counts else 0,
        "min_estimated_tokens": min(token_counts) if token_counts else 0,
        "max_estimated_tokens": max(token_counts) if token_counts else 0,
        "top_products": dict(product_counts.most_common(20)),
        "top_industry_hints": dict(industry_counts.most_common(20)),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = ["# Customer Story RAG Chunks", "", f"Chunks: {len(chunks)}", f"Stories: {len(story_counts)}", ""]
    for story_id, count in story_counts.most_common():
        sample = next(chunk for chunk in chunks if chunk["metadata"]["story_id"] == story_id)
        lines.append(f"- **{sample['metadata']['title']}** — {count} chunks")
    if skipped:
        lines.extend(["", "## Skipped", ""])
        for reason, count in skipped.items():
            lines.append(f"- {reason}: {count}")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chunk local customer stories for RAG indexing.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input local_customer_stories.jsonl path.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for chunk JSONL.")
    parser.add_argument("--max-tokens", type=int, default=850, help="Approximate max tokens per chunk.")
    parser.add_argument("--overlap-tokens", type=int, default=120, help="Approximate overlap tokens between chunks.")
    parser.add_argument("--min-text-chars", type=int, default=1000, help="Skip records with less extracted text.")
    parser.add_argument("--include-url-only", action="store_true", help="Include URL-only records. Off by default.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_path = Path(args.input)
    out_dir = Path(args.out_dir)
    records = load_jsonl(source_path)
    chunks: list[dict[str, Any]] = []
    skipped: Counter[str] = Counter()

    for record in records:
        skip, reason = should_skip(record, include_url_only=args.include_url_only, min_text_chars=args.min_text_chars)
        if skip:
            skipped[reason or "unknown"] += 1
            continue
        chunks.extend(build_chunks(record, max_tokens=args.max_tokens, overlap_tokens=args.overlap_tokens))

    write_outputs(chunks, skipped=skipped, out_dir=out_dir, source_path=source_path)
    print(f"Loaded stories: {len(records)}", flush=True)
    print(f"Chunks: {len(chunks)}", flush=True)
    print(f"Skipped: {dict(skipped)}", flush=True)
    print(f"JSONL: {out_dir / 'customer_story_chunks.jsonl'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

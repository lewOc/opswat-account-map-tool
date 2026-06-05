#!/usr/bin/env python3
"""Ingest local OPSWAT customer-story assets into a JSONL corpus.

This creates one record per customer-story folder. It extracts:
- PDF text with pypdf.
- PPTX text through the native PPTX zip/XML structure.
- URLs from .url shortcut files.

The output is intended as an internal-only RAG source. Some local assets may
contain non-public or anonymized sales material, so downstream prompts should
treat the corpus as internal pattern evidence rather than public citations.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import zipfile
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_SOURCE_ROOT = Path("~/Documents/customer_stories/Customer Stories").expanduser()
DEFAULT_OUTPUT_DIR = Path("outputs/local_customer_stories")
SUPPORTED_EXTENSIONS = {".pdf", ".pptx", ".url"}

PRODUCT_ALIASES: tuple[tuple[str, str], ...] = (
    ("MetaDefender Managed File Transfer", "MetaDefender Managed File Transfer"),
    ("Managed File Transfer", "MetaDefender Managed File Transfer"),
    ("MD MFT", "MetaDefender Managed File Transfer"),
    (" MFT", "MetaDefender Managed File Transfer"),
    ("MetaDefender Storage Security", "MetaDefender Storage Security"),
    ("MDSS", "MetaDefender Storage Security"),
    ("Storage Security", "MetaDefender Storage Security"),
    ("MetaDefender ICAP Server", "MetaDefender ICAP Server"),
    ("MD ICAP Server", "MetaDefender ICAP Server"),
    ("ICAP Server", "MetaDefender ICAP Server"),
    ("MetaDefender Industrial Firewall", "MetaDefender Industrial Firewall"),
    ("Industrial Firewall", "MetaDefender Industrial Firewall"),
    ("MetaDefender Media Firewall", "MetaDefender Media Firewall"),
    ("Media Firewall", "MetaDefender Media Firewall"),
    ("MetaDefender OT Access", "MetaDefender OT Access"),
    ("MD OT Access", "MetaDefender OT Access"),
    ("MetaDefender OT Security", "MetaDefender OT Security"),
    ("MD OT Security", "MetaDefender OT Security"),
    ("OT Security", "MetaDefender OT Security"),
    ("MetaDefender Email Security", "MetaDefender Email Security"),
    ("Email Security", "MetaDefender Email Security"),
    ("MetaDefender Email Gateway", "MetaDefender Email Gateway"),
    ("MetaDefender Cloud", "MetaDefender Cloud"),
    ("MD Cloud", "MetaDefender Cloud"),
    ("MetaDefender Core", "MetaDefender Core"),
    ("MD Core", "MetaDefender Core"),
    ("MetaDefender Kiosk", "MetaDefender Kiosk"),
    ("MD Kiosk", "MetaDefender Kiosk"),
    ("Kiosk Mini", "MetaDefender Kiosk"),
    ("K2100", "MetaDefender Kiosk"),
    ("MetaDefender Drive", "MetaDefender Drive"),
    ("MD Drive", "MetaDefender Drive"),
    ("MetaDefender Endpoint Validation", "MetaDefender Endpoint Validation"),
    ("MD Endpoint Validation", "MetaDefender Endpoint Validation"),
    ("Endpoint Validation", "MetaDefender Endpoint Validation"),
    ("MetaDefender Endpoint", "MetaDefender Endpoint"),
    ("MD Endpoint", "MetaDefender Endpoint"),
    ("MetaDefender NDR", "MetaDefender NDR"),
    ("MD NDR", "MetaDefender NDR"),
    ("MetaDefender NetWall", "MetaDefender NetWall"),
    ("NetWall", "MetaDefender NetWall"),
    ("My OPSWAT Central Management", "My OPSWAT Central Management"),
    ("My OPSWAT CM", "My OPSWAT Central Management"),
    ("Central Management", "My OPSWAT Central Management"),
    ("MetaDefender Sandbox", "MetaDefender Sandbox"),
    ("MD Sandbox", "MetaDefender Sandbox"),
    ("Sandbox", "MetaDefender Sandbox"),
    ("MetaDefender Optical Diode", "MetaDefender Optical Diode"),
    ("Optical Diode", "MetaDefender Optical Diode"),
    ("Data Diode", "MetaDefender Optical Diode"),
    ("FEND", "MetaDefender Optical Diode"),
    ("OESIS Framework", "OESIS Framework"),
)

INDUSTRY_HINTS = (
    "Aerospace",
    "Airport",
    "Bank",
    "Cybersecurity",
    "Data Center",
    "Education",
    "Energy",
    "Financial Services",
    "Food Processing",
    "Gaming",
    "Government",
    "Healthcare",
    "Insurance",
    "Law Enforcement",
    "Manufacturing",
    "Mining",
    "Nuclear",
    "Oil & Gas",
    "Petrochemical",
    "Pharmaceutical",
    "SaaS",
    "Software",
    "Technology",
    "Telecom",
    "Transportation",
    "Utility",
    "Utilities",
    "Water",
)


@dataclass
class LocalAsset:
    path: str
    kind: str
    bytes: int
    text_chars: int
    extraction_error: str | None = None


@dataclass
class LocalStoryRecord:
    id: str
    title: str
    source_type: str
    source_folder: str
    fetched_at: str
    anonymous: bool
    industry_hints: list[str]
    products_used: list[str]
    urls: list[str]
    asset_counts: dict[str, int]
    assets: list[LocalAsset]
    text_path: str
    text: str


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:90] or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def normalize_text(text: str) -> str:
    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\s+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_url_file(path: Path) -> tuple[list[str], str]:
    urls: list[str] = []
    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        line = line.strip()
        if line.upper().startswith("URL="):
            urls.append(line.split("=", 1)[1].strip())
        elif re.match(r"https?://", line):
            urls.append(line)
    return urls, "\n".join(urls)


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Missing pypdf. Install requirements or run with the bundled Codex Python runtime.") from exc

    reader = PdfReader(str(path))
    pages = [(page.extract_text() or "") for page in reader.pages]
    return normalize_text("\n\n".join(pages))


def pptx_slide_sort_key(name: str) -> tuple[int, str]:
    match = re.search(r"slide(\d+)\.xml$", name)
    return (int(match.group(1)) if match else 999999, name)


def extract_pptx_text(path: Path) -> str:
    texts: list[str] = []
    with zipfile.ZipFile(path) as archive:
        slide_names = sorted(
            (
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            ),
            key=pptx_slide_sort_key,
        )
        for slide_name in slide_names:
            xml = archive.read(slide_name).decode("utf-8", errors="replace")
            slide_texts = [html.unescape(match) for match in re.findall(r"<a:t>(.*?)</a:t>", xml)]
            if slide_texts:
                texts.append(" | ".join(slide_texts))
    return normalize_text("\n\n".join(texts))


def extract_asset_text(path: Path) -> tuple[str, list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(path), []
    if suffix == ".pptx":
        return extract_pptx_text(path), []
    if suffix == ".url":
        urls, text = read_url_file(path)
        return text, urls
    return "", []


def detect_products(*texts: str) -> list[str]:
    haystack = "\n".join(texts)
    found: list[str] = []
    for alias, product in PRODUCT_ALIASES:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])", haystack, flags=re.IGNORECASE):
            found.append(product)
    return list(dict.fromkeys(found))


def extract_industry_labels(text: str) -> list[str]:
    labels: list[str] = []
    for match in re.finditer(r"\bIndustry\s*:\s*([^\n|]{2,80})", text, flags=re.IGNORECASE):
        labels.append(match.group(1).strip())
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for index, line in enumerate(lines):
        if line.upper().rstrip(":") == "INDUSTRY" and index + 1 < len(lines):
            labels.append(lines[index + 1][:80].strip())
    return dedupe_preserve_order(labels)


def detect_industry_hints(*texts: str) -> list[str]:
    haystack = "\n".join(texts)
    found = []
    for hint in INDUSTRY_HINTS:
        if re.search(rf"(?<![A-Za-z0-9]){re.escape(hint)}(?![A-Za-z0-9])", haystack, flags=re.IGNORECASE):
            found.append(hint)
    return list(dict.fromkeys(found))


def story_folders(root: Path) -> list[Path]:
    if not root.exists():
        raise SystemExit(f"Source root does not exist: {root}")
    return sorted(path for path in root.iterdir() if path.is_dir())


def supported_files(folder: Path) -> list[Path]:
    return sorted(
        path
        for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS and path.name != ".DS_Store"
    )


def asset_kind(path: Path) -> str:
    name = path.name.lower()
    if path.suffix.lower() == ".url":
        if "video" in name or "youtube" in name:
            return "video_url"
        return "url"
    if "impact" in name:
        return "impact_slide"
    if "summary" in name or "slick" in name:
        return "summary_slick"
    if "brochure" in name:
        return "brochure"
    if "one pager" in name or "one-pager" in name:
        return "one_pager"
    if "presentation" in name:
        return "presentation"
    return path.suffix.lower().lstrip(".")


def dedupe_preserve_order(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def build_story_record(folder: Path, out_dir: Path, fetched_at: str) -> LocalStoryRecord:
    title = folder.name
    story_id = slugify(title)
    text_parts: list[str] = []
    signal_parts: list[str] = [title]
    industry_labels: list[str] = []
    urls: list[str] = []
    assets: list[LocalAsset] = []
    counts: Counter[str] = Counter()

    for path in supported_files(folder):
        kind = asset_kind(path)
        counts[kind] += 1
        signal_parts.append(path.name)
        try:
            text, asset_urls = extract_asset_text(path)
            urls.extend(asset_urls)
            if text:
                text_parts.append(f"## Source: {path.name}\n{text}")
                signal_parts.append(text[:3500])
                industry_labels.extend(extract_industry_labels(text[:6000]))
            assets.append(
                LocalAsset(
                    path=str(path),
                    kind=kind,
                    bytes=path.stat().st_size,
                    text_chars=len(text),
                )
            )
        except Exception as exc:
            assets.append(
                LocalAsset(
                    path=str(path),
                    kind=kind,
                    bytes=path.stat().st_size,
                    text_chars=0,
                    extraction_error=str(exc),
                )
            )

    combined_text = normalize_text("\n\n".join(text_parts))
    text_dir = out_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    text_path = text_dir / f"{story_id}.txt"
    text_path.write_text(combined_text, encoding="utf-8")

    signal_text = normalize_text("\n\n".join(signal_parts))
    products = detect_products(signal_text)
    industries = dedupe_preserve_order(industry_labels + detect_industry_hints(title, "\n".join(industry_labels)))
    return LocalStoryRecord(
        id=story_id,
        title=title,
        source_type="local_customer_story_folder",
        source_folder=str(folder),
        fetched_at=fetched_at,
        anonymous=bool(re.search(r"\banon(?:ymous)?\b", title, flags=re.IGNORECASE)),
        industry_hints=industries,
        products_used=products,
        urls=dedupe_preserve_order(urls),
        asset_counts=dict(sorted(counts.items())),
        assets=assets,
        text_path=str(text_path),
        text=combined_text,
    )


def write_outputs(records: list[LocalStoryRecord], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "local_customer_stories.jsonl"
    summary_path = out_dir / "summary.json"
    index_path = out_dir / "index.md"

    with jsonl_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(asdict(record), ensure_ascii=True) + "\n")

    product_counts: Counter[str] = Counter()
    industry_counts: Counter[str] = Counter()
    asset_counts: Counter[str] = Counter()
    extraction_errors = 0
    for record in records:
        product_counts.update(record.products_used)
        industry_counts.update(record.industry_hints)
        asset_counts.update(record.asset_counts)
        extraction_errors += sum(1 for asset in record.assets if asset.extraction_error)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stories": len(records),
        "jsonl_path": str(jsonl_path),
        "output_dir": str(out_dir),
        "asset_counts": dict(asset_counts.most_common()),
        "top_products": dict(product_counts.most_common(20)),
        "top_industry_hints": dict(industry_counts.most_common(20)),
        "extraction_errors": extraction_errors,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = ["# Local OPSWAT Customer Story Corpus", "", f"Stories: {len(records)}", ""]
    for record in records:
        products = ", ".join(record.products_used) if record.products_used else "Products not detected"
        assets = ", ".join(f"{key}: {value}" for key, value in record.asset_counts.items())
        lines.append(f"- **{record.title}** — {products} — {assets}")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest local OPSWAT customer-story PDFs/PPTXs/URLs.")
    parser.add_argument("--source-root", default=str(DEFAULT_SOURCE_ROOT), help="Folder containing one subfolder per customer story.")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory for JSONL and extracted text.")
    parser.add_argument("--max-stories", type=int, default=0, help="Limit stories for testing. 0 means no limit.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source_root = Path(args.source_root).expanduser()
    out_dir = Path(args.out_dir)
    folders = story_folders(source_root)
    if args.max_stories:
        folders = folders[: args.max_stories]

    fetched_at = datetime.now(timezone.utc).isoformat()
    records: list[LocalStoryRecord] = []
    print(f"Found {len(folders)} story folders", flush=True)
    for index, folder in enumerate(folders, start=1):
        record = build_story_record(folder, out_dir, fetched_at)
        records.append(record)
        print(
            f"[{index:03d}] {record.title} "
            f"files={sum(record.asset_counts.values())} text_chars={len(record.text)}",
            flush=True,
        )

    write_outputs(records, out_dir)
    print(f"\nWrote {len(records)} local stories to {out_dir}", flush=True)
    print(f"JSONL: {out_dir / 'local_customer_stories.jsonl'}", flush=True)
    print(f"Index: {out_dir / 'index.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

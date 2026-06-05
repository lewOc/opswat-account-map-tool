#!/usr/bin/env python3
"""Ingest public OPSWAT customer stories into a local JSONL corpus.

The crawler is intentionally small and polite:
- Uses browser-like headers because bare curl/python requests are blocked.
- Starts from public /customers and the public case-studies sitemap.
- Sleeps between page requests.
- Saves raw HTML, cleaned text, metadata, and a JSONL corpus.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable


BASE_URL = "https://www.opswat.com"
CUSTOMERS_URL = f"{BASE_URL}/customers"
SITEMAP_INDEX_URL = f"{BASE_URL}/sitemaps-1-sitemap.xml"
CASE_STUDIES_SITEMAP_URL = f"{BASE_URL}/sitemaps-1-section-caseStudies-1-sitemap.xml"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
)
BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "br",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "section",
    "td",
    "th",
    "tr",
}
SKIP_TAGS = {"script", "style", "svg", "noscript", "template"}
STORY_MARKERS = (
    "customer stories",
    "what's the story",
    "products used",
    "about the company",
    "resources/case-studies",
)
BOILERPLATE_PATTERNS = (
    r"^Home\s*/",
    r"^Share this Post$",
    r"^Latest Posts$",
    r"^Find Out How",
    r"^Request a Demo$",
    r"^Talk to an Expert$",
    r"^OPSWAT$",
    r"^Protecting the World's Critical Infrastructure$",
)
KNOWN_PRODUCTS = (
    "MetaDefender Managed File Transfer",
    "MetaDefender Industrial Firewall",
    "My OPSWAT Central Management",
    "MetaDefender Email Gateway",
    "MetaDefender Media Firewall",
    "MetaDefender ICAP Server",
    "MetaDefender OT Access",
    "MetaDefender Storage Security",
    "MetaDefender Cloud Email Security",
    "MetaDefender Cloud",
    "MetaDefender Core",
    "MetaDefender Kiosk",
    "MetaDefender Drive",
    "MetaDefender Access",
    "MetaDefender NetWall",
    "MetaDefender NDR",
    "MetaDefender Endpoint",
    "MetaDefender Aether",
    "MetaDefender Platform",
    "OESIS Framework",
    "OPSWAT MetaDefender",
)


@dataclass
class FetchResult:
    url: str
    status: int
    content_type: str
    body: str


@dataclass
class StoryRecord:
    id: str
    title: str
    url: str
    source_type: str
    fetched_at: str
    industry: str | None
    location: str | None
    company_size: str | None
    products_used: list[str]
    description: str | None
    text_path: str
    html_path: str
    text: str


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in SKIP_TAGS and self.skip_depth:
            self.skip_depth -= 1
            return
        if self.skip_depth:
            return
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        raw = " ".join(self.parts)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s+", "\n", raw)
        raw = re.sub(r"\s+\n", "\n", raw)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in BOILERPLATE_PATTERNS):
                continue
            lines.append(line)
        return "\n".join(dedupe_adjacent(lines)).strip()


def dedupe_adjacent(lines: Iterable[str]) -> list[str]:
    result: list[str] = []
    previous = None
    for line in lines:
        if line == previous:
            continue
        result.append(line)
        previous = line
    return result


def fetch(url: str, timeout: int = 30) -> FetchResult:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-GB,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return FetchResult(url, response.status, response.headers.get("content-type", ""), body)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return FetchResult(url, exc.code, exc.headers.get("content-type", ""), body)


def clean_url(url: str) -> str:
    parsed = urllib.parse.urlparse(urllib.parse.urljoin(BASE_URL, unescape(url)))
    if parsed.netloc not in {"www.opswat.com", "opswat.com"}:
        return ""
    return urllib.parse.urlunparse(parsed._replace(scheme="https", netloc="www.opswat.com", query="", fragment=""))


def extract_hrefs(html: str) -> set[str]:
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE)
    return {url for href in hrefs if (url := clean_url(href))}


def extract_sitemap_locs(xml_text: str) -> set[str]:
    locs: set[str] = set()
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return locs
    for elem in root.iter():
        if elem.tag.endswith("loc") and elem.text:
            url = clean_url(elem.text.strip())
            if url:
                locs.add(url)
    return locs


def read_seed_file(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    seed_path = Path(path)
    if not seed_path.exists():
        raise SystemExit(f"Seed file not found: {seed_path}")
    seeds: dict[str, str] = {}
    for raw_line in seed_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        url = clean_url(line)
        if url:
            seeds[url] = "seed_file"
    return seeds


def discover_urls(include_blog_sitemap: bool = False, seed_file: str | None = None) -> tuple[list[str], dict[str, str]]:
    discovered: dict[str, str] = {}
    discovered.update(read_seed_file(seed_file))

    customers = fetch(CUSTOMERS_URL)
    if customers.status == 200:
        for url in extract_hrefs(customers.body):
            path = urllib.parse.urlparse(url).path
            if path.startswith("/blog/") or path.startswith("/resources/case-studies/"):
                discovered[url] = "customers_page"
    else:
        print(f"WARN customers page returned HTTP {customers.status}", flush=True)

    case_studies = fetch(CASE_STUDIES_SITEMAP_URL)
    if case_studies.status == 200:
        for url in extract_sitemap_locs(case_studies.body):
            if "/resources/case-studies/" in urllib.parse.urlparse(url).path:
                discovered[url] = "case_studies_sitemap"
    else:
        print(f"WARN case-studies sitemap returned HTTP {case_studies.status}", flush=True)

    if include_blog_sitemap:
        sitemap_index = fetch(SITEMAP_INDEX_URL)
        sitemap_urls = extract_sitemap_locs(sitemap_index.body) if sitemap_index.status == 200 else {f"{BASE_URL}/sitemaps-blog.xml"}
        blog_sitemaps = [url for url in sitemap_urls if "blog" in url]
        for sitemap_url in blog_sitemaps:
            sitemap = fetch(sitemap_url)
            if sitemap.status != 200:
                continue
            for url in extract_sitemap_locs(sitemap.body):
                if "/blog/" in urllib.parse.urlparse(url).path:
                    discovered.setdefault(url, "blog_sitemap")

    return sorted(discovered), discovered


def title_from_html(html: str) -> str:
    for pattern in (
        r"<h1[^>]*>(.*?)</h1>",
        r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)[\"']",
        r"<title[^>]*>(.*?)</title>",
    ):
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_inline(strip_tags(match.group(1)).replace(" - OPSWAT", ""))
    return "Untitled OPSWAT customer story"


def meta_description(html: str) -> str | None:
    for pattern in (
        r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']+)[\"']",
        r"<meta[^>]+property=[\"']og:description[\"'][^>]+content=[\"']([^\"']+)[\"']",
    ):
        match = re.search(pattern, html, flags=re.IGNORECASE | re.DOTALL)
        if match:
            return normalize_inline(match.group(1))
    return None


def strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


def normalize_inline(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def html_to_text(html: str) -> str:
    extractor = TextExtractor()
    extractor.feed(html)
    return extractor.text()


def is_customer_story(url: str, text: str) -> bool:
    path = urllib.parse.urlparse(url).path
    lowered = f"{path}\n{text[:6000]}".lower()
    if "/resources/case-studies/" in path:
        return True
    return sum(marker in lowered for marker in STORY_MARKERS) >= 2


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:90] or hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def extract_label(text: str, labels: tuple[str, ...]) -> str | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    upper_labels = tuple(label.upper().rstrip(":") for label in labels)
    stop_labels = {
        "INDUSTRY",
        "LOCATION",
        "SIZE",
        "PRODUCTS USED",
        "KEY TECHNOLOGIES",
        "ABOUT THE COMPANY",
        "WHAT'S THE STORY?",
    }
    for idx, line in enumerate(lines):
        normalized = line.upper().rstrip(":")
        if normalized not in upper_labels:
            continue
        values: list[str] = []
        for following in lines[idx + 1 : idx + 5]:
            if following.upper().rstrip(":") in stop_labels:
                break
            if len(following) > 120:
                break
            values.append(following)
            if values:
                break
        value = " ".join(values).strip()
        return value or None
    return None


def extract_products(text: str, title: str = "", description: str | None = None) -> list[str]:
    found: list[str] = []
    value = extract_label(text, ("PRODUCTS USED", "PRODUCT USED"))
    search_text = value if value and len(value) < 300 else f"{title}\n{description or ''}"
    normalized_text = normalize_inline(search_text).replace("™", "")
    for product in KNOWN_PRODUCTS:
        if re.search(rf"\b{re.escape(product)}\b", normalized_text, flags=re.IGNORECASE):
            found.append(product)
    if re.search(r"\bMobile Kiosk\b|\bKiosk App\b|\bK2100\b", normalized_text, flags=re.IGNORECASE):
        found.append("MetaDefender Kiosk")
    return list(dict.fromkeys(found))


def build_record(
    url: str,
    html: str,
    source_type: str,
    out_dir: Path,
    fetched_at: str,
) -> StoryRecord:
    title = title_from_html(html)
    description = meta_description(html)
    story_id = slugify(title)
    raw_dir = out_dir / "raw_html"
    text_dir = out_dir / "text"
    raw_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    html_path = raw_dir / f"{story_id}.html"
    text_path = text_dir / f"{story_id}.txt"
    text = html_to_text(html)
    html_path.write_text(html, encoding="utf-8")
    text_path.write_text(text, encoding="utf-8")
    return StoryRecord(
        id=story_id,
        title=title,
        url=url,
        source_type=source_type,
        fetched_at=fetched_at,
        industry=extract_label(text, ("INDUSTRY",)),
        location=extract_label(text, ("LOCATION",)),
        company_size=extract_label(text, ("SIZE", "COMPANY SIZE")),
        products_used=extract_products(text, title=title, description=description),
        description=description,
        text_path=str(text_path),
        html_path=str(html_path),
        text=text,
    )


def write_outputs(records: list[StoryRecord], out_dir: Path, discovered_count: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = out_dir / "customer_stories.jsonl"
    summary_path = out_dir / "summary.json"
    index_path = out_dir / "index.md"
    with jsonl_path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(asdict(record), ensure_ascii=True) + "\n")

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discovered_urls": discovered_count,
        "customer_stories": len(records),
        "jsonl_path": str(jsonl_path),
        "output_dir": str(out_dir),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    lines = ["# OPSWAT Customer Story Corpus", "", f"Stories: {len(records)}", ""]
    for record in records:
        products = ", ".join(record.products_used) if record.products_used else "Products not parsed"
        lines.append(f"- [{record.title}]({record.url}) — {products}")
    index_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download public OPSWAT customer stories into a local corpus.")
    parser.add_argument("--out-dir", default="outputs/customer_stories", help="Output directory for raw HTML, text, and JSONL.")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds to wait between story page requests.")
    parser.add_argument("--max-pages", type=int, default=0, help="Limit story page fetches for testing. 0 means no limit.")
    parser.add_argument("--include-blog-sitemap", action="store_true", help="Also inspect blog sitemap URLs. Off by default.")
    parser.add_argument("--seed-file", default=None, help="Optional text file containing additional OPSWAT URLs, one per line.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    urls, sources = discover_urls(include_blog_sitemap=args.include_blog_sitemap, seed_file=args.seed_file)
    if args.max_pages:
        urls = urls[: args.max_pages]
    print(f"Discovered {len(urls)} candidate URLs", flush=True)

    records: list[StoryRecord] = []
    fetched_at = datetime.now(timezone.utc).isoformat()
    for index, url in enumerate(urls, start=1):
        if index > 1:
            time.sleep(max(args.delay, 0))
        result = fetch(url)
        if result.status != 200:
            print(f"SKIP HTTP {result.status}: {url}", flush=True)
            continue
        text = html_to_text(result.body)
        if not is_customer_story(url, text):
            print(f"SKIP non-story: {url}", flush=True)
            continue
        record = build_record(url, result.body, sources.get(url, "unknown"), out_dir, fetched_at)
        records.append(record)
        print(f"[{len(records):03d}] {record.title}", flush=True)

    write_outputs(records, out_dir, discovered_count=len(sources))
    print(f"\nWrote {len(records)} stories to {out_dir}", flush=True)
    print(f"JSONL: {out_dir / 'customer_stories.jsonl'}", flush=True)
    print(f"Index: {out_dir / 'index.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

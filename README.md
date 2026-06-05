# OPSWAT Account Map Tool

Account mapping prototype for generating source-grounded OPSWAT account plays, use-case diagrams, and partner-facing slide decks.

## What Exists

- `api.py` serves the FastAPI API and browser UI.
- `ui/` contains the account-manager interface.
- `scripts/account_map.py` generates sourced account maps with Claude.
- `scripts/diagram_generator.py` generates OPSWAT-style SVG architecture/data-flow diagrams.
- `scripts/ingest_customer_stories.py` downloads public OPSWAT customer stories into a local JSONL corpus.
- `scripts/ingest_local_customer_stories.py` extracts the local customer-story PDF/PPTX/URL folder into an internal JSONL corpus.
- `scripts/export_deck.mjs` exports account-map content into PowerPoint.
- `data/capability_map.json` is the product capability map used for product-fit grounding.
- `assets/product_icons` and `assets/other_icons` contain diagram icon assets.

Generated account maps, diagrams, decks, and scratch files are intentionally ignored by git under `outputs/` and `work/`.

## Ingest Customer Stories

The local customer-story ingester should be the primary source when the local archive is available:

```bash
python scripts/ingest_local_customer_stories.py \
  --source-root ~/Documents/customer_stories/"Customer Stories" \
  --out-dir outputs/local_customer_stories
```

It writes:

- `outputs/local_customer_stories/local_customer_stories.jsonl`
- `outputs/local_customer_stories/index.md`
- `outputs/local_customer_stories/summary.json`
- `outputs/local_customer_stories/text/`

The public customer-story ingestion script builds a supplemental corpus from public OPSWAT customer pages and case-study sitemaps:

```bash
python scripts/ingest_customer_stories.py --out-dir outputs/customer_stories --delay 1.0
```

It writes:

- `outputs/customer_stories/customer_stories.jsonl`
- `outputs/customer_stories/index.md`
- `outputs/customer_stories/summary.json`
- `outputs/customer_stories/raw_html/`
- `outputs/customer_stories/text/`

Use `--seed-file path/to/urls.txt` to add extra OPSWAT story URLs that are not linked from the public customer page.

## Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Add at least:

```text
ANTHROPIC_API_KEY=...
ANTHROPIC_MODEL=claude-opus-4-8
```

Optional environment settings are documented in `.env.example`.

## Run The UI

```bash
. .venv/bin/activate
uvicorn api:app --reload --host 127.0.0.1 --port 8010
```

Open:

```text
http://127.0.0.1:8010
```

## Generate A Real Account Map

```bash
python scripts/account_map.py "SSE energy company"
```

Outputs are written under:

```text
outputs/account_maps/
```

## Generate A Diagram

The reusable diagram endpoint accepts a use-case context and returns a normalized diagram spec plus an SVG artifact:

```bash
curl -s -X POST http://127.0.0.1:8010/api/diagrams \
  -H 'Content-Type: application/json' \
  -d @work/diagram-payload.json
```

Response fields:

- `id`: generated diagram ID.
- `spec`: normalized diagram model.
- `svg`: inline SVG.
- `svg_url`: reusable SVG URL, for example `/api/diagrams/<id>.svg`.
- `json_url`: reusable JSON spec URL, for example `/api/diagrams/<id>.json`.

Account-map generation can enrich each `recommended_use_cases` item with either the deterministic OPSWAT SVG renderer or GPT Image output. SVG is the default. GPT Image uses the user's OpenAI key, local reference diagrams in `assets/references/diagrams`, and relevant product icons from `assets/product_icons`.

SVG enrichment shape:

```json
{
  "diagram": {
    "id": "...",
    "pattern": "removable_media",
    "svg_url": "/api/diagrams/....svg",
    "json_url": "/api/diagrams/....json"
  }
}
```

GPT Image enrichment shape:

```json
{
  "diagram": {
    "id": "...",
    "pattern": "gpt_image",
    "renderer": "gpt_image",
    "image_url": "/api/image-diagrams/....png",
    "json_url": "/api/image-diagrams/....json",
    "model": "gpt-image-2"
  }
}
```

Saved maps that pre-date this feature are backfilled when opened through `GET /api/account-maps/{map_id}`.

## Notes For Deployment

- Keep the app behind authentication before sharing with the team.
- Mount or back up `outputs/` if generated maps/decks should persist.
- Set `PRESENTATION_TEMPLATE_PATH` before using PPTX export.
- If using deck export outside Codex, install or provide `@oai/artifact-tool` and set `ARTIFACT_TOOL_MODULE` if needed.

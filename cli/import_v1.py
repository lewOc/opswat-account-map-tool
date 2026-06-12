"""Import v1 JSON maps into the v2 AccountMap contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.models.legacy import LegacyConversionError, from_v1_file


def json_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(item for item in path.glob("*.json") if item.is_file())


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert v1 account-map JSON into v2 AccountMap JSON.")
    parser.add_argument("input", type=Path, help="A v1 JSON file or a directory of v1 JSON files.")
    parser.add_argument("--out-dir", type=Path, default=Path("var/imported_v1"), help="Directory for converted v2 JSON.")
    args = parser.parse_args()

    paths = json_paths(args.input)
    if not paths:
        raise SystemExit(f"No JSON files found: {args.input}")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    converted = 0
    skipped = 0
    for path in paths:
        try:
            account_map = from_v1_file(path)
        except (LegacyConversionError, json.JSONDecodeError) as exc:
            skipped += 1
            print(f"SKIP {path.name}: {exc}")
            continue
        out_path = args.out_dir / f"{account_map.id}.json"
        out_path.write_text(account_map.model_dump_json(indent=2) + "\n", encoding="utf-8")
        converted += 1
        print(f"OK   {path.name} -> {out_path}")

    print(f"Converted: {converted}; skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

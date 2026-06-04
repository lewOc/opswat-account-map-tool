#!/usr/bin/env python3
"""Reusable OPSWAT-style diagram generator.

The generator intentionally emits SVG first. SVG gives us an API-friendly
artifact that can be previewed in a browser, embedded in apps, or later inserted
into slide decks as an image/vector source.
"""

from __future__ import annotations

import html
import json
import re
import base64
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any


WIDTH = 1280
HEIGHT = 720

THEME = {
    "ink": "#050E22",
    "near_black": "#1F2937",
    "navy": "#0B1424",
    "muted": "#6D7C98",
    "blue": "#2563EB",
    "active_blue": "#1E6BFF",
    "cyan": "#22D3EE",
    "green": "#2DBE6C",
    "zone_green": "#16A34A",
    "red": "#E23B3B",
    "zone_red": "#DC2626",
    "kiosk_red": "#C2362F",
    "yellow": "#FFD600",
    "orange": "#E8842A",
    "black_flow": "#111827",
    "line": "#CBD5E1",
    "soft": "#F6F8FB",
    "inactive": "#E5E7EB",
    "inactive_text": "#9CA3AF",
    "white": "#FFFFFF",
}

ZONE_LABELS = [
    {"rank": 0, "label": "EXTERNAL", "x": 40, "color": "#1F2937"},
    {"rank": 1, "label": "LOW · DMZ/Landing Zone", "x": 535, "color": "#16A34A"},
    {"rank": 2, "label": "HIGH · Enterprise Network", "x": 760, "color": "#E8842A"},
    {"rank": 3, "label": "EXTRA HIGH · High Security Zone", "x": 1020, "color": "#DC2626"},
]

FLOW_ROLE_COLORS = {
    "ingress": "#E23B3B",
    "egress": "#2DBE6C",
    "primary": "#111827",
    "sync": "#2563EB",
}

PROJECT_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_DIR = PROJECT_DIR.parent
PRODUCT_ICON_DIR = Path(os.environ.get("PRODUCT_ICON_DIR", PROJECT_DIR / "assets" / "product_icons"))
OTHER_ICON_DIR = Path(os.environ.get("OTHER_ICON_DIR", PROJECT_DIR / "assets" / "other_icons"))

PRODUCT_ICON_FILES = {
    "core": "on_premises.png",
    "kiosk": "kiosk_tower.png",
    "mft": "managed_file_transfer_mft.png",
    "diode": "transfer_guard.png",
    "drive": "drive.png",
    "firewall": "media_firewall.png",
    "email": "email_Security.png",
    "hmi": "ot_Security.png",
    "product": "on_premises.png",
}

UTILITY_ICON_FILES = {
    "file": "file_blue.png",
    "file_red": "file_red.png",
    "file_green": "file_blue.png",
    "media": "usb_blue.png",
    "media_red": "usb_red.png",
    "email": "email_blue.png",
    "threat": "threat_red.png",
    "server": "file_grey.png",
    "folder": "file_grey.png",
}


@dataclass
class DiagramArtifact:
    diagram_id: str
    spec: dict[str, Any]
    svg: str


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return slug or "diagram"


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def truncate(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", "" if value is None else str(value)).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def wrap_words(value: Any, limit: int, max_lines: int = 2) -> list[str]:
    words = re.sub(r"\s+", " ", "" if value is None else str(value)).strip().split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) == max_lines - 1:
            break
    remaining = " ".join(words[sum(len(line.split()) for line in lines) :])
    if remaining:
        current = truncate(remaining, limit)
    if current:
        lines.append(current)
    return lines[:max_lines] or [""]


def product_type(name: str) -> str:
    normalized = name.lower()
    if "kiosk" in normalized:
        return "kiosk"
    if "core" in normalized:
        return "core"
    if "managed file transfer" in normalized or normalized == "mft" or " mft" in normalized:
        return "mft"
    if "diode" in normalized:
        return "diode"
    if "drive" in normalized:
        return "drive"
    if "media firewall" in normalized:
        return "firewall"
    if "email" in normalized:
        return "email"
    if "hmi" in normalized:
        return "hmi"
    return "product"


def product_short_name(name: str) -> str:
    normalized = name.lower()
    if "managed file transfer" in normalized:
        return "MFT"
    if "metadefender core" in normalized:
        return "Core"
    if "metadefender kiosk" in normalized:
        return "Kiosk"
    if "metadefender drive" in normalized:
        return "Drive"
    if "data diode" in normalized or "diode" in normalized:
        return "Diode"
    if "media firewall" in normalized:
        return "Media Firewall"
    return truncate(name.replace("MetaDefender ", ""), 16)


@lru_cache(maxsize=128)
def asset_data_uri(path_text: str) -> str | None:
    path = Path(path_text)
    if not path.exists():
        return None
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def product_icon_uri(product_type_value: str) -> str | None:
    filename = PRODUCT_ICON_FILES.get(product_type_value) or PRODUCT_ICON_FILES["product"]
    return asset_data_uri(str(PRODUCT_ICON_DIR / filename))


def utility_icon_uri(kind: str) -> str | None:
    filename = UTILITY_ICON_FILES.get(kind)
    if not filename:
        return None
    return asset_data_uri(str(OTHER_ICON_DIR / filename))


def flow_color(flow: dict[str, Any]) -> str:
    role = flow.get("role")
    if role in FLOW_ROLE_COLORS:
        return FLOW_ROLE_COLORS[role]
    color_key = flow.get("color")
    if color_key in THEME:
        return THEME[color_key]
    return str(color_key or FLOW_ROLE_COLORS["primary"])


def flow_marker_id(flow: dict[str, Any]) -> str:
    role = flow.get("role")
    if role in {"ingress", "egress", "primary", "sync"}:
        return f"{role}-arrow"
    return "primary-arrow"


def get_use_case_products(use_case: dict[str, Any], explicit_products: list[Any]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for product in use_case.get("opswat_products") or []:
        if isinstance(product, dict):
            name = product.get("product") or product.get("name") or product.get("slug") or "OPSWAT Product"
            products.append(
                {
                    "name": name,
                    "short": product_short_name(name),
                    "type": product_type(name),
                    "capabilities": product.get("capabilities_used") or product.get("matched_capabilities") or [],
                }
            )
    for product in explicit_products:
        if isinstance(product, str):
            products.append({"name": product, "short": product_short_name(product), "type": product_type(product), "capabilities": []})
        elif isinstance(product, dict):
            name = product.get("name") or product.get("product") or "OPSWAT Product"
            products.append(
                {
                    "name": name,
                    "short": product.get("short") or product_short_name(name),
                    "type": product.get("type") or product_type(name),
                    "capabilities": product.get("capabilities") or [],
                }
            )

    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for product in products:
        key = product["short"].lower()
        if key not in seen:
            deduped.append(product)
            seen.add(key)
    return deduped


def infer_pattern(use_case: dict[str, Any], products: list[dict[str, Any]], requested: str | None) -> str:
    if requested and requested != "auto":
        return requested
    text = " ".join(
        [
            str(use_case.get("title") or use_case.get("use_case") or ""),
            str(use_case.get("account_trigger") or use_case.get("problem") or ""),
            str(use_case.get("business_value") or ""),
            " ".join(product["name"] for product in products),
        ]
    ).lower()
    if any(word in text for word in ["removable", "usb", "media", "kiosk", "drive", "transient"]):
        return "removable_media"
    if any(word in text for word in ["diode", "one-way", "unidirectional"]):
        return "cross_domain"
    if any(word in text for word in ["email", "mft", "file transfer", "supplier", "contractor", "sftp", "managed file"]):
        return "secure_file_exchange"
    return "secure_file_exchange"


def build_spec(payload: dict[str, Any]) -> dict[str, Any]:
    use_case = payload.get("use_case") or {}
    if not isinstance(use_case, dict):
        use_case = {}
    products = get_use_case_products(use_case, payload.get("products") or [])
    pattern = infer_pattern(use_case, products, payload.get("pattern"))
    account_name = payload.get("account_name") or payload.get("account") or payload.get("customer") or ""
    title = payload.get("title") or use_case.get("title") or use_case.get("use_case") or "A Day in the Life of a Data File"
    subtitle = payload.get("subtitle") or "SECURING THE FLOW OF DATA"
    include_purdue = bool(payload.get("include_purdue", pattern in {"removable_media", "cross_domain", "secure_file_exchange"}))

    if not products:
        if pattern == "removable_media":
            products = [
                {"name": "MetaDefender Kiosk", "short": "Kiosk", "type": "kiosk", "capabilities": []},
                {"name": "MetaDefender Core", "short": "Core", "type": "core", "capabilities": []},
            ]
        else:
            products = [
                {"name": "MetaDefender Managed File Transfer", "short": "MFT", "type": "mft", "capabilities": []},
                {"name": "MetaDefender Core", "short": "Core", "type": "core", "capabilities": []},
            ]

    product_by_type = {product["type"]: product for product in products}
    has_kiosk = "kiosk" in product_by_type
    has_mft = "mft" in product_by_type
    has_core = "core" in product_by_type
    has_diode = "diode" in product_by_type

    nodes: list[dict[str, Any]] = []
    flows: list[dict[str, Any]] = []

    if pattern == "removable_media" or has_kiosk:
        nodes.extend(
            [
                {"id": "actor", "label": "Engineer /\nContractor", "kind": "actor", "x": 42, "y": 440},
                {"id": "media", "label": "External\nMedia", "kind": "source", "x": 150, "y": 438},
                {"id": "kiosk", "label": product_by_type.get("kiosk", {"short": "Kiosk"})["short"], "kind": "product", "product_type": "kiosk", "x": 260, "y": 420, "variant": "active"},
                {"id": "core", "label": product_by_type.get("core", {"short": "Core"})["short"], "kind": "product", "product_type": "core", "x": 480, "y": 360, "variant": "active"},
                {"id": "quarantine", "label": "Quarantine", "kind": "quarantine", "x": 595, "y": 410},
                {"id": "ot", "label": "OT Assets /\nSecure Zone", "kind": "zone", "x": 860, "y": 370},
            ]
        )
        flows.extend(
            [
                {"from": "actor", "to": "media", "role": "ingress", "label": "untrusted file", "glyph": "file_red"},
                {"from": "media", "to": "kiosk", "role": "ingress", "label": "scan media", "glyph": "media_red"},
                {"from": "kiosk", "to": "core", "role": "primary", "label": "inspect"},
                {"from": "core", "to": "ot", "role": "egress", "label": "sanitized", "glyph": "file_green"},
                {"from": "core", "to": "quarantine", "role": "ingress", "label": "blocked", "indicator": "quarantine"},
            ]
        )
        if any(product["type"] == "drive" for product in products):
            nodes.append({"id": "drive", "label": "Drive", "kind": "product", "product_type": "drive", "x": 260, "y": 300})
            flows.append({"from": "drive", "to": "core", "role": "primary", "label": "offline scan", "glyph": "media_red"})

    elif pattern == "cross_domain" or has_diode:
        nodes.extend(
            [
                {"id": "source", "label": "Low-side\nSystems", "kind": "source", "x": 80, "y": 380},
                {"id": "core", "label": product_by_type.get("core", {"short": "Core"})["short"], "kind": "product", "product_type": "core", "x": 295, "y": 360, "variant": "active"},
                {"id": "diode", "label": product_by_type.get("diode", {"short": "Diode"})["short"], "kind": "product", "product_type": "diode", "x": 520, "y": 360},
                {"id": "mft", "label": product_by_type.get("mft", {"short": "MFT"})["short"], "kind": "product", "product_type": "mft", "x": 720, "y": 360},
                {"id": "secure", "label": "High-side\nConsumers", "kind": "zone", "x": 960, "y": 350},
            ]
        )
        flows.extend(
            [
                {"from": "source", "to": "core", "role": "ingress", "label": "inspect", "glyph": "file_red"},
                {"from": "core", "to": "diode", "role": "egress", "label": "validated", "glyph": "file_green"},
                {"from": "diode", "to": "mft", "role": "primary", "label": "one-way"},
                {"from": "mft", "to": "secure", "role": "egress", "label": "deliver", "glyph": "file_green"},
            ]
        )

    else:
        nodes.extend(
            [
                {"id": "supplier", "label": "3rd Parties /\nSuppliers", "kind": "actor", "x": 44, "y": 350},
                {"id": "incoming", "label": "Incoming\nFiles", "kind": "source", "x": 160, "y": 350},
                {"id": "mft", "label": product_by_type.get("mft", {"short": "MFT"})["short"], "kind": "product", "product_type": "mft", "x": 300, "y": 350, "variant": "active"},
                {"id": "core", "label": product_by_type.get("core", {"short": "Core"})["short"], "kind": "product", "product_type": "core", "x": 520, "y": 350, "variant": "active"},
                {"id": "approved", "label": "Approved\nExchange", "kind": "process", "x": 735, "y": 350},
                {"id": "secure", "label": "Secure\nEnvironment", "kind": "zone", "x": 950, "y": 330},
            ]
        )
        flows.extend(
            [
                {"from": "supplier", "to": "incoming", "role": "ingress", "label": "upload", "glyph": "file_red"},
                {"from": "incoming", "to": "mft", "role": "primary", "label": "transfer"},
                {"from": "mft", "to": "core", "role": "primary", "label": "inspect", "sync": True},
                {"from": "core", "to": "approved", "role": "egress", "label": "clean", "glyph": "file_green"},
                {"from": "approved", "to": "secure", "role": "egress", "label": "deliver", "glyph": "file_green"},
                {"from": "core", "to": "quarantine", "role": "ingress", "label": "blocked", "indicator": "quarantine"},
            ]
        )
        nodes.append({"id": "quarantine", "label": "Quarantine", "kind": "quarantine", "x": 520, "y": 485})

    custom_nodes = payload.get("nodes")
    custom_flows = payload.get("flows")
    if isinstance(custom_nodes, list) and custom_nodes:
        nodes = [node for node in custom_nodes if isinstance(node, dict)]
    if isinstance(custom_flows, list) and custom_flows:
        flows = [flow for flow in custom_flows if isinstance(flow, dict)]

    return {
        "schema_version": "0.1",
        "theme": "opswat-2023-light-flow",
        "canvas": {"width": WIDTH, "height": HEIGHT},
        "title": truncate(title, 82),
        "subtitle": subtitle,
        "account_name": account_name,
        "pattern": pattern,
        "include_purdue": include_purdue,
        "zones": payload.get("zones") or ZONE_LABELS,
        "products": products,
        "nodes": nodes,
        "flows": flows,
        "context": {
            "overview": truncate(use_case.get("business_value") or use_case.get("overview") or "", 280),
            "trigger": truncate(use_case.get("account_trigger") or use_case.get("problem") or "", 280),
        },
    }


def node_center(node: dict[str, Any]) -> tuple[int, int]:
    return int(node["x"]) + 48, int(node["y"]) + 34


def draw_generated_icon(product_type_value: str, x: int, y: int) -> str:
    # Small isometric icon inspired by the OPSWAT diagram source decks.
    colors = {
        "core": ("#09A9FF", "#0048D9"),
        "kiosk": ("#23B7FF", "#0B57D0"),
        "mft": ("#19A6FF", "#063EA9"),
        "diode": ("#10D4FF", "#1242A6"),
        "drive": ("#2571FB", "#03E7F5"),
        "firewall": ("#2571FB", "#FF003C"),
        "email": ("#03E7F5", "#2571FB"),
        "product": ("#2571FB", "#03E7F5"),
    }
    c1, c2 = colors.get(product_type_value, colors["product"])
    return f"""
      <g transform="translate({x},{y})">
        <polygon points="18,0 36,9 18,18 0,9" fill="{c1}" stroke="#7FE7FF" stroke-width="1"/>
        <polygon points="0,9 18,18 18,39 0,29" fill="{c2}" stroke="#1257D8" stroke-width="1"/>
        <polygon points="36,9 18,18 18,39 36,28" fill="#082F88" stroke="#1257D8" stroke-width="1"/>
        <path d="M8 9 L18 14 L28 9 M12 6 L22 11" stroke="#B9F7FF" stroke-width="1" fill="none"/>
      </g>
    """


def draw_product_icon(product_type_value: str, x: int, y: int, node_id: str) -> str:
    uri = product_icon_uri(product_type_value)
    if not uri:
        return draw_generated_icon(product_type_value, x, y)
    clip_id = f"clip-{re.sub(r'[^a-zA-Z0-9_-]', '-', node_id)}"
    filter_attr = ' filter="url(#kioskRed)"' if product_type_value == "kiosk" else ""
    # The source PNGs include product captions underneath the artwork. Render
    # them larger and clip to the upper art region so the diagram uses the
    # official object art while keeping labels controlled by the diagram.
    return f"""
      <clipPath id="{clip_id}"><rect x="{x}" y="{y}" width="48" height="40" rx="2"/></clipPath>
      <image href="{uri}" x="{x-20}" y="{y-6}" width="88" height="88" preserveAspectRatio="xMidYMin meet" clip-path="url(#{clip_id})"{filter_attr}/>
    """


def draw_utility_icon(kind: str, x: int, y: int, size: int = 34) -> str:
    uri = utility_icon_uri(kind)
    if not uri:
        return ""
    return f'<image href="{uri}" x="{x}" y="{y}" width="{size}" height="{size}" preserveAspectRatio="xMidYMid meet"/>'


def draw_line_icon(kind: str, x: int, y: int) -> str:
    stroke = THEME["near_black"]
    if kind == "server-rack":
        return f'<g class="entity-icon"><rect x="{x}" y="{y}" width="26" height="30" rx="2"/><line x1="{x+5}" y1="{y+9}" x2="{x+21}" y2="{y+9}"/><line x1="{x+5}" y1="{y+18}" x2="{x+21}" y2="{y+18}"/><circle cx="{x+8}" cy="{y+24}" r="1.5"/></g>'
    if kind == "monitor":
        return f'<g class="entity-icon"><rect x="{x}" y="{y}" width="30" height="21" rx="2"/><line x1="{x+15}" y1="{y+21}" x2="{x+15}" y2="{y+29}"/><line x1="{x+7}" y1="{y+29}" x2="{x+23}" y2="{y+29}"/></g>'
    if kind == "folder-lock":
        return f'<g class="entity-icon"><path d="M{x} {y+10} h10 l4 5 h20 v19 h-34 z"/><rect x="{x+18}" y="{y+20}" width="10" height="9" rx="1"/><path d="M{x+20} {y+20} v-4 c0-6 6-6 6 0 v4"/></g>'
    if kind == "remote-access":
        return f'<g class="entity-icon"><rect x="{x}" y="{y}" width="30" height="22" rx="2"/><path d="M{x+8} {y+30} h14 M{x+15} {y+22} v8"/><path d="M{x+10} {y+11} h10 M{x+17} {y+8} l4 3 -4 3"/></g>'
    if kind == "bank":
        return f'<g class="entity-icon"><path d="M{x} {y+13} l16-10 16 10 z"/><line x1="{x+4}" y1="{y+13}" x2="{x+4}" y2="{y+31}"/><line x1="{x+12}" y1="{y+13}" x2="{x+12}" y2="{y+31}"/><line x1="{x+20}" y1="{y+13}" x2="{x+20}" y2="{y+31}"/><line x1="{x+28}" y1="{y+13}" x2="{x+28}" y2="{y+31}"/><line x1="{x}" y1="{y+31}" x2="{x+32}" y2="{y+31}"/></g>'
    if kind == "people":
        return f'<g class="entity-icon"><circle cx="{x+11}" cy="{y+10}" r="6"/><circle cx="{x+23}" cy="{y+12}" r="5"/><path d="M{x} {y+31} c2-12 20-12 22 0"/><path d="M{x+16} {y+31} c1-8 14-8 16 0"/></g>'
    return f'<g class="entity-icon"><circle cx="{x+16}" cy="{y+10}" r="7"/><path d="M{x+3} {y+32} c2-14 24-14 27 0"/></g>'


def draw_node(node: dict[str, Any]) -> str:
    x = int(node["x"])
    y = int(node["y"])
    label = esc(node.get("label", ""))
    kind = node.get("kind", "process")
    if kind == "product":
        product_type_value = node.get("product_type", "product")
        variant = node.get("variant", "default")
        fill = THEME["active_blue"] if variant == "active" else THEME["navy"]
        stroke = THEME["active_blue"] if variant == "active" else "#16243A"
        glow = ""
        if variant == "active":
            glow = f'<rect x="{x-7}" y="{y-7}" width="110" height="82" rx="12" fill="none" stroke="{THEME["active_blue"]}" stroke-width="3" filter="url(#blueGlow)"/>'
        if product_type_value == "kiosk":
            fill = "#2A1010" if variant != "active" else "#1E6BFF"
            stroke = THEME["kiosk_red"]
        return f"""
          {glow}
          <rect x="{x}" y="{y}" width="96" height="68" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="1.5"/>
          {draw_product_icon(product_type_value, x + 24, y + 8, node.get("id", "product"))}
          <text x="{x+48}" y="{y+58}" text-anchor="middle" class="node-label light">{label}</text>
        """
    if kind == "actor":
        inactive = node.get("variant") == "inactive"
        box_class = "actor-box inactive" if inactive else "actor-box"
        label_class = "tiny inactive-text" if inactive else "tiny"
        icon_kind = node.get("icon", "people")
        return f"""
          <rect x="{x}" y="{y}" width="82" height="64" rx="6" class="{box_class}"/>
          {draw_line_icon(icon_kind, x + 25, y + 8)}
          <text x="{x+41}" y="{y+54}" text-anchor="middle" class="{label_class}">{label}</text>
        """
    if kind == "source":
        utility_kind = "media" if "media" in label.lower() or "usb" in label.lower() else "file"
        return f"""
          <rect x="{x}" y="{y}" width="82" height="58" rx="4" class="source-box"/>
          {draw_utility_icon(utility_kind, x + 24, y + 9, 34)}
          <text x="{x+41}" y="{y+50}" text-anchor="middle" class="tiny">{label}</text>
        """
    if kind == "zone":
        return f"""
          <rect x="{x}" y="{y}" width="160" height="112" rx="8" class="zone-box"/>
          <text x="{x+80}" y="{y+28}" text-anchor="middle" class="node-label">{label}</text>
          <g transform="translate({x+24},{y+48})" opacity="0.82">
            <rect width="28" height="24" class="mini-box"/><text x="14" y="39" text-anchor="middle" class="tiny">API</text>
            <rect x="48" width="28" height="24" class="mini-box"/><text x="62" y="39" text-anchor="middle" class="tiny">SFTP</text>
            <rect x="96" width="28" height="24" class="mini-box"/><text x="110" y="39" text-anchor="middle" class="tiny">GUI</text>
          </g>
        """
    if kind == "quarantine":
        return f"""
          <rect x="{x}" y="{y}" width="112" height="64" rx="6" class="process-box"/>
          <g transform="translate({x+16},{y+16})">
            <circle cx="12" cy="12" r="8" fill="{THEME["red"]}" opacity="0.16"/>
            <path d="M12 4 v16 M4 12 h16 M6 6 l12 12 M18 6 l-12 12" stroke="{THEME["red"]}" stroke-width="1.5"/>
          </g>
          <text x="{x+66}" y="{y+36}" text-anchor="middle" class="node-label">Quarantine</text>
        """
    return f"""
      <rect x="{x}" y="{y}" width="96" height="58" rx="4" class="process-box"/>
      <text x="{x+48}" y="{y+34}" text-anchor="middle" class="node-label">{label}</text>
    """


def draw_flow(flow: dict[str, Any], nodes_by_id: dict[str, dict[str, Any]]) -> str:
    source = nodes_by_id.get(flow["from"])
    target = nodes_by_id.get(flow["to"])
    if not source or not target:
        return ""
    sx, sy = node_center(source)
    tx, ty = node_center(target)
    color = flow_color(flow)
    marker_id = flow_marker_id(flow)
    mid_x = (sx + tx) // 2
    label = esc(flow.get("label", ""))
    path = f"M {sx} {sy} L {mid_x} {sy} L {mid_x} {ty} L {tx} {ty}"
    label_y = sy - 8 if sy <= ty else ty - 8
    glyph = ""
    if flow.get("glyph"):
        glyph_key = str(flow["glyph"])
        glyph_x = mid_x - 10
        glyph_y = ((sy + ty) // 2) - 10
        glyph = draw_utility_icon(glyph_key, glyph_x, glyph_y, 20)
    sync = ""
    if flow.get("sync"):
        sync = f"""
          <g transform="translate({mid_x-12},{((sy+ty)//2)-12})">
            <circle cx="12" cy="12" r="11" fill="#FFFFFF" stroke="{FLOW_ROLE_COLORS['sync']}" stroke-width="1.3"/>
            <path d="M7 11 c1-5 8-6 11-2 l2 2 M17 13 c-1 5-8 6-11 2 l-2-2" fill="none" stroke="{FLOW_ROLE_COLORS['sync']}" stroke-width="1.5" stroke-linecap="round"/>
          </g>
        """
    quarantine = ""
    if flow.get("indicator") == "quarantine":
        quarantine = f"""
          <g transform="translate({mid_x+12},{label_y-20})">
            <circle cx="7" cy="7" r="6" fill="{THEME["red"]}" opacity="0.16"/>
            <path d="M7 1 v12 M1 7 h12 M3 3 l8 8 M11 3 l-8 8" stroke="{THEME["red"]}" stroke-width="1.2"/>
          </g>
        """
    return f"""
      <path d="{path}" fill="none" stroke="{color}" stroke-width="2" marker-end="url(#{marker_id})"/>
      {glyph}
      {sync}
      {quarantine}
      <text x="{mid_x}" y="{label_y}" text-anchor="middle" class="flow-label" fill="{color}">{label}</text>
    """


def render_svg(spec: dict[str, Any]) -> str:
    zones = spec.get("zones", [])
    nodes = spec.get("nodes", [])
    nodes_by_id = {node["id"]: node for node in nodes}
    title_lines = wrap_words(spec.get("title"), 44, 2)
    title_font = 34 if len(title_lines) == 1 else 28
    title_svg = "\n".join(
        f'<tspan x="16" dy="{0 if index == 0 else 36}">{esc(line)}</tspan>' for index, line in enumerate(title_lines)
    )
    guide_y = 124 if len(title_lines) == 1 else 150
    zone_top = guide_y + 18
    zone_lines = []
    for zone in zones:
        x = int(zone.get("x", 0))
        label = esc(zone.get("label", ""))
        color = esc(zone.get("color") or THEME["near_black"])
        zone_lines.append(f'<line x1="{x}" y1="{zone_top}" x2="{x}" y2="650" class="zone-line"/>')
        zone_lines.append(f'<text x="{x+6}" y="{zone_top+10}" class="zone-label" fill="{color}">{label}</text>')

    purdue = ""
    if spec.get("include_purdue"):
        purdue = f"""
          <line x1="150" y1="650" x2="1138" y2="650" class="purdue-line"/>
          <text x="154" y="666" class="purdue-label">Purdue</text>
          <text x="520" y="666" class="purdue-label green">Level 4</text>
          <text x="744" y="666" class="purdue-label orange">Level 3.5</text>
          <text x="1015" y="666" class="purdue-label red">Level 2</text>
        """

    flows = "\n".join(draw_flow(flow, nodes_by_id) for flow in spec.get("flows", []))
    rendered_nodes = "\n".join(draw_node(node) for node in nodes)
    account = spec.get("account_name")
    account_text = f'<text x="1240" y="44" text-anchor="end" class="account">{esc(account)}</text>' if account else ""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">
  <defs>
    <filter id="glow" x="-30%" y="-30%" width="160%" height="160%">
      <feGaussianBlur stdDeviation="4" result="coloredBlur"/>
      <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="blueGlow" x="-40%" y="-40%" width="180%" height="180%">
      <feGaussianBlur stdDeviation="6" flood-color="{THEME["active_blue"]}" result="coloredBlur"/>
      <feMerge><feMergeNode in="coloredBlur"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="kioskRed" color-interpolation-filters="sRGB">
      <feColorMatrix type="matrix" values="0.55 0.05 0.05 0 0.24  0.10 0.04 0.04 0 0.03  0.08 0.03 0.03 0 0.03  0 0 0 1 0"/>
    </filter>
    <marker id="ingress-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="{FLOW_ROLE_COLORS["ingress"]}"/></marker>
    <marker id="egress-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="{FLOW_ROLE_COLORS["egress"]}"/></marker>
    <marker id="primary-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="{FLOW_ROLE_COLORS["primary"]}"/></marker>
    <marker id="sync-arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" fill="{FLOW_ROLE_COLORS["sync"]}"/></marker>
    <style>
      @font-face {{ font-family: 'Simplon Norm'; src: local('Simplon Norm'); }}
      svg {{ background: {THEME["white"]}; font-family: 'Simplon Norm', Arial, sans-serif; }}
      .subtitle {{ font-size: 17px; letter-spacing: 10px; fill: {THEME["blue"]}; }}
      .title {{ font-size: {title_font}px; font-weight: 800; fill: {THEME["ink"]}; }}
      .account {{ font-size: 14px; font-weight: 700; fill: {THEME["ink"]}; }}
      .zone-label {{ font-size: 10.5px; font-weight: 800; letter-spacing: 0; }}
      .zone-line {{ stroke: {THEME["line"]}; stroke-width: 1; stroke-dasharray: 4 4; }}
      .purdue-line {{ stroke: {THEME["line"]}; stroke-width: 1; }}
      .purdue-label {{ font-size: 9px; fill: {THEME["muted"]}; }}
      .purdue-label.green {{ fill: {THEME["zone_green"]}; }}
      .purdue-label.orange {{ fill: {THEME["orange"]}; }}
      .purdue-label.red {{ fill: {THEME["zone_red"]}; }}
      .actor-box,.source-box,.process-box {{ fill: {THEME["white"]}; stroke: #9AA7B8; stroke-width: 1; }}
      .actor-box.inactive {{ fill: {THEME["inactive"]}; stroke: #D1D5DB; }}
      .source-box {{ fill: {THEME["soft"]}; }}
      .zone-box {{ fill: none; stroke: #8EA2BF; stroke-width: 1.3; stroke-dasharray: 8 6; }}
      .mini-box {{ fill: {THEME["white"]}; stroke: #9AA7B8; stroke-width: 1; }}
      .node-label {{ font-size: 12px; font-weight: 700; fill: {THEME["ink"]}; white-space: pre; }}
      .node-label.light {{ fill: {THEME["white"]}; }}
      .tiny {{ font-size: 8px; fill: {THEME["ink"]}; white-space: pre; }}
      .inactive-text {{ fill: {THEME["inactive_text"]}; }}
      .flow-label {{ font-size: 8px; font-weight: 700; }}
      .line-icon,.icon-path {{ fill: none; stroke: {THEME["ink"]}; stroke-width: 1.2; }}
      .entity-icon * {{ fill: none; stroke: {THEME["near_black"]}; stroke-width: 1.2; stroke-linecap: round; stroke-linejoin: round; }}
    </style>
  </defs>
  <rect x="0" y="0" width="{WIDTH}" height="{HEIGHT}" fill="{THEME["white"]}"/>
  <text x="16" y="32" class="subtitle">{esc(spec.get("subtitle"))}</text>
  <text x="16" y="72" class="title">{title_svg}</text>
  {account_text}
  <line x1="40" y1="{guide_y}" x2="1240" y2="{guide_y}" stroke="{THEME["line"]}" stroke-width="1"/>
  {"".join(zone_lines)}
  {purdue}
  {flows}
  {rendered_nodes}
  <circle cx="1262" cy="674" r="7" fill="none" stroke="{THEME["ink"]}" stroke-width="1"/>
</svg>
"""


def generate_diagram(payload: dict[str, Any]) -> DiagramArtifact:
    spec = build_spec(payload)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    identity = " ".join(str(part) for part in [spec.get("account_name"), spec.get("title")] if part)
    diagram_id = f"{slugify(identity or 'diagram')}-{stamp}"
    svg = render_svg(spec)
    return DiagramArtifact(diagram_id=diagram_id, spec=spec, svg=svg)


def write_diagram(artifact: DiagramArtifact, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{artifact.diagram_id}.json"
    svg_path = output_dir / f"{artifact.diagram_id}.svg"
    json_path.write_text(json.dumps(artifact.spec, indent=2), encoding="utf-8")
    svg_path.write_text(artifact.svg, encoding="utf-8")
    return json_path, svg_path

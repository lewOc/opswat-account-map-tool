"""Prompt template loading for staged v2 generation."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional


PROMPT_DIR = Path(__file__).resolve().parent / "pipeline" / "prompts"


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def short_hash(self) -> str:
        return self.sha256[:12]

    def render(self, values: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> str:
        context = dict(values or {})
        context.update(kwargs)
        return self.text.format(**context)


def load_prompt(name: str, prompt_dir: Path = PROMPT_DIR) -> PromptTemplate:
    safe_name = name.removesuffix(".md")
    if "/" in safe_name or "\\" in safe_name or safe_name.startswith("."):
        raise ValueError(f"Unsafe prompt name: {name}")
    path = prompt_dir / f"{safe_name}.md"
    return PromptTemplate(name=safe_name, text=path.read_text(encoding="utf-8"))

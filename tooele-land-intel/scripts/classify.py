"""
Classify agenda-item paragraphs by type, and extract structured fields
(parcel IDs, addresses, acreage, density). Pure regex/keyword — no LLM
in the cron path so it's free and deterministic.

The downstream Claude Skill does the *interesting* analysis on the
already-structured CSV; this layer just normalizes the input.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import yaml


@dataclass
class ExtractedItem:
    label: str
    raw_text: str
    parcels: list[str] = field(default_factory=list)
    addresses: list[str] = field(default_factory=list)
    acreage: float | None = None
    density_du_per_acre: float | None = None


def load_config(path: str = "data/jurisdictions.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def classify(text: str, rules: list[dict]) -> str:
    lowered = text.lower()
    for rule in rules:
        if any(kw.lower() in lowered for kw in rule["keywords"]):
            return rule["label"]
    return "other"


def extract(text: str, patterns: dict) -> dict:
    parcels = re.findall(patterns["parcel_pattern"], text)
    addresses = re.findall(patterns["address_pattern"], text, flags=re.IGNORECASE)
    acreage_match = re.search(patterns["acreage_pattern"], text, flags=re.IGNORECASE)
    density_match = re.search(patterns["density_pattern"], text, flags=re.IGNORECASE)
    return {
        "parcels": list(dict.fromkeys(parcels)),         # preserve order, dedupe
        "addresses": list(dict.fromkeys(addresses)),
        "acreage": float(acreage_match.group(1)) if acreage_match else None,
        "density_du_per_acre": float(density_match.group(1)) if density_match else None,
    }


def split_into_items(agenda_text: str) -> list[str]:
    """
    Heuristic split: most agendas number their items. This regex catches
    forms like '1.', '1)', 'Item 1:', 'A.', 'Action Item 1', etc. Items
    less than 30 chars are dropped (likely page numbers / headers).
    """
    chunks = re.split(
        r"\n\s*(?:\d+\.|\d+\)|Item\s+\d+|Action\s+Item\s+\d+|[A-Z]\.\s)",
        agenda_text,
    )
    return [c.strip() for c in chunks if len(c.strip()) > 30]

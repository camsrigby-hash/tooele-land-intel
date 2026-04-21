"""Classify agenda item text by development type."""

import re

# Pattern sets for each category
_PATTERNS: list[tuple[str, list[str]]] = [
    ("residential_subdivision", [
        r"\bsubdivision\b", r"\bplat\b", r"\bpreliminary plat\b", r"\bfinal plat\b",
        r"\blot split\b", r"\bduplicate lot\b",
    ]),
    ("residential_density", [
        r"\bpud\b", r"\bplanned unit development\b", r"\bmulti.?family\b",
        r"\bapartment\b", r"\btownhome\b", r"\btownhouse\b", r"\bcondominium\b",
        r"\bdensity\b", r"\bdwelling unit\b",
    ]),
    ("commercial", [
        r"\bcommercial\b", r"\bretail\b", r"\bshopping\b", r"\bstrip mall\b",
        r"\boffice\b", r"\bhotel\b", r"\bmotel\b", r"\brestaurant\b",
    ]),
    ("industrial", [
        r"\bindustrial\b", r"\bwarehouse\b", r"\bmanufacturing\b", r"\blight industrial\b",
        r"\bheavy industrial\b", r"\bstorage facility\b",
    ]),
    ("mixed_use", [
        r"\bmixed.?use\b", r"\bmixed use\b",
    ]),
    ("rezone", [
        r"\brezone\b", r"\brezoning\b", r"\bzone change\b", r"\bamendment to.*zoning\b",
        r"\bzoning map amendment\b",
    ]),
    ("general_plan_amendment", [
        r"\bgeneral plan\b", r"\bmaster plan\b", r"\bcomprehensive plan\b",
        r"\bfuture land use\b", r"\bgeneral plan amendment\b",
    ]),
    ("conditional_use", [
        r"\bconditional use\b", r"\bspecial use\b", r"\bvariance\b",
        r"\bspecial exception\b",
    ]),
    ("annexation", [
        r"\bannexation\b", r"\bde-?annexation\b",
    ]),
    ("infrastructure", [
        r"\bstreet\b", r"\broad\b", r"\butility\b", r"\bsewer\b", r"\bwater line\b",
        r"\bstorm drain\b", r"\btrail\b",
    ]),
    ("site_plan", [
        r"\bsite plan\b", r"\bsite development\b", r"\bbuilding permit\b",
    ]),
]


def classify_agenda_item(text: str) -> dict:
    """Return {type, confidence, matched_patterns} for a given text snippet."""
    if not text:
        return {"type": "other", "confidence": 0.0, "matched_patterns": []}

    lower = text.lower()
    scores: dict[str, int] = {}
    matched: dict[str, list[str]] = {}

    for category, patterns in _PATTERNS:
        hits = [p for p in patterns if re.search(p, lower)]
        if hits:
            scores[category] = len(hits)
            matched[category] = hits

    if not scores:
        return {"type": "other", "confidence": 0.0, "matched_patterns": []}

    best = max(scores, key=lambda k: scores[k])
    total_patterns = sum(len(p) for _, p in _PATTERNS if _ == best)
    confidence = min(1.0, scores[best] / max(total_patterns, 1))

    return {
        "type": best,
        "confidence": round(confidence, 2),
        "matched_patterns": matched[best],
    }

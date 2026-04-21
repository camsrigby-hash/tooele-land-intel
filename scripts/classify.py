"""Classify agenda item text by development type.

Design notes
------------
The classifier is intentionally called only on agenda item *titles* and *body text*,
never on bare meeting-header lines (address, date, roll-call preamble).  To defend
against stray address text that slips in anyway, two guards are applied before
matching:

1. ``_strip_address_lines()`` removes common civic meeting header patterns
   ("The [body] will hold a meeting at 429 East Main Street...").
2. Infrastructure keywords (street, road) require accompanying action words
   (improvement, project, extension, etc.) so that an address like "429 East
   Main Street" does not trigger the infrastructure category.
"""

import re

# ── Pre-processing: strip meeting-header / address boilerplate ────────────────

# "The [body] will hold a [meeting] on [date] at/in [address], [City], UT..."
_HEADER_RE = re.compile(
    r'(?:the\s+\w[\w\s]*(?:council|commission|board|city|county)\s+will\s+hold'
    r'|public\s+notice\s*:?'
    r'|notice\s+of\s+(?:annual|regular|special)\s+meeting'
    r'|roll\s+call'
    r')\b.*?(?:\n|$)',
    re.I,
)

# Street addresses: optional number, optional direction, word, Street|Road|Ave etc.
_ADDRESS_RE = re.compile(
    r'\b\d*\s*(?:north|south|east|west|n\.?|s\.?|e\.?|w\.?|ne|nw|se|sw)\s+'
    r'[\w\s]{1,30}(?:street|road|avenue|boulevard|drive|lane|way|blvd|ave|dr|rd)\b'
    r'[^.;\n]*',
    re.I,
)

# "at/in [number] [word(s)] [Street|Road...]"
_AT_ADDRESS_RE = re.compile(
    r'\b(?:at|in|held\s+at|located\s+at)\s+\d+\s+[\w\s]{1,30}'
    r'(?:street|road|avenue|boulevard|blvd|ave|dr|rd|lane|way)\b'
    r'[^.;\n]*',
    re.I,
)


def _strip_boilerplate(text: str) -> str:
    """Remove meeting-header and address lines from text before classifying."""
    text = _HEADER_RE.sub(' ', text)
    text = _AT_ADDRESS_RE.sub(' ', text)
    text = _ADDRESS_RE.sub(' ', text)
    return text


# ── Pattern sets ──────────────────────────────────────────────────────────────

_PATTERNS: list[tuple[str, list[str]]] = [
    ("residential_subdivision", [
        r"\bsubdivision\b",
        r"\bpreliminary plat\b",
        r"\bfinal plat\b",
        r"\bplat\b",
        r"\blot split\b",
        r"\bduplicate lot\b",
    ]),
    ("residential_density", [
        r"\bpud\b",
        r"\bplanned unit development\b",
        r"\bmulti.?family\b",
        r"\bapartment\b",
        r"\btownhome\b",
        r"\btownhouse\b",
        r"\bcondominium\b",
        r"\bdensity\b",
        r"\bdwelling unit\b",
    ]),
    ("commercial", [
        r"\bcommercial\b",
        r"\bretail\b",
        r"\bshopping\b",
        r"\bstrip mall\b",
        r"\boffice\b",
        r"\bhotel\b",
        r"\bmotel\b",
        r"\brestaurant\b",
    ]),
    ("industrial", [
        r"\bindustrial\b",
        r"\bwarehouse\b",
        r"\bmanufacturing\b",
        r"\blight industrial\b",
        r"\bheavy industrial\b",
        r"\bstorage facility\b",
    ]),
    ("mixed_use", [
        r"\bmixed.?use\b",
    ]),
    ("rezone", [
        r"\brezone\b",
        r"\brezoning\b",
        r"\bzone change\b",
        r"\bamendment to.*zoning\b",
        r"\bzoning map amendment\b",
    ]),
    ("general_plan_amendment", [
        r"\bgeneral plan\b",
        r"\bmaster plan\b",
        r"\bcomprehensive plan\b",
        r"\bfuture land use\b",
        r"\bgeneral plan amendment\b",
    ]),
    ("conditional_use", [
        r"\bconditional use\b",
        r"\bspecial use\b",
        r"\bvariance\b",
        r"\bspecial exception\b",
    ]),
    ("annexation", [
        r"\bannexation\b",
        r"\bde-?annexation\b",
    ]),
    ("infrastructure", [
        # Require "street/road" alongside a project/action word to avoid
        # matching bare addresses like "429 East Main Street, Grantsville UT"
        r"\bstreet\s+(?:improvement|project|extension|construction|widening|repair|maintenance)\b",
        r"\broad\s+(?:improvement|project|extension|construction|widening|repair|maintenance)\b",
        r"\butility\s+(?:project|extension|improvement|line|easement)\b",
        r"\bsewer\b",
        r"\bwater\s+(?:line|main|system|extension|project)\b",
        r"\bstorm\s*drain\b",
        r"\btrail\s+(?:project|extension|improvement|plan)\b",
    ]),
    ("site_plan", [
        r"\bsite plan\b",
        r"\bsite development\b",
        r"\bbuilding permit\b",
    ]),
]


def classify_agenda_item(text: str) -> dict:
    """Return {type, confidence, matched_patterns} for a given text snippet.

    Strips meeting-header boilerplate and address lines before matching so
    that agenda items aren't mis-tagged as 'infrastructure' because their
    notice text includes a street address.
    """
    if not text:
        return {"type": "other", "confidence": 0.0, "matched_patterns": []}

    cleaned = _strip_boilerplate(text)
    lower = cleaned.lower()

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
    # Confidence = fraction of category's patterns that matched
    cat_patterns = next(pats for cat, pats in _PATTERNS if cat == best)
    confidence = min(1.0, scores[best] / max(len(cat_patterns), 1))

    return {
        "type": best,
        "confidence": round(confidence, 2),
        "matched_patterns": matched[best],
    }

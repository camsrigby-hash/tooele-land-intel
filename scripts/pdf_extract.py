"""Extract text from agenda PDFs (by URL or local path)."""

import io
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import pdfplumber
import requests

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "TooeleLandIntel/1.0"})
TIMEOUT = 30


def extract_pdf_text(source: str, max_pages: int = 10) -> Optional[str]:
    """
    Extract text from a PDF given a URL or local file path.
    Returns concatenated text or None on failure.
    """
    try:
        if urlparse(source).scheme in ("http", "https"):
            resp = SESSION.get(source, timeout=TIMEOUT)
            resp.raise_for_status()
            pdf_bytes = io.BytesIO(resp.content)
        else:
            pdf_bytes = open(source, "rb")

        with pdfplumber.open(pdf_bytes) as pdf:
            pages = pdf.pages[:max_pages]
            texts = [p.extract_text() or "" for p in pages]
        return "\n".join(texts).strip() or None

    except Exception as e:
        print(f"  [WARN] PDF extraction failed for {source}: {e}", file=sys.stderr)
        return None


def extract_pdf_tables(source: str, max_pages: int = 10) -> list[list[list]]:
    """Extract tables from a PDF, returning list-of-tables (each table is list of rows)."""
    try:
        if urlparse(source).scheme in ("http", "https"):
            resp = SESSION.get(source, timeout=TIMEOUT)
            resp.raise_for_status()
            pdf_bytes = io.BytesIO(resp.content)
        else:
            pdf_bytes = open(source, "rb")

        tables = []
        with pdfplumber.open(pdf_bytes) as pdf:
            for page in pdf.pages[:max_pages]:
                page_tables = page.extract_tables()
                tables.extend(page_tables)
        return tables

    except Exception as e:
        print(f"  [WARN] Table extraction failed for {source}: {e}", file=sys.stderr)
        return []

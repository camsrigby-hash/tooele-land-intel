"""
Pull text from agenda PDFs. We use pypdf (pure Python, no system deps) so
the GitHub Action stays light. For scanned PDFs we'd need OCR — out of scope
for v1 since municipal agendas are almost always text-PDFs.
"""
from __future__ import annotations

import io

import requests
from pypdf import PdfReader


def fetch_pdf_text(url: str, timeout: int = 30) -> str:
    """Download a PDF and return its concatenated page text."""
    r = requests.get(url, timeout=timeout, headers={
        "User-Agent": "tooele-land-intel/1.0 (+https://github.com)"
    })
    r.raise_for_status()
    reader = PdfReader(io.BytesIO(r.content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)

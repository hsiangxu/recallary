from __future__ import annotations

import math
import re
from collections import Counter
from pathlib import Path

import pymupdf

from recallary.domain import ParsedPage, ParsedPaper


class NoTextError(RuntimeError):
    pass


_WHITESPACE = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n{3,}")
_HYPHENATED_LINE_BREAK = re.compile(r"(?<=[A-Za-z])-\s*\n\s*(?=[a-z])")
_SIMPLE_LINE_BREAK = re.compile(r"(?<![.!?:;])\n(?=[a-z(])")
_PAGE_NUMBER = re.compile(
    r"^\s*(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,8})\s*$", re.IGNORECASE
)


def _normalize_repeated_line(line: str) -> str:
    return re.sub(r"\d+", "#", _WHITESPACE.sub(" ", line.strip().lower()))


def _extract_block_text(page: pymupdf.Page) -> str:
    blocks = page.get_text("blocks", sort=True)
    text_parts: list[str] = []
    for block in blocks:
        if len(block) < 7 or int(block[6]) != 0:
            continue
        text = str(block[4]).strip()
        if text:
            text_parts.append(text)
    return "\n\n".join(text_parts)


def _repeated_margin_lines(raw_pages: list[str]) -> set[str]:
    candidates: Counter[str] = Counter()
    pages_with_text = 0
    for text in raw_pages:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        pages_with_text += 1
        page_candidates = {
            _normalize_repeated_line(line)
            for line in (lines[:2] + lines[-2:])
            if len(line) <= 160
        }
        candidates.update(page_candidates)

    threshold = max(2, math.ceil(pages_with_text * 0.5))
    return {line for line, count in candidates.items() if count >= threshold}


def clean_page_text(text: str, repeated_lines: set[str]) -> str:
    kept_lines: list[str] = []
    for line in text.splitlines():
        stripped = _WHITESPACE.sub(" ", line).strip()
        if not stripped:
            kept_lines.append("")
            continue
        if _normalize_repeated_line(stripped) in repeated_lines:
            continue
        if _PAGE_NUMBER.fullmatch(stripped):
            continue
        kept_lines.append(stripped)

    cleaned = "\n".join(kept_lines)
    cleaned = _HYPHENATED_LINE_BREAK.sub("", cleaned)
    cleaned = _SIMPLE_LINE_BREAK.sub(" ", cleaned)
    cleaned = _BLANK_LINES.sub("\n\n", cleaned)
    return cleaned.strip()


def _guess_title(metadata_title: str, first_page: str, fallback: str) -> str:
    title = _WHITESPACE.sub(" ", metadata_title).strip()
    if title and title.lower() not in {"untitled", "none"}:
        return title

    lines = [
        _WHITESPACE.sub(" ", line).strip()
        for line in first_page.splitlines()
        if line.strip()
    ]
    candidates: list[str] = []
    for line in lines[:12]:
        lowered = line.lower()
        if lowered.startswith(("abstract", "doi:", "http://", "https://")):
            break
        if 15 <= len(line) <= 300 and not _PAGE_NUMBER.fullmatch(line):
            candidates.append(line)
        if sum(len(item) for item in candidates) >= 50:
            break
    guessed = " ".join(candidates[:3]).strip()
    return guessed or fallback


def parse_pdf(path: Path) -> ParsedPaper:
    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise RuntimeError("PDF is password-protected.")

        metadata = document.metadata or {}
        raw_pages = [_extract_block_text(page) for page in document]
        repeated_lines = _repeated_margin_lines(raw_pages)
        pages = [
            ParsedPage(
                page_number=index + 1,
                raw_text=raw,
                clean_text=clean_page_text(raw, repeated_lines),
            )
            for index, raw in enumerate(raw_pages)
        ]

        total_text = sum(len(page.clean_text) for page in pages)
        if total_text < 100:
            raise NoTextError(
                "The PDF contains too little extractable text; OCR is not enabled."
            )

        first_page = pages[0].clean_text if pages else ""
        title = _guess_title(
            str(metadata.get("title") or ""),
            first_page,
            path.stem,
        )
        authors = _WHITESPACE.sub(
            " ", str(metadata.get("author") or "")
        ).strip()
        return ParsedPaper(
            title=title,
            authors=authors,
            page_count=len(pages),
            pages=pages,
        )


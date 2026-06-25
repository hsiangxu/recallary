from __future__ import annotations

import re

from recallary.domain import Chunk, ParsedPaper


TARGET_WORDS = 240
OVERLAP_WORDS = 40
MIN_CHUNK_WORDS = 35

_HEADING = re.compile(
    r"^(?:\d+(?:\.\d+)*\s+)?"
    r"(abstract|introduction|background|related work|methods?|methodology|"
    r"materials and methods|experimental setup|experiments?|results?|"
    r"discussion|conclusions?|limitations?|references)\b",
    re.IGNORECASE,
)


def _section_hint(paragraph: str, current: str) -> str:
    first_line = paragraph.splitlines()[0].strip()
    match = _HEADING.match(first_line)
    if match and len(first_line) <= 120:
        return match.group(1).title()
    return current


def _split_long_words(words: list[str]) -> list[list[str]]:
    if len(words) <= TARGET_WORDS:
        return [words]
    pieces: list[list[str]] = []
    start = 0
    while start < len(words):
        end = min(start + TARGET_WORDS, len(words))
        pieces.append(words[start:end])
        if end == len(words):
            break
        start = max(end - OVERLAP_WORDS, start + 1)
    return pieces


def chunk_paper(paper: ParsedPaper) -> list[Chunk]:
    chunks: list[Chunk] = []
    current_section = ""

    for page in paper.pages:
        paragraphs = [
            paragraph.strip()
            for paragraph in re.split(r"\n\s*\n", page.clean_text)
            if paragraph.strip()
        ]
        page_chunks: list[tuple[str, str]] = []
        buffer: list[str] = []
        buffer_words = 0

        def flush() -> None:
            nonlocal buffer, buffer_words
            if not buffer:
                return
            text = "\n\n".join(buffer).strip()
            if text:
                page_chunks.append((current_section, text))
            buffer = []
            buffer_words = 0

        for paragraph in paragraphs:
            current_section = _section_hint(paragraph, current_section)
            words = paragraph.split()
            if len(words) > TARGET_WORDS:
                flush()
                for piece in _split_long_words(words):
                    page_chunks.append((current_section, " ".join(piece)))
                continue

            if buffer and buffer_words + len(words) > TARGET_WORDS:
                previous_words = "\n\n".join(buffer).split()
                flush()
                overlap = previous_words[-OVERLAP_WORDS:]
                if overlap:
                    buffer = [" ".join(overlap)]
                    buffer_words = len(overlap)

            buffer.append(paragraph)
            buffer_words += len(words)
        flush()

        # Keep short pages searchable, but merge tiny trailing chunks where possible.
        if len(page_chunks) >= 2:
            last_section, last_text = page_chunks[-1]
            if len(last_text.split()) < MIN_CHUNK_WORDS:
                previous_section, previous_text = page_chunks[-2]
                page_chunks[-2] = (
                    previous_section or last_section,
                    f"{previous_text}\n\n{last_text}",
                )
                page_chunks.pop()

        for index, (section, text) in enumerate(page_chunks):
            chunks.append(
                Chunk(
                    page_number=page.page_number,
                    chunk_index=index,
                    section_hint=section,
                    text=text,
                )
            )
    return chunks


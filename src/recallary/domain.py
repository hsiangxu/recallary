from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class FileSnapshot:
    path: Path
    relative_path: str
    size: int
    modified_ns: int


@dataclass(frozen=True)
class ParsedPage:
    page_number: int
    raw_text: str
    clean_text: str


@dataclass(frozen=True)
class ParsedPaper:
    title: str
    authors: str
    page_count: int
    pages: list[ParsedPage]


@dataclass(frozen=True)
class Chunk:
    page_number: int
    chunk_index: int
    section_hint: str
    text: str


@dataclass(frozen=True)
class EmbeddedChunk:
    chunk: Chunk
    embedding: np.ndarray


@dataclass
class IndexSummary:
    discovered: int = 0
    indexed: int = 0
    unchanged: int = 0
    metadata_updated: int = 0
    removed: int = 0
    failed: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class SearchEvidence:
    page_number: int
    text: str
    score: float


@dataclass(frozen=True)
class SearchResult:
    paper_id: int
    title: str
    authors: str
    relative_path: str
    score: float
    evidence: list[SearchEvidence]

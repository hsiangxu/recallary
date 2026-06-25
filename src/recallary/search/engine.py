from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from recallary import database
from recallary.config import Settings
from recallary.domain import SearchEvidence, SearchResult
from recallary.indexing.embedder import Embedder


RRF_K = 60
RETRIEVAL_LIMIT = 100


@dataclass(frozen=True)
class _ChunkHit:
    chunk_id: int
    paper_id: int
    page_number: int
    text: str
    title: str
    authors: str
    relative_path: str


def _query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[^\W_][\w.+#/-]*", query.lower(), flags=re.UNICODE)
    return list(dict.fromkeys(token for token in tokens if len(token) > 1))


def _fts_query(query: str) -> str:
    tokens = _query_tokens(query)
    if not tokens:
        return ""
    quoted = ['"' + token.replace('"', '""') + '"' for token in tokens[:30]]
    return " OR ".join(quoted)


def _lexical_hits(
    connection: sqlite3.Connection, query: str
) -> list[_ChunkHit]:
    match = _fts_query(query)
    if not match:
        return []
    rows = connection.execute(
        """
        SELECT
            c.id AS chunk_id,
            c.paper_id,
            c.page_number,
            c.text,
            p.title,
            p.authors,
            p.relative_path,
            bm25(chunks_fts, 0.0, 0.0, 5.0, 8.0, 4.0, 2.0, 1.0)
                AS lexical_score
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.chunk_id
        JOIN papers p ON p.id = c.paper_id
        WHERE chunks_fts MATCH ? AND p.status = 'ready'
        ORDER BY lexical_score ASC
        LIMIT ?
        """,
        (match, RETRIEVAL_LIMIT),
    ).fetchall()
    return [
        _ChunkHit(
            chunk_id=int(row["chunk_id"]),
            paper_id=int(row["paper_id"]),
            page_number=int(row["page_number"]),
            text=str(row["text"]),
            title=str(row["title"]),
            authors=str(row["authors"]),
            relative_path=str(row["relative_path"]),
        )
        for row in rows
    ]


def _semantic_hits(
    connection: sqlite3.Connection,
    query_vector: np.ndarray,
) -> list[_ChunkHit]:
    rows = connection.execute(
        """
        SELECT
            c.id AS chunk_id,
            c.paper_id,
            c.page_number,
            c.text,
            c.embedding,
            c.embedding_dim,
            p.title,
            p.authors,
            p.relative_path
        FROM chunks c
        JOIN papers p ON p.id = c.paper_id
        WHERE p.status = 'ready'
        """
    ).fetchall()
    if not rows:
        return []

    valid_rows: list[sqlite3.Row] = []
    vectors: list[np.ndarray] = []
    for row in rows:
        dimension = int(row["embedding_dim"])
        if dimension != int(query_vector.size):
            continue
        vector = np.frombuffer(row["embedding"], dtype=np.float32)
        if vector.size == dimension:
            valid_rows.append(row)
            vectors.append(vector)
    if not vectors:
        return []

    matrix = np.vstack(vectors)
    similarities = matrix @ query_vector
    count = min(RETRIEVAL_LIMIT, len(similarities))
    if count == len(similarities):
        indices = np.argsort(-similarities)
    else:
        selected = np.argpartition(-similarities, count - 1)[:count]
        indices = selected[np.argsort(-similarities[selected])]

    return [
        _ChunkHit(
            chunk_id=int(valid_rows[index]["chunk_id"]),
            paper_id=int(valid_rows[index]["paper_id"]),
            page_number=int(valid_rows[index]["page_number"]),
            text=str(valid_rows[index]["text"]),
            title=str(valid_rows[index]["title"]),
            authors=str(valid_rows[index]["authors"]),
            relative_path=str(valid_rows[index]["relative_path"]),
        )
        for index in indices
    ]


def _evidence_snippet(text: str, query: str, limit: int = 650) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact

    lowered = compact.lower()
    positions = [
        lowered.find(token)
        for token in _query_tokens(query)
        if lowered.find(token) >= 0
    ]
    center = min(positions) if positions else 0
    start = max(0, center - limit // 3)
    end = min(len(compact), start + limit)
    if start:
        sentence_start = max(
            compact.rfind(". ", 0, start),
            compact.rfind("? ", 0, start),
            compact.rfind("! ", 0, start),
        )
        if sentence_start >= 0:
            start = sentence_start + 2
    snippet = compact[start:end].strip()
    if start > 0:
        snippet = f"...{snippet}"
    if end < len(compact):
        snippet = f"{snippet}..."
    return snippet


def search_library(
    settings: Settings,
    query: str,
    *,
    limit: int = 10,
    embedder: Embedder | None = None,
) -> list[SearchResult]:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")
    if limit < 1:
        raise ValueError("Search limit must be at least 1.")
    if not settings.database_path.is_file():
        raise RuntimeError(
            "Recallary is not initialized. Run `recallary setup` first."
        )

    settings.configure_local_storage()
    active_embedder = embedder or Embedder(settings)
    query_vector = active_embedder.encode_query(cleaned_query)

    with database.connect(settings.database_path) as connection:
        lexical = _lexical_hits(connection, cleaned_query)
        semantic = _semantic_hits(connection, query_vector)

    chunk_scores: defaultdict[int, float] = defaultdict(float)
    chunk_data: dict[int, _ChunkHit] = {}
    source_count: defaultdict[int, int] = defaultdict(int)
    for hits in (lexical, semantic):
        seen: set[int] = set()
        for rank, hit in enumerate(hits, start=1):
            chunk_scores[hit.chunk_id] += 1.0 / (RRF_K + rank)
            chunk_data[hit.chunk_id] = hit
            if hit.chunk_id not in seen:
                source_count[hit.chunk_id] += 1
                seen.add(hit.chunk_id)

    paper_hits: defaultdict[int, list[tuple[float, _ChunkHit]]] = defaultdict(list)
    for chunk_id, score in chunk_scores.items():
        if source_count[chunk_id] == 2:
            score *= 1.08
        hit = chunk_data[chunk_id]
        paper_hits[hit.paper_id].append((score, hit))

    ranked_papers: list[tuple[float, int, list[tuple[float, _ChunkHit]]]] = []
    query_token_set = set(_query_tokens(cleaned_query))
    for paper_id, hits in paper_hits.items():
        hits.sort(key=lambda item: item[0], reverse=True)
        score = hits[0][0]
        used_pages = {hits[0][1].page_number}
        for hit_score, hit in hits[1:]:
            if hit.page_number not in used_pages:
                score += hit_score * 0.45
                used_pages.add(hit.page_number)
                break
        title_tokens = set(_query_tokens(hits[0][1].title))
        if query_token_set:
            score += (
                len(query_token_set & title_tokens) / len(query_token_set)
            ) * 0.005
        ranked_papers.append((score, paper_id, hits))

    ranked_papers.sort(key=lambda item: item[0], reverse=True)
    results: list[SearchResult] = []
    for score, paper_id, hits in ranked_papers[:limit]:
        selected: list[SearchEvidence] = []
        used_pages: set[int] = set()
        for hit_score, hit in hits:
            if hit.page_number in used_pages:
                continue
            selected.append(
                SearchEvidence(
                    page_number=hit.page_number,
                    text=_evidence_snippet(hit.text, cleaned_query),
                    score=hit_score,
                )
            )
            used_pages.add(hit.page_number)
            if len(selected) == 3:
                break
        representative = hits[0][1]
        results.append(
            SearchResult(
                paper_id=paper_id,
                title=representative.title
                or representative.relative_path.rsplit("/", 1)[-1],
                authors=representative.authors,
                relative_path=representative.relative_path,
                score=score,
                evidence=selected,
            )
        )
    return results

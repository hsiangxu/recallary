from __future__ import annotations

import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from recallary import database
from recallary.config import Settings
from recallary.domain import BibTeXInfo, SearchEvidence, SearchResult
from recallary.indexing.embedder import Embedder


RRF_K = 60
RETRIEVAL_LIMIT = 100


@dataclass(frozen=True)
class _EvidenceHit:
    source_type: str
    source_id: int
    paper_id: int
    page_number: int | None
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


def _tag_filter_sql(tag_names: tuple[str, ...], paper_alias: str = "p") -> tuple[str, tuple[object, ...]]:
    clean_tags = tuple(
        dict.fromkeys(
            database.normalize_tag_name(tag)
            for tag in tag_names
            if database.normalize_tag_name(tag)
        )
    )
    if not clean_tags:
        return "", ()
    placeholders = ",".join("?" for _ in clean_tags)
    return (
        f"""
        AND {paper_alias}.id IN (
            SELECT pt.paper_id
            FROM paper_tags pt
            JOIN tags t ON t.id = pt.tag_id
            WHERE t.name IN ({placeholders})
            GROUP BY pt.paper_id
            HAVING COUNT(DISTINCT t.name) = ?
        )
        """,
        (*clean_tags, len(clean_tags)),
    )


def _lexical_hits(
    connection: sqlite3.Connection, query: str, tag_names: tuple[str, ...] = ()
) -> list[_EvidenceHit]:
    match = _fts_query(query)
    if not match:
        return []
    tag_sql, tag_params = _tag_filter_sql(tag_names)
    rows = connection.execute(
        f"""
        SELECT
            c.id AS chunk_id,
            c.paper_id,
            c.page_number,
            c.text,
            COALESCE(NULLIF(p.display_name, ''), p.title) AS title,
            p.authors,
            p.relative_path,
            bm25(chunks_fts, 0.0, 0.0, 5.0, 8.0, 4.0, 2.0, 1.0)
                AS lexical_score
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.chunk_id
        JOIN papers p ON p.id = c.paper_id
        WHERE chunks_fts MATCH ? AND p.status = 'ready'
        {tag_sql}
        ORDER BY lexical_score ASC
        LIMIT ?
        """,
        (match, *tag_params, RETRIEVAL_LIMIT),
    ).fetchall()
    return [
        _EvidenceHit(
            source_type="pdf",
            source_id=int(row["chunk_id"]),
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
    tag_names: tuple[str, ...] = (),
) -> list[_EvidenceHit]:
    tag_sql, tag_params = _tag_filter_sql(tag_names)
    rows = connection.execute(
        f"""
        SELECT
            c.id AS chunk_id,
            c.paper_id,
            c.page_number,
            c.text,
            c.embedding,
            c.embedding_dim,
            COALESCE(NULLIF(p.display_name, ''), p.title) AS title,
            p.authors,
            p.relative_path
        FROM chunks c
        JOIN papers p ON p.id = c.paper_id
        WHERE p.status = 'ready'
        {tag_sql}
        """
        ,
        tag_params,
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
        _EvidenceHit(
            source_type="pdf",
            source_id=int(valid_rows[index]["chunk_id"]),
            paper_id=int(valid_rows[index]["paper_id"]),
            page_number=int(valid_rows[index]["page_number"]),
            text=str(valid_rows[index]["text"]),
            title=str(valid_rows[index]["title"]),
            authors=str(valid_rows[index]["authors"]),
            relative_path=str(valid_rows[index]["relative_path"]),
        )
        for index in indices
    ]


def _note_lexical_hits(
    connection: sqlite3.Connection, query: str, tag_names: tuple[str, ...] = ()
) -> list[_EvidenceHit]:
    match = _fts_query(query)
    if not match:
        return []
    tag_sql, tag_params = _tag_filter_sql(tag_names)
    rows = connection.execute(
        f"""
        SELECT
            n.id AS note_id,
            n.paper_id,
            n.content AS text,
            COALESCE(NULLIF(p.display_name, ''), p.title) AS title,
            p.authors,
            p.relative_path,
            bm25(paper_notes_fts, 4.0, 8.0) AS lexical_score
        FROM paper_notes_fts
        JOIN paper_notes n ON n.id = paper_notes_fts.note_id
        JOIN papers p ON p.id = n.paper_id
        WHERE paper_notes_fts MATCH ? AND p.status = 'ready'
        {tag_sql}
        ORDER BY lexical_score ASC
        LIMIT ?
        """,
        (match, *tag_params, RETRIEVAL_LIMIT),
    ).fetchall()
    return [
        _EvidenceHit(
            source_type="note",
            source_id=int(row["note_id"]),
            paper_id=int(row["paper_id"]),
            page_number=None,
            text=str(row["text"]),
            title=str(row["title"]),
            authors=str(row["authors"]),
            relative_path=str(row["relative_path"]),
        )
        for row in rows
    ]


def _note_semantic_hits(
    connection: sqlite3.Connection,
    query_vector: np.ndarray,
    tag_names: tuple[str, ...] = (),
) -> list[_EvidenceHit]:
    tag_sql, tag_params = _tag_filter_sql(tag_names)
    rows = connection.execute(
        f"""
        SELECT
            n.id AS note_id,
            n.paper_id,
            n.content AS text,
            n.embedding,
            n.embedding_dim,
            COALESCE(NULLIF(p.display_name, ''), p.title) AS title,
            p.authors,
            p.relative_path
        FROM paper_notes n
        JOIN papers p ON p.id = n.paper_id
        WHERE p.status = 'ready'
          AND n.embedding_dim > 0
        {tag_sql}
        """,
        tag_params,
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
        _EvidenceHit(
            source_type="note",
            source_id=int(valid_rows[index]["note_id"]),
            paper_id=int(valid_rows[index]["paper_id"]),
            page_number=None,
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
    tag_names: tuple[str, ...] = (),
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
    database.initialize(settings.database_path)

    settings.configure_local_storage()
    active_embedder = embedder or Embedder(settings)
    query_vector = active_embedder.encode_query(cleaned_query)

    with database.connect(settings.database_path) as connection:
        lexical = _lexical_hits(connection, cleaned_query, tag_names)
        semantic = _semantic_hits(connection, query_vector, tag_names)
        note_lexical = _note_lexical_hits(connection, cleaned_query, tag_names)
        note_semantic = _note_semantic_hits(connection, query_vector, tag_names)
        result_paper_ids = sorted(
            {hit.paper_id for hit in lexical}
            | {hit.paper_id for hit in semantic}
            | {hit.paper_id for hit in note_lexical}
            | {hit.paper_id for hit in note_semantic}
        )
        tags_by_paper = database.tags_for_paper_ids(connection, result_paper_ids)
        bibtex_by_paper = database.bibtex_for_paper_ids(connection, result_paper_ids)

    hit_scores: defaultdict[tuple[str, int], float] = defaultdict(float)
    hit_data: dict[tuple[str, int], _EvidenceHit] = {}
    source_count: defaultdict[tuple[str, int], int] = defaultdict(int)
    for hits in (lexical, semantic, note_lexical, note_semantic):
        seen: set[tuple[str, int]] = set()
        for rank, hit in enumerate(hits, start=1):
            key = (hit.source_type, hit.source_id)
            hit_scores[key] += 1.0 / (RRF_K + rank)
            hit_data[key] = hit
            if key not in seen:
                source_count[key] += 1
                seen.add(key)

    paper_hits: defaultdict[int, list[tuple[float, _EvidenceHit]]] = defaultdict(list)
    for key, score in hit_scores.items():
        if source_count[key] >= 2:
            score *= 1.08
        hit = hit_data[key]
        if hit.source_type == "note":
            score *= 0.95
        paper_hits[hit.paper_id].append((score, hit))

    ranked_papers: list[tuple[float, int, list[tuple[float, _EvidenceHit]]]] = []
    query_token_set = set(_query_tokens(cleaned_query))
    for paper_id, hits in paper_hits.items():
        hits.sort(key=lambda item: item[0], reverse=True)
        score = hits[0][0]
        used_pages = (
            {hits[0][1].page_number}
            if hits[0][1].source_type == "pdf"
            else set()
        )
        used_note = hits[0][1].source_type == "note"
        for hit_score, hit in hits[1:]:
            if hit.source_type == "pdf" and hit.page_number not in used_pages:
                score += hit_score * 0.45
                used_pages.add(hit.page_number)
                break
            if hit.source_type == "note" and not used_note:
                score += hit_score * 0.35
                used_note = True
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
        used_note = False
        for hit_score, hit in hits:
            if hit.source_type == "pdf" and hit.page_number in used_pages:
                continue
            if hit.source_type == "note" and used_note:
                continue
            selected.append(
                SearchEvidence(
                    source_type=hit.source_type,
                    page_number=hit.page_number,
                    text=_evidence_snippet(hit.text, cleaned_query),
                    score=hit_score,
                )
            )
            if hit.source_type == "pdf" and hit.page_number is not None:
                used_pages.add(hit.page_number)
            if hit.source_type == "note":
                used_note = True
            if len(selected) == 3:
                break
        representative = hits[0][1]
        bibtex_row = bibtex_by_paper.get(paper_id)
        bibtex = (
            BibTeXInfo(
                citekey=str(bibtex_row["citekey"] or ""),
                entry_type=str(bibtex_row["entry_type"] or ""),
                title=str(bibtex_row["title"] or ""),
                authors=str(bibtex_row["authors"] or ""),
                year=str(bibtex_row["year"] or ""),
                raw_bibtex=str(bibtex_row["raw_bibtex"] or ""),
            )
            if bibtex_row
            else None
        )
        results.append(
            SearchResult(
                paper_id=paper_id,
                title=representative.title
                or representative.relative_path.rsplit("/", 1)[-1],
                authors=representative.authors,
                relative_path=representative.relative_path,
                score=score,
                evidence=selected,
                tags=tags_by_paper.get(paper_id, ()),
                bibtex=bibtex,
            )
        )
    return results

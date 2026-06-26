from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

import numpy as np

from recallary.config import INDEX_VERSION, MODEL_ID
from recallary.domain import EmbeddedChunk, FileSnapshot, ParsedPaper


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    file_size INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    page_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_message TEXT NOT NULL DEFAULT '',
    indexed_at TEXT,
    last_attempt_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_tags (
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    tag_id INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (paper_id, tag_id)
);

CREATE TABLE IF NOT EXISTS bibtex_entries (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL UNIQUE REFERENCES papers(id) ON DELETE CASCADE,
    citekey TEXT NOT NULL DEFAULT '',
    entry_type TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    authors TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    raw_bibtex TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    clean_text TEXT NOT NULL,
    UNIQUE(paper_id, page_number)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    page_id INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    chunk_index INTEGER NOT NULL,
    section_hint TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    embedding BLOB NOT NULL,
    embedding_dim INTEGER NOT NULL,
    UNIQUE(paper_id, page_number, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    chunk_id UNINDEXED,
    paper_id UNINDEXED,
    filename,
    title,
    authors,
    section_hint,
    text,
    tokenize = 'unicode61 remove_diacritics 2'
);

CREATE INDEX IF NOT EXISTS idx_pages_paper ON pages(paper_id);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);
CREATE INDEX IF NOT EXISTS idx_chunks_page ON chunks(page_id);
CREATE INDEX IF NOT EXISTS idx_papers_status ON papers(status);
CREATE INDEX IF NOT EXISTS idx_paper_tags_tag ON paper_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_bibtex_paper ON bibtex_entries(paper_id);
"""


class RecallaryConnection(sqlite3.Connection):
    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> bool:
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return bool(result)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, factory=RecallaryConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize(path: Path) -> None:
    with connect(path) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            """
            INSERT INTO metadata(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("index_version", str(INDEX_VERSION)),
                ("embedding_model", MODEL_ID),
            ),
        )


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.rollback()
        raise
    else:
        connection.commit()


def fetch_papers_by_path(
    connection: sqlite3.Connection,
) -> dict[str, sqlite3.Row]:
    rows = connection.execute("SELECT * FROM papers").fetchall()
    return {str(row["relative_path"]): row for row in rows}


def fetch_paper_by_relative_path(
    connection: sqlite3.Connection,
    relative_path: str,
) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM papers WHERE relative_path = ?",
        (relative_path,),
    ).fetchone()


def list_papers(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT
            p.*,
            b.citekey AS bibtex_citekey,
            b.year AS bibtex_year
        FROM papers p
        LEFT JOIN bibtex_entries b ON b.paper_id = p.id
        ORDER BY lower(p.title), lower(p.relative_path)
        """
    ).fetchall()


def update_file_snapshot(
    connection: sqlite3.Connection,
    paper_id: int,
    snapshot: FileSnapshot,
) -> None:
    with transaction(connection):
        connection.execute(
            """
            UPDATE papers
            SET file_size = ?, modified_ns = ?, last_attempt_at = ?
            WHERE id = ?
            """,
            (snapshot.size, snapshot.modified_ns, utc_now(), paper_id),
        )


def replace_paper(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    content_hash: str,
    paper: ParsedPaper,
    chunks: Sequence[EmbeddedChunk],
) -> int:
    now = utc_now()
    with transaction(connection):
        existing = connection.execute(
            "SELECT id FROM papers WHERE relative_path = ?",
            (snapshot.relative_path,),
        ).fetchone()
        if existing:
            paper_id = int(existing["id"])
            connection.execute(
                "DELETE FROM chunks_fts WHERE paper_id = ?", (paper_id,)
            )
            connection.execute("DELETE FROM pages WHERE paper_id = ?", (paper_id,))
            connection.execute(
                """
                UPDATE papers
                SET filename = ?, file_size = ?, modified_ns = ?,
                    content_hash = ?, title = ?, authors = ?, page_count = ?,
                    status = 'ready', error_message = '', indexed_at = ?,
                    last_attempt_at = ?
                WHERE id = ?
                """,
                (
                    Path(snapshot.relative_path).name,
                    snapshot.size,
                    snapshot.modified_ns,
                    content_hash,
                    paper.title,
                    paper.authors,
                    paper.page_count,
                    now,
                    now,
                    paper_id,
                ),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO papers(
                    relative_path, filename, file_size, modified_ns,
                    content_hash, title, authors, page_count, status,
                    error_message, indexed_at, last_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', '', ?, ?)
                """,
                (
                    snapshot.relative_path,
                    Path(snapshot.relative_path).name,
                    snapshot.size,
                    snapshot.modified_ns,
                    content_hash,
                    paper.title,
                    paper.authors,
                    paper.page_count,
                    now,
                    now,
                ),
            )
            paper_id = int(cursor.lastrowid)

        page_ids: dict[int, int] = {}
        for page in paper.pages:
            cursor = connection.execute(
                """
                INSERT INTO pages(paper_id, page_number, raw_text, clean_text)
                VALUES (?, ?, ?, ?)
                """,
                (paper_id, page.page_number, page.raw_text, page.clean_text),
            )
            page_ids[page.page_number] = int(cursor.lastrowid)

        for embedded in chunks:
            chunk = embedded.chunk
            vector = np.asarray(embedded.embedding, dtype=np.float32)
            cursor = connection.execute(
                """
                INSERT INTO chunks(
                    paper_id, page_id, page_number, chunk_index,
                    section_hint, text, embedding, embedding_dim
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    paper_id,
                    page_ids[chunk.page_number],
                    chunk.page_number,
                    chunk.chunk_index,
                    chunk.section_hint,
                    chunk.text,
                    vector.tobytes(),
                    int(vector.size),
                ),
            )
            chunk_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO chunks_fts(
                    chunk_id, paper_id, filename, title, authors,
                    section_hint, text
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk_id,
                    paper_id,
                    Path(snapshot.relative_path).name,
                    paper.title,
                    paper.authors,
                    chunk.section_hint,
                    chunk.text,
                ),
            )
    return paper_id


def record_failure(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    content_hash: str,
    status: str,
    message: str,
) -> None:
    now = utc_now()
    with transaction(connection):
        existing = connection.execute(
            "SELECT id, status FROM papers WHERE relative_path = ?",
            (snapshot.relative_path,),
        ).fetchone()
        if existing:
            # Preserve a previously usable index if refreshing the file failed.
            next_status = (
                str(existing["status"])
                if existing["status"] == "ready"
                else status
            )
            connection.execute(
                """
                UPDATE papers
                SET file_size = ?, modified_ns = ?, content_hash = ?,
                    status = ?, error_message = ?, last_attempt_at = ?
                WHERE id = ?
                """,
                (
                    snapshot.size,
                    snapshot.modified_ns,
                    content_hash,
                    next_status,
                    message[:2000],
                    now,
                    int(existing["id"]),
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO papers(
                    relative_path, filename, file_size, modified_ns,
                    content_hash, status, error_message, last_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.relative_path,
                    Path(snapshot.relative_path).name,
                    snapshot.size,
                    snapshot.modified_ns,
                    content_hash,
                    status,
                    message[:2000],
                    now,
                ),
            )


def remove_papers(
    connection: sqlite3.Connection, paper_ids: Sequence[int]
) -> int:
    if not paper_ids:
        return 0
    with transaction(connection):
        connection.executemany(
            "DELETE FROM chunks_fts WHERE paper_id = ?",
            ((paper_id,) for paper_id in paper_ids),
        )
        connection.executemany(
            "DELETE FROM papers WHERE id = ?",
            ((paper_id,) for paper_id in paper_ids),
        )
    return len(paper_ids)


def normalize_tag_name(name: str) -> str:
    return " ".join(name.strip().split()).lower()


def list_tags(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT t.name, COUNT(pt.paper_id) AS paper_count
        FROM tags t
        LEFT JOIN paper_tags pt ON pt.tag_id = t.id
        GROUP BY t.id
        ORDER BY lower(t.name)
        """
    ).fetchall()


def tags_for_paper_ids(
    connection: sqlite3.Connection,
    paper_ids: Sequence[int],
) -> dict[int, tuple[str, ...]]:
    if not paper_ids:
        return {}
    placeholders = ",".join("?" for _ in paper_ids)
    rows = connection.execute(
        f"""
        SELECT pt.paper_id, t.name
        FROM paper_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.paper_id IN ({placeholders})
        ORDER BY lower(t.name)
        """,
        tuple(paper_ids),
    ).fetchall()
    tags: dict[int, list[str]] = {int(paper_id): [] for paper_id in paper_ids}
    for row in rows:
        tags[int(row["paper_id"])].append(str(row["name"]))
    return {paper_id: tuple(names) for paper_id, names in tags.items()}


def tags_for_paper(connection: sqlite3.Connection, paper_id: int) -> tuple[str, ...]:
    return tags_for_paper_ids(connection, [paper_id]).get(paper_id, ())


def add_tag_to_paper(
    connection: sqlite3.Connection,
    relative_path: str,
    tag_name: str,
) -> None:
    clean_name = normalize_tag_name(tag_name)
    if not clean_name:
        raise ValueError("Tag name cannot be empty.")
    row = fetch_paper_by_relative_path(connection, relative_path)
    if row is None:
        raise ValueError(f"Paper is not indexed: {relative_path}")
    now = utc_now()
    with transaction(connection):
        cursor = connection.execute(
            """
            INSERT INTO tags(name, created_at) VALUES(?, ?)
            ON CONFLICT(name) DO UPDATE SET name = excluded.name
            RETURNING id
            """,
            (clean_name, now),
        )
        tag_id = int(cursor.fetchone()["id"])
        connection.execute(
            """
            INSERT OR IGNORE INTO paper_tags(paper_id, tag_id, created_at)
            VALUES (?, ?, ?)
            """,
            (int(row["id"]), tag_id, now),
        )


def remove_tag_from_paper(
    connection: sqlite3.Connection,
    relative_path: str,
    tag_name: str,
) -> None:
    clean_name = normalize_tag_name(tag_name)
    if not clean_name:
        raise ValueError("Tag name cannot be empty.")
    row = fetch_paper_by_relative_path(connection, relative_path)
    if row is None:
        raise ValueError(f"Paper is not indexed: {relative_path}")
    with transaction(connection):
        connection.execute(
            """
            DELETE FROM paper_tags
            WHERE paper_id = ?
              AND tag_id IN (SELECT id FROM tags WHERE name = ?)
            """,
            (int(row["id"]), clean_name),
        )
        connection.execute(
            """
            DELETE FROM tags
            WHERE id NOT IN (SELECT tag_id FROM paper_tags)
            """
        )


def bibtex_for_paper_ids(
    connection: sqlite3.Connection,
    paper_ids: Sequence[int],
) -> dict[int, sqlite3.Row]:
    if not paper_ids:
        return {}
    placeholders = ",".join("?" for _ in paper_ids)
    rows = connection.execute(
        f"""
        SELECT *
        FROM bibtex_entries
        WHERE paper_id IN ({placeholders})
        """,
        tuple(paper_ids),
    ).fetchall()
    return {int(row["paper_id"]): row for row in rows}


def bibtex_for_paper(
    connection: sqlite3.Connection,
    paper_id: int,
) -> sqlite3.Row | None:
    return bibtex_for_paper_ids(connection, [paper_id]).get(paper_id)


def save_bibtex_for_paper(
    connection: sqlite3.Connection,
    relative_path: str,
    *,
    raw_bibtex: str,
    citekey: str = "",
    entry_type: str = "",
    title: str = "",
    authors: str = "",
    year: str = "",
) -> None:
    raw = raw_bibtex.strip()
    if not raw:
        raise ValueError("BibTeX cannot be empty.")
    row = fetch_paper_by_relative_path(connection, relative_path)
    if row is None:
        raise ValueError(f"Paper is not indexed: {relative_path}")
    now = utc_now()
    with transaction(connection):
        connection.execute(
            """
            INSERT INTO bibtex_entries(
                paper_id, citekey, entry_type, title, authors, year,
                raw_bibtex, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(paper_id) DO UPDATE SET
                citekey = excluded.citekey,
                entry_type = excluded.entry_type,
                title = excluded.title,
                authors = excluded.authors,
                year = excluded.year,
                raw_bibtex = excluded.raw_bibtex,
                updated_at = excluded.updated_at
            """,
            (
                int(row["id"]),
                citekey.strip(),
                entry_type.strip(),
                title.strip(),
                authors.strip(),
                year.strip(),
                raw,
                now,
                now,
            ),
        )


def remove_bibtex_from_paper(
    connection: sqlite3.Connection,
    relative_path: str,
) -> None:
    row = fetch_paper_by_relative_path(connection, relative_path)
    if row is None:
        raise ValueError(f"Paper is not indexed: {relative_path}")
    with transaction(connection):
        connection.execute(
            "DELETE FROM bibtex_entries WHERE paper_id = ?",
            (int(row["id"]),),
        )


def manual_metadata_counts(connection: sqlite3.Connection) -> dict[str, int]:
    tag_count = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
    tagged_papers = connection.execute(
        "SELECT COUNT(DISTINCT paper_id) FROM paper_tags"
    ).fetchone()[0]
    bibtex_count = connection.execute(
        "SELECT COUNT(*) FROM bibtex_entries"
    ).fetchone()[0]
    return {
        "tags": int(tag_count),
        "tagged_papers": int(tagged_papers),
        "bibtex_entries": int(bibtex_count),
    }


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def export_manual_metadata(path: Path) -> dict[str, list[dict[str, Any]]]:
    if not path.is_file():
        return {"tags": [], "bibtex": []}
    with connect(path) as connection:
        if not _table_exists(connection, "tags"):
            return {"tags": [], "bibtex": []}
        tag_rows = connection.execute(
            """
            SELECT p.relative_path, t.name
            FROM paper_tags pt
            JOIN papers p ON p.id = pt.paper_id
            JOIN tags t ON t.id = pt.tag_id
            ORDER BY p.relative_path, lower(t.name)
            """
        ).fetchall()
        bibtex_rows: list[sqlite3.Row] = []
        if _table_exists(connection, "bibtex_entries"):
            bibtex_rows = connection.execute(
                """
                SELECT
                    p.relative_path,
                    b.citekey,
                    b.entry_type,
                    b.title,
                    b.authors,
                    b.year,
                    b.raw_bibtex
                FROM bibtex_entries b
                JOIN papers p ON p.id = b.paper_id
                ORDER BY p.relative_path
                """
            ).fetchall()
    return {
        "tags": [
            {
                "relative_path": str(row["relative_path"]),
                "name": str(row["name"]),
            }
            for row in tag_rows
        ],
        "bibtex": [
            {
                "relative_path": str(row["relative_path"]),
                "citekey": str(row["citekey"] or ""),
                "entry_type": str(row["entry_type"] or ""),
                "title": str(row["title"] or ""),
                "authors": str(row["authors"] or ""),
                "year": str(row["year"] or ""),
                "raw_bibtex": str(row["raw_bibtex"] or ""),
            }
            for row in bibtex_rows
        ],
    }


def import_manual_metadata(
    connection: sqlite3.Connection,
    exported: dict[str, list[dict[str, Any]]],
) -> None:
    for item in exported.get("tags", []):
        relative_path = str(item.get("relative_path", ""))
        tag_name = str(item.get("name", ""))
        if not relative_path or not tag_name:
            continue
        try:
            add_tag_to_paper(connection, relative_path, tag_name)
        except ValueError:
            continue
    for item in exported.get("bibtex", []):
        relative_path = str(item.get("relative_path", ""))
        raw_bibtex = str(item.get("raw_bibtex", ""))
        if not relative_path or not raw_bibtex:
            continue
        try:
            save_bibtex_for_paper(
                connection,
                relative_path,
                raw_bibtex=raw_bibtex,
                citekey=str(item.get("citekey", "")),
                entry_type=str(item.get("entry_type", "")),
                title=str(item.get("title", "")),
                authors=str(item.get("authors", "")),
                year=str(item.get("year", "")),
            )
        except ValueError:
            continue


def integrity_check(connection: sqlite3.Connection) -> str:
    row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else "unknown"


def status_counts(connection: sqlite3.Connection) -> dict[str, int]:
    counts = {
        "total": 0,
        "ready": 0,
        "no_text": 0,
        "parse_failed": 0,
    }
    rows = connection.execute(
        "SELECT status, COUNT(*) AS count FROM papers GROUP BY status"
    ).fetchall()
    for row in rows:
        status = str(row["status"])
        count = int(row["count"])
        counts["total"] += count
        counts[status] = count
    return counts


def failed_papers(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT relative_path, status, error_message
        FROM papers
        WHERE status != 'ready' OR error_message != ''
        ORDER BY relative_path
        """
    ).fetchall()


def latest_index_time(connection: sqlite3.Connection) -> str | None:
    row = connection.execute(
        "SELECT MAX(indexed_at) AS indexed_at FROM papers"
    ).fetchone()
    return str(row["indexed_at"]) if row and row["indexed_at"] else None

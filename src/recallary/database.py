from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Sequence

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
"""


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
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

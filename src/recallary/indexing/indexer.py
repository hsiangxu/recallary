from __future__ import annotations

import hashlib
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock, Timeout

from recallary import database
from recallary.config import Settings
from recallary.domain import EmbeddedChunk, FileSnapshot, IndexSummary
from recallary.indexing.chunker import chunk_paper
from recallary.indexing.embedder import Embedder
from recallary.indexing.parser import NoTextError, parse_pdf


ProgressCallback = Callable[[int, int, str], None]
EmbedderFactory = Callable[[Settings], Embedder]


def scan_library(settings: Settings) -> list[FileSnapshot]:
    snapshots: list[FileSnapshot] = []
    if not settings.library_dir.exists():
        return snapshots
    for path in sorted(
        settings.library_dir.rglob("*.pdf"),
        key=lambda item: item.as_posix().lower(),
    ):
        if not path.is_file():
            continue
        stat = path.stat()
        snapshots.append(
            FileSnapshot(
                path=path,
                relative_path=path.relative_to(settings.root).as_posix(),
                size=stat.st_size,
                modified_ns=stat.st_mtime_ns,
            )
        )
    return snapshots


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_stable(snapshot: FileSnapshot) -> bool:
    stat = snapshot.path.stat()
    return stat.st_size == snapshot.size and stat.st_mtime_ns == snapshot.modified_ns


def _process_snapshot(
    connection: sqlite3.Connection,
    snapshot: FileSnapshot,
    content_hash: str,
    embedder: Embedder,
) -> None:
    try:
        paper = parse_pdf(snapshot.path)
        chunks = chunk_paper(paper)
        if not chunks:
            raise NoTextError("No searchable text chunks could be created.")
        vectors = embedder.encode_passages([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise RuntimeError("The embedding model returned an invalid result count.")
        if not _is_stable(snapshot):
            raise RuntimeError(
                "The PDF changed while it was being indexed; retry after sync finishes."
            )
        embedded = [
            EmbeddedChunk(chunk=chunk, embedding=vector)
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        database.replace_paper(
            connection,
            snapshot,
            content_hash,
            paper,
            embedded,
        )
    except NoTextError as error:
        database.record_failure(
            connection, snapshot, content_hash, "no_text", str(error)
        )
        raise
    except Exception as error:
        database.record_failure(
            connection, snapshot, content_hash, "parse_failed", str(error)
        )
        raise


def _run_index(
    settings: Settings,
    database_path: Path,
    progress: ProgressCallback | None = None,
    embedder_factory: EmbedderFactory = Embedder,
) -> IndexSummary:
    database.initialize(database_path)
    summary = IndexSummary()
    snapshots = scan_library(settings)
    summary.discovered = len(snapshots)

    embedder: Embedder | None = None
    with database.connect(database_path) as connection:
        existing_by_path = database.fetch_papers_by_path(connection)
        current_paths = {snapshot.relative_path for snapshot in snapshots}
        missing_ids = [
            int(row["id"])
            for path, row in existing_by_path.items()
            if path not in current_paths
        ]
        summary.removed = database.remove_papers(connection, missing_ids)

        candidates: list[tuple[FileSnapshot, sqlite3.Row | None]] = []
        for snapshot in snapshots:
            existing = existing_by_path.get(snapshot.relative_path)
            if (
                existing
                and int(existing["file_size"]) == snapshot.size
                and int(existing["modified_ns"]) == snapshot.modified_ns
                and str(existing["status"]) == "ready"
                and not str(existing["error_message"])
            ):
                summary.unchanged += 1
                continue
            candidates.append((snapshot, existing))

        total = len(candidates)
        for index, (snapshot, existing) in enumerate(candidates, start=1):
            if progress:
                progress(index, total, snapshot.relative_path)
            content_hash: str | None = None
            try:
                content_hash = sha256_file(snapshot.path)
                if (
                    existing
                    and str(existing["content_hash"]) == content_hash
                    and str(existing["status"]) == "ready"
                    and not str(existing["error_message"])
                ):
                    database.update_file_snapshot(
                        connection, int(existing["id"]), snapshot
                    )
                    summary.metadata_updated += 1
                    continue
                if embedder is None:
                    embedder = embedder_factory(settings)
                _process_snapshot(
                    connection, snapshot, content_hash, embedder
                )
                summary.indexed += 1
            except Exception as error:
                if content_hash is None:
                    fallback_hash = (
                        str(existing["content_hash"]) if existing else ""
                    )
                    try:
                        database.record_failure(
                            connection,
                            snapshot,
                            fallback_hash,
                            "parse_failed",
                            str(error),
                        )
                    except Exception:
                        pass
                summary.failed += 1
                summary.failures.append((snapshot.relative_path, str(error)))
    return summary


def index_library(
    settings: Settings,
    *,
    rebuild: bool = False,
    progress: ProgressCallback | None = None,
    embedder_factory: EmbedderFactory = Embedder,
) -> IndexSummary:
    settings.configure_local_storage()
    try:
        with FileLock(settings.index_lock_path, timeout=0):
            if not rebuild:
                return _run_index(
                    settings,
                    settings.database_path,
                    progress,
                    embedder_factory,
                )

            manual_metadata = database.export_manual_metadata(settings.database_path)
            temporary = settings.data_dir / "recallary.rebuild.db"
            for candidate in (
                temporary,
                Path(f"{temporary}-journal"),
                Path(f"{temporary}-wal"),
                Path(f"{temporary}-shm"),
            ):
                if candidate.exists():
                    candidate.unlink()

            summary = _run_index(
                settings,
                temporary,
                progress,
                embedder_factory,
            )
            with database.connect(temporary) as connection:
                database.import_manual_metadata(connection, manual_metadata)
                check = database.integrity_check(connection)
            if check != "ok":
                raise RuntimeError(
                    f"Rebuilt database failed its integrity check: {check}"
                )
            os.replace(temporary, settings.database_path)
            return summary
    except Timeout as error:
        raise RuntimeError(
            "Another Recallary indexing process is already running."
        ) from error

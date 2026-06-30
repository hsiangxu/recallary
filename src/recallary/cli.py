from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import typer

from recallary import database
from recallary.bibtex import parse_bibtex
from recallary.config import DEFAULT_LIMIT, MODEL_ID, Settings
from recallary.indexing.embedder import download_model, model_is_installed
from recallary.indexing.indexer import (
    index_library,
    pending_reason_for_snapshot,
    scan_library,
)
from recallary.launchers import make_launcher
from recallary.search.engine import search_library


app = typer.Typer(
    no_args_is_help=True,
    help="Find papers in a local PDF library from vague descriptions.",
)
tag_app = typer.Typer(help="Manage manual paper tags.")
bib_app = typer.Typer(help="Manage BibTeX entries linked to PDFs.")
app.add_typer(tag_app, name="tag")
app.add_typer(bib_app, name="bib")


def _settings() -> Settings:
    settings = Settings.from_root()
    settings.configure_local_storage()
    return settings


def _fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _relative_path(settings: Settings, path_text: str) -> str:
    path = Path(path_text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(settings.root).as_posix()
        except ValueError as error:
            raise ValueError("PDF path must be inside the Recallary project.") from error
    return path.as_posix()


@app.command()
def setup() -> None:
    """Create local directories, initialize the database, and download the model."""
    settings = _settings()
    typer.echo(f"Project: {settings.root}")
    typer.echo("Initializing local database...")
    database.initialize(settings.database_path)
    if model_is_installed(settings.model_dir):
        typer.echo(f"Model already installed: {settings.model_dir}")
    else:
        typer.echo(f"Downloading {settings.model_id} to {settings.model_dir}")
        typer.echo("This is the only step that requires network access.")
        try:
            download_model(settings)
        except Exception as error:
            _fail(str(error))
    typer.secho("Recallary setup is complete.", fg=typer.colors.GREEN)


@app.command("index")
def index_command(
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Build a new database from every PDF, then replace the old one.",
    ),
) -> None:
    """Index new or changed PDFs and remove entries for deleted PDFs."""
    settings = _settings()
    if not model_is_installed(settings.model_dir):
        _fail("The local model is missing. Run `recallary setup` first.")

    def progress(current: int, total: int, relative_path: str) -> None:
        typer.echo(f"[{current}/{total}] {relative_path}")

    try:
        summary = index_library(
            settings,
            rebuild=rebuild,
            progress=progress,
        )
    except KeyboardInterrupt:
        typer.secho(
            "Indexing interrupted. Completed papers remain available.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=130)
    except Exception as error:
        _fail(str(error))

    typer.echo(
        "Index complete: "
        f"{summary.indexed} indexed, "
        f"{summary.metadata_updated} metadata-only updates, "
        f"{summary.unchanged} unchanged, "
        f"{summary.removed} removed, "
        f"{summary.failed} failed."
    )
    for path, message in summary.failures:
        typer.secho(f"  {path}: {message}", fg=typer.colors.YELLOW)
    if summary.failed:
        raise typer.Exit(code=2)


@app.command()
def search(
    query: str = typer.Argument(..., help="A vague description of the paper."),
    limit: int = typer.Option(
        DEFAULT_LIMIT,
        "--limit",
        "-n",
        min=1,
        max=100,
        help="Maximum number of papers to return.",
    ),
    tags: list[str] = typer.Option(
        [],
        "--tag",
        help="Restrict search to papers that have this tag. Repeat for multiple tags.",
    ),
) -> None:
    """Return likely papers with PDF page numbers and source evidence."""
    settings = _settings()
    try:
        results = search_library(settings, query, limit=limit, tag_names=tuple(tags))
    except Exception as error:
        _fail(str(error))

    if not results:
        typer.echo("No matching indexed papers were found.")
        return

    for rank, result in enumerate(results, start=1):
        typer.secho(f"{rank}. {result.title}", bold=True)
        if result.authors:
            typer.echo(f"   {result.authors}")
        typer.echo(f"   {result.relative_path}")
        if result.tags:
            typer.echo(f"   Tags: {', '.join(result.tags)}")
        if result.bibtex and (result.bibtex.citekey or result.bibtex.year):
            citation = " ".join(
                part for part in (result.bibtex.citekey, result.bibtex.year) if part
            )
            typer.echo(f"   BibTeX: {citation}")
        for evidence in result.evidence:
            label = (
                f"PDF page {evidence.page_number}"
                if evidence.source_type == "pdf"
                else "Note evidence"
            )
            typer.secho(f"   {label}", fg=typer.colors.CYAN)
            typer.echo(f'   "{evidence.text}"')
        typer.echo()


@app.command()
def status() -> None:
    """Show library, index, model, and database health."""
    settings = _settings()
    pdf_count = len(scan_library(settings))
    typer.echo(f"Project: {settings.root}")
    typer.echo(f"PDFs in library: {pdf_count}")
    typer.echo(f"Model: {MODEL_ID}")
    typer.echo(
        f"Model installed: {'yes' if model_is_installed(settings.model_dir) else 'no'}"
    )

    if not settings.database_path.is_file():
        typer.echo("Database: not initialized")
        return
    database.initialize(settings.database_path)
    try:
        with database.connect(settings.database_path) as connection:
            counts = database.status_counts(connection)
            manual = database.manual_metadata_counts(connection)
            integrity = database.integrity_check(connection)
            latest = database.latest_index_time(connection)
            failures = database.failed_papers(connection)
            existing = database.fetch_papers_by_path(connection)
    except sqlite3.DatabaseError as error:
        _fail(f"Could not read the database: {error}")

    pending = 0
    for snapshot in scan_library(settings):
        row = existing.get(snapshot.relative_path)
        if pending_reason_for_snapshot(row, snapshot, verify_hash=True):
            pending += 1

    typer.echo(f"Database integrity: {integrity}")
    typer.echo(f"Tracked records: {counts['total']}")
    typer.echo(f"Ready: {counts['ready']}")
    typer.echo(f"Pending or changed: {pending}")
    typer.echo(f"No text: {counts['no_text']}")
    typer.echo(f"Parse failed: {counts['parse_failed']}")
    typer.echo(f"Tags: {manual['tags']}")
    typer.echo(f"Papers with tags: {manual['tagged_papers']}")
    typer.echo(f"Papers with BibTeX: {manual['bibtex_entries']}")
    typer.echo(f"Last successful indexing: {latest or 'never'}")
    if failures:
        typer.echo("Files needing attention:")
        for row in failures:
            typer.echo(
                f"  {row['relative_path']} [{row['status']}]: "
                f"{row['error_message']}"
            )


@app.command("make-launcher")
def make_launcher_command() -> None:
    """Create a double-click launcher in the repository root for this computer."""
    settings = _settings()
    try:
        result = make_launcher(settings)
    except Exception as error:
        _fail(str(error))
    typer.secho("Launcher created.", fg=typer.colors.GREEN)
    typer.echo(f"Launcher: {result.path}")
    typer.echo(f"Python: {result.python_path}")
    typer.echo(f"Log: {result.log_path}")
    typer.echo("Re-run this command if the Conda environment or repo path changes.")


@tag_app.command("add")
def tag_add(
    pdf: str = typer.Argument(..., help="Indexed PDF path relative to the project."),
    tag: str = typer.Argument(..., help="Tag to add."),
) -> None:
    """Add a manual tag to an indexed PDF."""
    settings = _settings()
    try:
        database.initialize(settings.database_path)
        with database.connect(settings.database_path) as connection:
            database.add_tag_to_paper(connection, _relative_path(settings, pdf), tag)
    except Exception as error:
        _fail(str(error))
    typer.echo("Tag added.")


@tag_app.command("remove")
def tag_remove(
    pdf: str = typer.Argument(..., help="Indexed PDF path relative to the project."),
    tag: str = typer.Argument(..., help="Tag to remove."),
) -> None:
    """Remove a manual tag from an indexed PDF."""
    settings = _settings()
    try:
        database.initialize(settings.database_path)
        with database.connect(settings.database_path) as connection:
            database.remove_tag_from_paper(connection, _relative_path(settings, pdf), tag)
    except Exception as error:
        _fail(str(error))
    typer.echo("Tag removed.")


@tag_app.command("list")
def tag_list() -> None:
    """List all tags."""
    settings = _settings()
    database.initialize(settings.database_path)
    with database.connect(settings.database_path) as connection:
        tags = database.list_tags(connection)
    if not tags:
        typer.echo("No tags.")
        return
    for row in tags:
        typer.echo(f"{row['name']} ({row['paper_count']})")


@tag_app.command("show")
def tag_show(
    pdf: str = typer.Argument(..., help="Indexed PDF path relative to the project."),
) -> None:
    """Show tags for one indexed PDF."""
    settings = _settings()
    try:
        database.initialize(settings.database_path)
        with database.connect(settings.database_path) as connection:
            row = database.fetch_paper_by_relative_path(
                connection, _relative_path(settings, pdf)
            )
            if row is None:
                raise ValueError(f"Paper is not indexed: {pdf}")
            tags = database.tags_for_paper(connection, int(row["id"]))
    except Exception as error:
        _fail(str(error))
    typer.echo(", ".join(tags) if tags else "No tags.")


@bib_app.command("add")
def bib_add(
    pdf: str = typer.Argument(..., help="Indexed PDF path relative to the project."),
    file: Path | None = typer.Option(
        None,
        "--file",
        "-f",
        exists=True,
        dir_okay=False,
        readable=True,
        help="BibTeX file to read. If omitted, paste BibTeX through stdin.",
    ),
) -> None:
    """Attach or replace a BibTeX entry for an indexed PDF."""
    settings = _settings()
    try:
        raw = file.read_text(encoding="utf-8") if file else sys.stdin.read()
        parsed = parse_bibtex(raw)
        database.initialize(settings.database_path)
        with database.connect(settings.database_path) as connection:
            database.save_bibtex_for_paper(
                connection,
                _relative_path(settings, pdf),
                raw_bibtex=raw,
                citekey=parsed["citekey"],
                entry_type=parsed["entry_type"],
                title=parsed["title"],
                authors=parsed["authors"],
                year=parsed["year"],
            )
    except Exception as error:
        _fail(str(error))
    typer.echo("BibTeX saved.")


@bib_app.command("show")
def bib_show(
    pdf: str = typer.Argument(..., help="Indexed PDF path relative to the project."),
) -> None:
    """Show the BibTeX entry linked to one indexed PDF."""
    settings = _settings()
    try:
        database.initialize(settings.database_path)
        with database.connect(settings.database_path) as connection:
            row = database.fetch_paper_by_relative_path(
                connection, _relative_path(settings, pdf)
            )
            if row is None:
                raise ValueError(f"Paper is not indexed: {pdf}")
            bibtex = database.bibtex_for_paper(connection, int(row["id"]))
    except Exception as error:
        _fail(str(error))
    typer.echo(str(bibtex["raw_bibtex"]) if bibtex else "No BibTeX.")


@bib_app.command("remove")
def bib_remove(
    pdf: str = typer.Argument(..., help="Indexed PDF path relative to the project."),
) -> None:
    """Remove the BibTeX entry linked to one indexed PDF."""
    settings = _settings()
    try:
        database.initialize(settings.database_path)
        with database.connect(settings.database_path) as connection:
            database.remove_bibtex_from_paper(connection, _relative_path(settings, pdf))
    except Exception as error:
        _fail(str(error))
    typer.echo("BibTeX removed.")


if __name__ == "__main__":
    app()

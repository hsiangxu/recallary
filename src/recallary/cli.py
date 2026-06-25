from __future__ import annotations

import sqlite3

import typer

from recallary import database
from recallary.config import DEFAULT_LIMIT, MODEL_ID, Settings
from recallary.indexing.embedder import download_model, model_is_installed
from recallary.indexing.indexer import index_library, scan_library
from recallary.search.engine import search_library


app = typer.Typer(
    no_args_is_help=True,
    help="Find papers in a local PDF library from vague descriptions.",
)


def _settings() -> Settings:
    settings = Settings.from_root()
    settings.configure_local_storage()
    return settings


def _fail(message: str) -> None:
    typer.secho(f"Error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


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
) -> None:
    """Return likely papers with PDF page numbers and source evidence."""
    settings = _settings()
    try:
        results = search_library(settings, query, limit=limit)
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
        for evidence in result.evidence:
            typer.secho(f"   PDF page {evidence.page_number}", fg=typer.colors.CYAN)
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
    try:
        with database.connect(settings.database_path) as connection:
            counts = database.status_counts(connection)
            integrity = database.integrity_check(connection)
            latest = database.latest_index_time(connection)
            failures = database.failed_papers(connection)
            existing = database.fetch_papers_by_path(connection)
    except sqlite3.DatabaseError as error:
        _fail(f"Could not read the database: {error}")

    pending = 0
    for snapshot in scan_library(settings):
        row = existing.get(snapshot.relative_path)
        if (
            row is None
            or int(row["file_size"]) != snapshot.size
            or int(row["modified_ns"]) != snapshot.modified_ns
            or str(row["status"]) != "ready"
            or bool(row["error_message"])
        ):
            pending += 1

    typer.echo(f"Database integrity: {integrity}")
    typer.echo(f"Tracked records: {counts['total']}")
    typer.echo(f"Ready: {counts['ready']}")
    typer.echo(f"Pending or changed: {pending}")
    typer.echo(f"No text: {counts['no_text']}")
    typer.echo(f"Parse failed: {counts['parse_failed']}")
    typer.echo(f"Last successful indexing: {latest or 'never'}")
    if failures:
        typer.echo("Files needing attention:")
        for row in failures:
            typer.echo(
                f"  {row['relative_path']} [{row['status']}]: "
                f"{row['error_message']}"
            )


if __name__ == "__main__":
    app()

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from recallary import database
from recallary.config import DEFAULT_LIMIT, Settings
from recallary.indexing.embedder import download_model, model_is_installed
from recallary.indexing.indexer import index_library
from recallary.search.engine import search_library


def _unique_destination(directory: Path, filename: str) -> Path:
    destination = directory / filename
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    counter = 1
    while True:
        candidate = directory / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _relative_path(settings: Settings, path: Path) -> str:
    return path.resolve().relative_to(settings.root).as_posix()


class SetupWorker(QObject):
    message = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings

    @Slot()
    def run(self) -> None:
        try:
            self.message.emit("Initializing local database...")
            database.initialize(self.settings.database_path)
            if model_is_installed(self.settings.model_dir):
                self.finished.emit(True, "Setup complete. Model is already installed.")
                return
            self.message.emit(f"Downloading model to {self.settings.model_dir}...")
            download_model(self.settings)
            self.finished.emit(True, "Setup complete. Model downloaded.")
        except Exception as error:
            self.finished.emit(False, str(error))


class IndexWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(bool, object, str)

    def __init__(self, settings: Settings, *, rebuild: bool = False):
        super().__init__()
        self.settings = settings
        self.rebuild = rebuild

    @Slot()
    def run(self) -> None:
        try:
            if not model_is_installed(self.settings.model_dir):
                raise RuntimeError("The local model is missing. Run Setup first.")

            def progress(current: int, total: int, relative_path: str) -> None:
                self.progress.emit(current, total, relative_path)

            summary = index_library(
                self.settings,
                rebuild=self.rebuild,
                progress=progress,
            )
            self.finished.emit(True, summary, "")
        except Exception as error:
            self.finished.emit(False, None, str(error))


class SearchWorker(QObject):
    finished = Signal(bool, object, str)

    def __init__(
        self,
        settings: Settings,
        query: str,
        *,
        limit: int = DEFAULT_LIMIT,
        tag_names: tuple[str, ...] = (),
    ):
        super().__init__()
        self.settings = settings
        self.query = query
        self.limit = limit
        self.tag_names = tag_names

    @Slot()
    def run(self) -> None:
        try:
            results = search_library(
                self.settings,
                self.query,
                limit=self.limit,
                tag_names=self.tag_names,
            )
            self.finished.emit(True, results, "")
        except Exception as error:
            self.finished.emit(False, [], str(error))


class SaveNotesWorker(QObject):
    finished = Signal(bool, object, str)

    def __init__(self, settings: Settings, relative_path: str, content: str):
        super().__init__()
        self.settings = settings
        self.relative_path = relative_path
        self.content = content

    @Slot()
    def run(self) -> None:
        try:
            text = self.content.strip()
            embedding = None
            semantic_enabled = False
            if text and model_is_installed(self.settings.model_dir):
                from recallary.indexing.embedder import Embedder

                embedding = Embedder(self.settings).encode_passages([text])[0]
                semantic_enabled = True

            database.initialize(self.settings.database_path)
            with database.connect(self.settings.database_path) as connection:
                database.save_note_for_paper(
                    connection,
                    self.relative_path,
                    text,
                    embedding=embedding,
                )
            self.finished.emit(
                True,
                {
                    "relative_path": self.relative_path,
                    "content": text,
                    "semantic_enabled": semantic_enabled,
                },
                "",
            )
        except Exception as error:
            self.finished.emit(False, None, str(error))


class AddPdfsWorker(QObject):
    progress = Signal(int, int, str)
    finished = Signal(bool, object, str)

    def __init__(self, settings: Settings, files: list[str]):
        super().__init__()
        self.settings = settings
        self.files = files

    @Slot()
    def run(self) -> None:
        try:
            added_paths: list[str] = []
            errors: list[str] = []
            self.settings.library_dir.mkdir(parents=True, exist_ok=True)
            total = len(self.files)
            for index, file_name in enumerate(self.files, start=1):
                source = Path(file_name)
                self.progress.emit(index, total, source.name)
                try:
                    destination = _unique_destination(
                        self.settings.library_dir,
                        source.name,
                    )
                    shutil.copy2(source, destination)
                    added_paths.append(_relative_path(self.settings, destination))
                except Exception as error:
                    errors.append(f"{source}: {error}")
            self.finished.emit(
                True,
                {
                    "added": len(added_paths),
                    "added_paths": added_paths,
                    "errors": errors,
                },
                "",
            )
        except Exception as error:
            self.finished.emit(False, None, str(error))


class FunctionWorker(QObject):
    finished = Signal(bool, object, str)

    def __init__(self, function: Any, *args: Any, **kwargs: Any):
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs

    @Slot()
    def run(self) -> None:
        try:
            self.finished.emit(True, self.function(*self.args, **self.kwargs), "")
        except Exception as error:
            self.finished.emit(False, None, str(error))

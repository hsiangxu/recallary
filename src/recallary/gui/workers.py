from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal, Slot

from recallary import database
from recallary.config import DEFAULT_LIMIT, Settings
from recallary.indexing.embedder import download_model, model_is_installed
from recallary.indexing.indexer import index_library
from recallary.search.engine import search_library


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

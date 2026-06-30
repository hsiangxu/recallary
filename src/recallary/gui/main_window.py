from __future__ import annotations

import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from recallary import database
from recallary.bibtex import parse_bibtex
from recallary.config import DEFAULT_LIMIT, Settings
from recallary.domain import BibTeXInfo, SearchResult
from recallary.indexing.embedder import model_is_installed
from recallary.indexing.indexer import pending_reason_for_snapshot, scan_library
from recallary.gui.workers import (
    AddPdfsWorker,
    IndexWorker,
    SaveNotesWorker,
    SearchWorker,
    SetupWorker,
)


def _display_title(row: Any) -> str:
    display_name = str(row["display_name"] or "").strip()
    if display_name:
        return display_name
    title = str(row["title"] or "").strip()
    if title:
        return title
    filename = str(row["filename"] or "").strip()
    return filename or Path(str(row["relative_path"])).name


def _parsed_title(row: Any) -> str:
    title = str(row["title"] or "").strip()
    if title:
        return title
    filename = str(row["filename"] or "").strip()
    return filename or Path(str(row["relative_path"])).name


def _detail_label() -> QLabel:
    label = QLabel("")
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    label.setSizePolicy(
        QSizePolicy.Policy.Ignored,
        QSizePolicy.Policy.Preferred,
    )
    return label


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


def _open_in_file_manager(path: Path) -> None:
    if sys.platform.startswith("win"):
        subprocess.run(["explorer", f"/select,{path}"], check=False)
    elif sys.platform == "darwin":
        subprocess.run(["open", "-R", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path.parent)], check=False)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.settings.configure_local_storage()
        database.initialize(self.settings.database_path)

        self.current_relative_path: str | None = None
        self.current_result: SearchResult | None = None
        self._threads: list[QThread] = []

        self.setWindowTitle("Recallary")
        self.resize(1280, 820)
        self.setStatusBar(QStatusBar())
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)

        toolbar = QHBoxLayout()
        self.setup_button = QPushButton("Setup / Check Model")
        self.add_button = QPushButton("Add PDFs")
        self.index_button = QPushButton("Index Library")
        self.rebuild_button = QPushButton("Rebuild Index")
        self.refresh_button = QPushButton("Refresh")
        toolbar.addWidget(self.setup_button)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.index_button)
        toolbar.addWidget(self.rebuild_button)
        toolbar.addWidget(self.refresh_button)
        toolbar.addStretch(1)
        root_layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_middle_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setSizes([260, 560, 460])
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

        self.setup_button.clicked.connect(self.run_setup)
        self.add_button.clicked.connect(self.add_pdfs)
        self.index_button.clicked.connect(lambda: self.run_index(rebuild=False))
        self.rebuild_button.clicked.connect(lambda: self.run_index(rebuild=True))
        self.refresh_button.clicked.connect(self.refresh)
        self.search_button.clicked.connect(self.run_search)
        self.search_box.returnPressed.connect(self.run_search)
        self.paper_list.itemSelectionChanged.connect(self.on_paper_selected)
        self.pending_list.itemSelectionChanged.connect(self.on_pending_selected)
        self.result_list.itemSelectionChanged.connect(self.on_result_selected)
        self.save_display_name_button.clicked.connect(self.save_display_name)
        self.reset_display_name_button.clicked.connect(self.reset_display_name)
        self.save_tags_button.clicked.connect(self.save_tags)
        self.save_bibtex_button.clicked.connect(self.save_bibtex)
        self.remove_bibtex_button.clicked.connect(self.remove_bibtex)
        self.save_notes_button.clicked.connect(self.save_notes)
        self.open_pdf_button.clicked.connect(self.open_pdf)
        self.reveal_pdf_button.clicked.connect(self.reveal_pdf)
        self.delete_pdf_button.clicked.connect(self.delete_paper)

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.addWidget(QLabel("Papers"))
        self.paper_list = QListWidget()
        layout.addWidget(self.paper_list, 3)
        layout.addWidget(QLabel("Pending PDFs"))
        self.pending_list = QListWidget()
        layout.addWidget(self.pending_list, 2)
        layout.addWidget(QLabel("Tag filter"))
        self.tag_filter_list = QListWidget()
        self.tag_filter_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        layout.addWidget(self.tag_filter_list, 2)
        return panel

    def _build_middle_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        search_row = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Describe the paper you are trying to find...")
        self.search_button = QPushButton("Search")
        search_row.addWidget(self.search_box, 1)
        search_row.addWidget(self.search_button)
        layout.addLayout(search_row)
        self.result_list = QListWidget()
        layout.addWidget(self.result_list, 1)
        self.evidence_box = QTextEdit()
        self.evidence_box.setReadOnly(True)
        self.evidence_box.setPlaceholderText("Evidence snippets from selected search result.")
        layout.addWidget(self.evidence_box, 1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.detail_tabs = QTabWidget()
        layout.addWidget(self.detail_tabs, 1)

        basic_tab = QWidget()
        basic_layout = QVBoxLayout(basic_tab)
        form = QFormLayout()
        self.title_label = _detail_label()
        self.filename_label = _detail_label()
        self.path_label = _detail_label()
        self.status_label = _detail_label()
        self.bib_summary_label = _detail_label()
        form.addRow("Display name", self.title_label)
        form.addRow("PDF file", self.filename_label)
        form.addRow("Path", self.path_label)
        form.addRow("Status", self.status_label)
        form.addRow("BibTeX", self.bib_summary_label)
        basic_layout.addLayout(form)

        basic_layout.addWidget(QLabel("Display name"))
        self.display_name_edit = QLineEdit()
        self.display_name_edit.setPlaceholderText("Edit the parsed PDF title")
        display_buttons = QHBoxLayout()
        self.save_display_name_button = QPushButton("Save Display Name")
        self.reset_display_name_button = QPushButton("Reset to Parsed Title")
        basic_layout.addWidget(self.display_name_edit)
        display_buttons.addWidget(self.save_display_name_button)
        display_buttons.addWidget(self.reset_display_name_button)
        basic_layout.addLayout(display_buttons)

        pdf_buttons = QHBoxLayout()
        self.open_pdf_button = QPushButton("Open PDF")
        self.reveal_pdf_button = QPushButton("Reveal in Folder")
        self.delete_pdf_button = QPushButton("Delete Paper")
        pdf_buttons.addWidget(self.open_pdf_button)
        pdf_buttons.addWidget(self.reveal_pdf_button)
        pdf_buttons.addWidget(self.delete_pdf_button)
        basic_layout.addLayout(pdf_buttons)
        basic_layout.addStretch(1)

        tags_tab = QWidget()
        tags_layout = QVBoxLayout(tags_tab)
        tags_layout.addWidget(QLabel("Tags (comma-separated)"))
        self.tags_edit = QLineEdit()
        self.save_tags_button = QPushButton("Save Tags")
        tags_layout.addWidget(self.tags_edit)
        tags_layout.addWidget(self.save_tags_button)
        tags_layout.addStretch(1)

        bibtex_tab = QWidget()
        bibtex_layout = QVBoxLayout(bibtex_tab)
        bibtex_layout.addWidget(QLabel("BibTeX"))
        self.bibtex_edit = QTextEdit()
        bibtex_layout.addWidget(self.bibtex_edit, 1)
        bib_buttons = QHBoxLayout()
        self.save_bibtex_button = QPushButton("Save BibTeX")
        self.remove_bibtex_button = QPushButton("Remove BibTeX")
        bib_buttons.addWidget(self.save_bibtex_button)
        bib_buttons.addWidget(self.remove_bibtex_button)
        bibtex_layout.addLayout(bib_buttons)

        notes_tab = QWidget()
        notes_layout = QVBoxLayout(notes_tab)
        notes_layout.addWidget(QLabel("Notes"))
        self.notes_edit = QTextEdit()
        self.notes_edit.setPlaceholderText(
            "Write personal search notes for this paper. Notes are searchable."
        )
        notes_layout.addWidget(self.notes_edit, 1)
        self.save_notes_button = QPushButton("Save Notes")
        notes_layout.addWidget(self.save_notes_button)

        self.detail_tabs.addTab(basic_tab, "Basic")
        self.detail_tabs.addTab(tags_tab, "Tags")
        self.detail_tabs.addTab(bibtex_tab, "BibTeX")
        self.detail_tabs.addTab(notes_tab, "Notes")
        return panel

    def _checked_tags(self) -> tuple[str, ...]:
        tags: list[str] = []
        for index in range(self.tag_filter_list.count()):
            item = self.tag_filter_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                tags.append(item.data(Qt.ItemDataRole.UserRole))
        return tuple(tags)

    def _start_worker(self, worker: Any) -> QThread:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        thread.finished.connect(thread.deleteLater)
        self._threads.append(thread)

        def cleanup() -> None:
            if thread in self._threads:
                self._threads.remove(thread)
            worker.deleteLater()

        thread.finished.connect(cleanup)
        thread.start()
        return thread

    def _set_busy(self, busy: bool, message: str = "") -> None:
        for button in (
            self.setup_button,
            self.add_button,
            self.index_button,
            self.rebuild_button,
            self.refresh_button,
            self.search_button,
            self.save_display_name_button,
            self.reset_display_name_button,
            self.save_tags_button,
            self.save_bibtex_button,
            self.remove_bibtex_button,
            self.save_notes_button,
            self.delete_pdf_button,
        ):
            button.setEnabled(not busy)
        if message:
            self.statusBar().showMessage(message)

    def _select_list_item(self, list_widget: QListWidget, relative_path: str) -> bool:
        for index in range(list_widget.count()):
            item = list_widget.item(index)
            if str(item.data(Qt.ItemDataRole.UserRole)) == relative_path:
                list_widget.setCurrentItem(item)
                return True
        return False

    def refresh(self, *, select_relative_path: str | None = None) -> None:
        database.initialize(self.settings.database_path)
        desired_selection = select_relative_path or self.current_relative_path
        self.paper_list.clear()
        self.pending_list.clear()
        self.tag_filter_list.clear()
        with database.connect(self.settings.database_path) as connection:
            papers = database.list_papers(connection)
            tags = database.list_tags(connection)
            existing_by_path = database.fetch_papers_by_path(connection)

        for row in papers:
            status = str(row["status"])
            suffix = "" if status == "ready" else f" [{status}]"
            item = QListWidgetItem(f"{_display_title(row)}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, str(row["relative_path"]))
            self.paper_list.addItem(item)

        snapshots = scan_library(self.settings)
        pending_count = 0
        for snapshot in snapshots:
            row = existing_by_path.get(snapshot.relative_path)
            reason = pending_reason_for_snapshot(
                row,
                snapshot,
                verify_hash=True,
            )
            if not reason:
                continue
            pending_count += 1
            item = QListWidgetItem(f"{reason}\n{snapshot.relative_path}")
            item.setData(Qt.ItemDataRole.UserRole, snapshot.relative_path)
            self.pending_list.addItem(item)

        for row in tags:
            item = QListWidgetItem(f"{row['name']} ({row['paper_count']})")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, str(row["name"]))
            self.tag_filter_list.addItem(item)

        pdf_count = len(snapshots)
        model_status = "installed" if model_is_installed(self.settings.model_dir) else "missing"
        self.statusBar().showMessage(
            f"{len(papers)} tracked records, {pdf_count} PDFs in library, "
            f"{pending_count} pending, model {model_status}."
        )
        if desired_selection:
            self._select_list_item(
                self.paper_list,
                desired_selection,
            ) or self._select_list_item(
                self.pending_list,
                desired_selection,
            )

    def run_setup(self) -> None:
        self._set_busy(True, "Running setup...")
        worker = SetupWorker(self.settings)
        worker.message.connect(lambda message: self.statusBar().showMessage(message))
        worker.finished.connect(self._setup_finished)
        thread = self._start_worker(worker)
        worker.finished.connect(thread.quit)

    def _setup_finished(self, ok: bool, message: str) -> None:
        self._set_busy(False)
        self.refresh()
        if ok:
            QMessageBox.information(self, "Recallary setup", message)
        else:
            QMessageBox.critical(self, "Recallary setup failed", message)

    def run_index(self, *, rebuild: bool) -> None:
        label = "Rebuilding index..." if rebuild else "Indexing library..."
        self._set_busy(True, label)
        self.result_list.clear()
        self.evidence_box.clear()
        worker = IndexWorker(self.settings, rebuild=rebuild)
        worker.progress.connect(
            lambda current, total, path: self.statusBar().showMessage(
                f"[{current}/{total}] {path}"
            )
        )
        worker.finished.connect(self._index_finished)
        thread = self._start_worker(worker)
        worker.finished.connect(thread.quit)

    def _index_finished(self, ok: bool, summary: Any, error: str) -> None:
        self._set_busy(False)
        self.refresh()
        if not ok:
            QMessageBox.critical(self, "Index failed", error)
            return
        message = (
            f"Index complete: {summary.indexed} indexed, "
            f"{summary.metadata_updated} metadata-only updates, "
            f"{summary.unchanged} unchanged, {summary.removed} removed, "
            f"{summary.failed} failed."
        )
        if summary.failures:
            message += "\n\n" + "\n".join(
                f"{path}: {failure}" for path, failure in summary.failures[:20]
            )
        QMessageBox.information(self, "Index complete", message)

    def run_search(self) -> None:
        query = self.search_box.text().strip()
        if not query:
            QMessageBox.warning(self, "Search", "Enter a search description first.")
            return
        self._set_busy(True, "Searching...")
        self.result_list.clear()
        self.evidence_box.clear()
        worker = SearchWorker(
            self.settings,
            query,
            limit=DEFAULT_LIMIT,
            tag_names=self._checked_tags(),
        )
        worker.finished.connect(self._search_finished)
        thread = self._start_worker(worker)
        worker.finished.connect(thread.quit)

    def _search_finished(self, ok: bool, results: object, error: str) -> None:
        self._set_busy(False)
        if not ok:
            QMessageBox.critical(self, "Search failed", error)
            return
        self.result_list.clear()
        for rank, result in enumerate(results, start=1):
            assert isinstance(result, SearchResult)
            tags = f" | tags: {', '.join(result.tags)}" if result.tags else ""
            item = QListWidgetItem(
                f"{rank}. {result.title}\n{result.relative_path}{tags}"
            )
            item.setData(Qt.ItemDataRole.UserRole, result)
            self.result_list.addItem(item)
        self.statusBar().showMessage(f"{self.result_list.count()} search results.")

    def on_result_selected(self) -> None:
        selected = self.result_list.selectedItems()
        if not selected:
            return
        result = selected[0].data(Qt.ItemDataRole.UserRole)
        if not isinstance(result, SearchResult):
            return
        self.current_result = result
        self.show_paper(result.relative_path, result=result)

    def on_paper_selected(self) -> None:
        selected = self.paper_list.selectedItems()
        if not selected:
            return
        self.pending_list.clearSelection()
        relative_path = selected[0].data(Qt.ItemDataRole.UserRole)
        self.current_result = None
        self.show_paper(str(relative_path))

    def on_pending_selected(self) -> None:
        selected = self.pending_list.selectedItems()
        if not selected:
            return
        self.paper_list.clearSelection()
        relative_path = selected[0].data(Qt.ItemDataRole.UserRole)
        self.current_result = None
        self.show_paper(str(relative_path))

    def show_paper(
        self,
        relative_path: str,
        *,
        result: SearchResult | None = None,
    ) -> None:
        self.current_relative_path = relative_path
        with database.connect(self.settings.database_path) as connection:
            row = database.fetch_paper_by_relative_path(connection, relative_path)
            if row is None:
                self.title_label.setText(Path(relative_path).name)
                self.filename_label.setText(Path(relative_path).name)
                self.path_label.setText(relative_path)
                self.status_label.setText("not indexed yet")
                self.display_name_edit.clear()
                self.tags_edit.clear()
                self.bibtex_edit.clear()
                self.notes_edit.clear()
                self.bib_summary_label.setText("none")
                self.evidence_box.setPlainText(
                    "This PDF is in library/ but has not been indexed yet.\n\n"
                    "Click Index Library to make it searchable and enable "
                    "tags/BibTeX/notes."
                )
                return
            tags = database.tags_for_paper(connection, int(row["id"]))
            bibtex_row = database.bibtex_for_paper(connection, int(row["id"]))
            note_row = database.note_for_paper(connection, int(row["id"]))

        self.title_label.setText(_display_title(row))
        self.filename_label.setText(str(row["filename"] or Path(relative_path).name))
        self.path_label.setText(relative_path)
        self.status_label.setText(str(row["status"]))
        self.display_name_edit.setText(str(row["display_name"] or "") or _parsed_title(row))
        self.tags_edit.setText(", ".join(tags))
        if bibtex_row:
            self.bibtex_edit.setPlainText(str(bibtex_row["raw_bibtex"]))
            summary = " ".join(
                part
                for part in (
                    str(bibtex_row["citekey"] or ""),
                    str(bibtex_row["year"] or ""),
                )
                if part
            )
            self.bib_summary_label.setText(summary or "saved")
        else:
            self.bibtex_edit.clear()
            self.bib_summary_label.setText("none")
        self.notes_edit.setPlainText(str(note_row["content"]) if note_row else "")

        if result:
            lines: list[str] = []
            for evidence in result.evidence:
                if evidence.source_type == "pdf":
                    lines.append(f"PDF page {evidence.page_number}")
                else:
                    lines.append("Note evidence")
                lines.append(evidence.text)
                lines.append("")
            if result.bibtex and isinstance(result.bibtex, BibTeXInfo):
                pass
            self.evidence_box.setPlainText("\n".join(lines).strip())

    def save_display_name(self) -> None:
        if not self.current_relative_path:
            QMessageBox.warning(
                self, "Display Name", "Select an indexed paper first."
            )
            return
        self.statusBar().showMessage("Saving display name...")
        try:
            with database.connect(self.settings.database_path) as connection:
                row = database.fetch_paper_by_relative_path(
                    connection, self.current_relative_path
                )
                if row is None:
                    raise ValueError("The selected paper is not indexed.")
                typed_name = " ".join(self.display_name_edit.text().strip().split())
                parsed_title = " ".join(_parsed_title(row).strip().split())
                display_name = "" if typed_name == parsed_title else typed_name
                database.set_display_name_for_paper(
                    connection,
                    self.current_relative_path,
                    display_name,
                )
            current = self.current_relative_path
            self.refresh(select_relative_path=current)
            self.show_paper(current)
            self.statusBar().showMessage("Display name saved.")
        except Exception as error:
            self.statusBar().showMessage("Could not save display name.")
            QMessageBox.critical(self, "Could not save display name", str(error))

    def reset_display_name(self) -> None:
        if not self.current_relative_path:
            QMessageBox.warning(
                self, "Display Name", "Select an indexed paper first."
            )
            return
        self.statusBar().showMessage("Resetting display name...")
        try:
            with database.connect(self.settings.database_path) as connection:
                row = database.fetch_paper_by_relative_path(
                    connection, self.current_relative_path
                )
                if row is None:
                    raise ValueError("The selected paper is not indexed.")
                database.set_display_name_for_paper(
                    connection,
                    self.current_relative_path,
                    "",
                )
            current = self.current_relative_path
            self.refresh(select_relative_path=current)
            self.show_paper(current)
            self.statusBar().showMessage("Display name reset to parsed title.")
        except Exception as error:
            self.statusBar().showMessage("Could not reset display name.")
            QMessageBox.critical(self, "Could not reset display name", str(error))

    def save_tags(self) -> None:
        if not self.current_relative_path:
            QMessageBox.warning(self, "Tags", "Select an indexed paper first.")
            return
        self.statusBar().showMessage("Saving tags...")
        desired = {
            database.normalize_tag_name(tag)
            for tag in self.tags_edit.text().split(",")
            if database.normalize_tag_name(tag)
        }
        try:
            with database.connect(self.settings.database_path) as connection:
                row = database.fetch_paper_by_relative_path(
                    connection, self.current_relative_path
                )
                if row is None:
                    raise ValueError("The selected paper is not indexed.")
                current = set(database.tags_for_paper(connection, int(row["id"])))
                for tag in sorted(desired - current):
                    database.add_tag_to_paper(connection, self.current_relative_path, tag)
                for tag in sorted(current - desired):
                    database.remove_tag_from_paper(
                        connection, self.current_relative_path, tag
                    )
            current = self.current_relative_path
            self.refresh(select_relative_path=current)
            self.show_paper(current)
            self.statusBar().showMessage("Tags saved.")
        except Exception as error:
            self.statusBar().showMessage("Could not save tags.")
            QMessageBox.critical(self, "Could not save tags", str(error))

    def save_bibtex(self) -> None:
        if not self.current_relative_path:
            QMessageBox.warning(self, "BibTeX", "Select an indexed paper first.")
            return
        raw = self.bibtex_edit.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "BibTeX", "BibTeX text is empty.")
            return
        self.statusBar().showMessage("Saving BibTeX...")
        try:
            parsed = parse_bibtex(raw)
            with database.connect(self.settings.database_path) as connection:
                database.save_bibtex_for_paper(
                    connection,
                    self.current_relative_path,
                    raw_bibtex=raw,
                    citekey=parsed["citekey"],
                    entry_type=parsed["entry_type"],
                    title=parsed["title"],
                    authors=parsed["authors"],
                    year=parsed["year"],
                )
            self.show_paper(self.current_relative_path)
            self.statusBar().showMessage("BibTeX saved.")
        except Exception as error:
            self.statusBar().showMessage("Could not save BibTeX.")
            QMessageBox.critical(self, "Could not save BibTeX", str(error))

    def remove_bibtex(self) -> None:
        if not self.current_relative_path:
            QMessageBox.warning(self, "BibTeX", "Select an indexed paper first.")
            return
        self.statusBar().showMessage("Removing BibTeX...")
        try:
            with database.connect(self.settings.database_path) as connection:
                database.remove_bibtex_from_paper(
                    connection, self.current_relative_path
                )
            self.show_paper(self.current_relative_path)
            self.statusBar().showMessage("BibTeX removed.")
        except Exception as error:
            self.statusBar().showMessage("Could not remove BibTeX.")
            QMessageBox.critical(self, "Could not remove BibTeX", str(error))

    def save_notes(self) -> None:
        if not self.current_relative_path:
            QMessageBox.warning(self, "Notes", "Select an indexed paper first.")
            return
        content = self.notes_edit.toPlainText().strip()
        relative_path = self.current_relative_path
        self._set_busy(True, "Saving notes...")
        worker = SaveNotesWorker(self.settings, relative_path, content)
        worker.finished.connect(self._notes_saved)
        thread = self._start_worker(worker)
        worker.finished.connect(thread.quit)

    def _notes_saved(self, ok: bool, result: object, error: str) -> None:
        self._set_busy(False)
        if not ok:
            QMessageBox.critical(self, "Could not save notes", error)
            return

        assert isinstance(result, dict)
        relative_path = str(result["relative_path"])
        content = str(result["content"])
        semantic_enabled = bool(result["semantic_enabled"])
        self.refresh(select_relative_path=relative_path)
        self.show_paper(relative_path)
        if content and not semantic_enabled:
            self.statusBar().showMessage(
                "Notes saved for keyword search. Run setup to enable semantic note search."
            )
        else:
            self.statusBar().showMessage("Notes saved.")

    def add_pdfs(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add PDFs to Recallary",
            str(Path.home()),
            "PDF files (*.pdf)",
        )
        if not files:
            return
        self._set_busy(True, "Adding PDFs...")
        worker = AddPdfsWorker(self.settings, files)
        worker.progress.connect(
            lambda current, total, name: self.statusBar().showMessage(
                f"Adding PDF [{current}/{total}] {name}"
            )
        )
        worker.finished.connect(self._pdfs_added)
        thread = self._start_worker(worker)
        worker.finished.connect(thread.quit)

    def _pdfs_added(self, ok: bool, result: object, error: str) -> None:
        self._set_busy(False)
        if not ok:
            QMessageBox.critical(self, "Could not add PDFs", error)
            return

        assert isinstance(result, dict)
        added = int(result["added"])
        added_paths = list(result["added_paths"])
        errors = list(result["errors"])
        self.refresh()
        message = f"Added {added} PDF(s) to library."
        if added:
            message += "\n\nNew PDFs are shown under Pending PDFs until you index them."
            message += "\n\n" + "\n".join(added_paths[:20])
            if len(added_paths) > 20:
                message += f"\n... and {len(added_paths) - 20} more"
        if errors:
            message += "\n\n" + "\n".join(errors[:10])
        QMessageBox.information(self, "Add PDFs", message)
        if errors:
            self.statusBar().showMessage(
                f"Added {added} PDF(s) with {len(errors)} error(s)."
            )
        else:
            self.statusBar().showMessage(f"Added {added} PDF(s) to library.")

    def _current_pdf_path(self) -> Path | None:
        if not self.current_relative_path:
            return None
        return self.settings.root / self.current_relative_path

    def open_pdf(self) -> None:
        path = self._current_pdf_path()
        if not path or not path.is_file():
            QMessageBox.warning(self, "Open PDF", "Select an existing PDF first.")
            return
        self.statusBar().showMessage("Opening PDF...")
        QDesktopServices.openUrl(QUrl.fromLocalFile(os.fspath(path)))
        self.statusBar().showMessage("Open PDF request sent.")

    def reveal_pdf(self) -> None:
        path = self._current_pdf_path()
        if not path or not path.is_file():
            QMessageBox.warning(self, "Reveal PDF", "Select an existing PDF first.")
            return
        self.statusBar().showMessage("Revealing PDF in folder...")
        _open_in_file_manager(path)
        self.statusBar().showMessage("Reveal in folder request sent.")

    def delete_paper(self) -> None:
        if not self.current_relative_path:
            QMessageBox.warning(self, "Delete Paper", "Select an indexed paper first.")
            return

        path = self._current_pdf_path()
        message = (
            "Delete this paper from Recallary?\n\n"
            f"{self.current_relative_path}\n\n"
            "The PDF will be moved to data/trash/ and its index, tags, BibTeX, "
            "and notes will be removed from the database."
        )
        if path and not path.exists():
            message = (
                "This PDF file is missing from library/. Remove its database record?\n\n"
                f"{self.current_relative_path}"
            )
        answer = QMessageBox.question(
            self,
            "Delete Paper",
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        self.statusBar().showMessage("Deleting paper...")
        try:
            paper_id: int | None = None
            with database.connect(self.settings.database_path) as connection:
                row = database.fetch_paper_by_relative_path(
                    connection, self.current_relative_path
                )
                if row is not None:
                    paper_id = int(row["id"])

            if path and path.exists():
                timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                trash_dir = self.settings.data_dir / "trash" / timestamp
                trash_dir.mkdir(parents=True, exist_ok=True)
                destination = _unique_destination(trash_dir, path.name)
                shutil.move(os.fspath(path), os.fspath(destination))

            if paper_id is not None:
                with database.connect(self.settings.database_path) as connection:
                    database.remove_papers(connection, [paper_id])

            self.current_relative_path = None
            self.current_result = None
            self.title_label.clear()
            self.filename_label.clear()
            self.path_label.clear()
            self.status_label.clear()
            self.bib_summary_label.clear()
            self.display_name_edit.clear()
            self.tags_edit.clear()
            self.bibtex_edit.clear()
            self.notes_edit.clear()
            self.evidence_box.clear()
            self.result_list.clear()
            self.refresh()
            self.statusBar().showMessage("Paper moved to data/trash/ and removed from index.")
        except Exception as error:
            self.statusBar().showMessage("Could not delete paper.")
            QMessageBox.critical(self, "Could not delete paper", str(error))

from pathlib import Path

import numpy as np
import pymupdf

from recallary.config import Settings
from recallary.indexing.indexer import index_library
from recallary.search.engine import search_library


def _write_pdf(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = pymupdf.open()
    page = document.new_page()
    page.insert_textbox(
        pymupdf.Rect(50, 50, 545, 790),
        f"{title}\n\nAbstract\n\n{body}",
        fontsize=10,
    )
    document.set_metadata({"title": title})
    document.save(path)
    document.close()


def _vector(text: str) -> np.ndarray:
    lowered = text.lower()
    values = np.array(
        [
            lowered.count("impedance") + lowered.count("metabolic"),
            lowered.count("transformer") + lowered.count("anomaly"),
            0.1,
        ],
        dtype=np.float32,
    )
    return values / np.linalg.norm(values)


class FakeEmbedder:
    def __init__(self, settings: Settings):
        self.settings = settings

    def encode_passages(self, texts: list[str]) -> np.ndarray:
        return np.vstack([_vector(text) for text in texts])

    def encode_query(self, query: str) -> np.ndarray:
        return _vector(query)


def test_incremental_index_and_hybrid_search(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path)
    settings.ensure_directories()
    _write_pdf(
        settings.library_dir / "ankle.pdf",
        "Adaptive Impedance Control for an Ankle Exoskeleton",
        (
            "We designed an adaptive impedance controller for an ankle "
            "exoskeleton. Validation measured metabolic cost during treadmill "
            "walking. "
        )
        * 8,
    )
    _write_pdf(
        settings.library_dir / "anomaly.pdf",
        "Transformer Time Series Anomaly Detection",
        (
            "A transformer architecture detects anomalies in multivariate "
            "time series using attention. "
        )
        * 10,
    )

    first = index_library(settings, embedder_factory=FakeEmbedder)
    second = index_library(settings, embedder_factory=FakeEmbedder)
    results = search_library(
        settings,
        "ankle exoskeleton impedance controller metabolic cost",
        embedder=FakeEmbedder(settings),
    )

    assert first.indexed == 2
    assert first.failed == 0
    assert second.unchanged == 2
    assert second.indexed == 0
    assert results
    assert results[0].relative_path == "library/ankle.pdf"
    assert results[0].evidence[0].page_number == 1


def test_deleted_pdf_is_removed_from_index(tmp_path: Path) -> None:
    settings = Settings(root=tmp_path)
    settings.ensure_directories()
    path = settings.library_dir / "paper.pdf"
    _write_pdf(
        path,
        "Paper",
        "An impedance controller was validated with metabolic cost. " * 12,
    )
    index_library(settings, embedder_factory=FakeEmbedder)

    path.unlink()
    summary = index_library(settings, embedder_factory=FakeEmbedder)

    assert summary.removed == 1

from pathlib import Path

import pymupdf

from recallary.indexing.parser import parse_pdf


def _write_pdf(path: Path, pages: list[str]) -> None:
    document = pymupdf.open()
    for text in pages:
        page = document.new_page()
        page.insert_textbox(
            pymupdf.Rect(50, 50, 545, 790),
            text,
            fontsize=11,
        )
    document.save(path)
    document.close()


def test_parse_pdf_preserves_pdf_page_numbers(tmp_path: Path) -> None:
    path = tmp_path / "paper.pdf"
    _write_pdf(
        path,
        [
            "A Specific Paper Title\n\nAbstract\n" + "first page text " * 20,
            "Methods\n\n" + "second page text " * 20,
        ],
    )

    paper = parse_pdf(path)

    assert paper.page_count == 2
    assert [page.page_number for page in paper.pages] == [1, 2]
    assert "second page text" in paper.pages[1].clean_text


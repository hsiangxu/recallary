from recallary.domain import ParsedPage, ParsedPaper
from recallary.indexing.chunker import chunk_paper


def test_chunks_stay_on_their_source_page() -> None:
    paper = ParsedPaper(
        title="Example",
        authors="",
        page_count=2,
        pages=[
            ParsedPage(
                page_number=1,
                raw_text="",
                clean_text="Introduction\n\n" + "alpha " * 300,
            ),
            ParsedPage(
                page_number=2,
                raw_text="",
                clean_text="Methods\n\n" + "beta " * 300,
            ),
        ],
    )

    chunks = chunk_paper(paper)

    assert chunks
    assert {chunk.page_number for chunk in chunks} == {1, 2}
    assert all(
        not ("alpha" in chunk.text and "beta" in chunk.text)
        for chunk in chunks
    )


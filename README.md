# Recallary

Recallary is a lightweight local desktop tool for finding papers you have
already saved, even when you remember only a vague description. It returns a
ranked list of likely PDFs with file paths, PDF page numbers, and source
excerpts.

Recallary does not generate answers or summaries. Evidence shown in search
results is extracted directly from your PDFs.

## Current scope

- Stores your PDFs under `library/`
- Stores the database, model, caches, logs, and runtime files under `data/`
- Opens as a local PySide6 desktop GUI
- Recursively indexes text-based PDFs in `library/`
- Updates only new, changed, or deleted files
- Combines SQLite full-text search with a small multilingual semantic model
- Runs the semantic model on CPU
- Supports manual paper tags
- Supports manually attached BibTeX entries
- Supports Windows and Apple Silicon macOS through separate local Conda
  environments
- Does not perform OCR

## Project storage

```text
recallary/
├─ library/       # Put PDFs here
├─ data/          # Database, model, caches, logs, and runtime files
├─ src/
├─ tests/
├─ environment.yml
└─ pyproject.toml
```

`library/` and `data/` are ignored by Git. They can still be synchronized by
OneDrive.

Except for the Conda environment, files created or downloaded for Recallary
are kept inside this repository. Recallary redirects Hugging Face,
Sentence Transformers, PyTorch, and temporary-file caches into `data/`.

## Install

From the repository root:

```bash
conda env create -f environment.yml
conda activate recallary
```

If the environment already exists and `environment.yml` changed:

```bash
conda env update -n recallary -f environment.yml
conda activate recallary
```

Each computer creates its own Conda environment. Do not synchronize Conda
environment directories through OneDrive.

## Start the GUI

```bash
recallary
```

The GUI lets you:

- run setup and download the local model
- add PDFs into `library/`
- see unindexed or changed PDFs under `Pending PDFs`
- index or rebuild the library
- search with vague natural-language descriptions
- filter search by manual tags
- edit the display name shown for a paper
- edit tags for selected papers
- attach or remove BibTeX entries
- open a PDF with the system PDF reader
- reveal a PDF in the file manager
- delete a paper by moving its PDF to `data/trash/` and removing its index

## First setup

In the GUI, click:

```text
Setup / Check Model
```

This:

- creates `library/` and `data/`
- initializes `data/recallary.db`
- downloads `intfloat/multilingual-e5-small` into `data/models/`

The model download is the only normal Recallary operation that requires
network access. Setup does not index PDFs.

## Normal use

1. Open Recallary with `recallary`.
2. Add PDFs with `Add PDFs`, or manually copy PDFs under `library/`.
3. Confirm newly added PDFs appear under `Pending PDFs`.
4. Click `Index Library`.
5. Select a paper and add tags or BibTeX if useful.
6. Search with a description such as:

```text
ankle exoskeleton using impedance control and metabolic cost
```

Search results show likely papers, tags, BibTeX hints, page numbers, and
evidence snippets.

`Display name` lets you override how a paper appears in the GUI and search
results. The field is prefilled from the title extracted from the PDF, so you
can edit it directly. Saving the same text as the extracted title keeps the
manual override empty. Use `Reset to Parsed Title` to discard a manual display
name and return to the extracted title.

`Delete Paper` does not permanently erase the PDF immediately. It moves the
file from `library/` into `data/trash/` and removes the paper's index, tags,
and BibTeX entry from the database.


## CLI fallback

The GUI is the default, but command-line commands are still available.

```bash
recallary status
recallary index
recallary index --rebuild
recallary search "AI feedback in education with limited effects"
recallary search "impedance controller validation" --tag controller-design
```

You can also use the explicit CLI entry point:

```bash
recallary-cli status
```

Manual tags:

```bash
recallary tag add "library/paper.pdf" controller-design
recallary tag remove "library/paper.pdf" controller-design
recallary tag list
recallary tag show "library/paper.pdf"
```

BibTeX:

```bash
recallary bib add "library/paper.pdf" --file citation.bib
recallary bib show "library/paper.pdf"
recallary bib remove "library/paper.pdf"
```

## Indexing behavior

Ordinary indexing is incremental:

- new PDF: index it
- changed PDF: replace that paper's index after successful processing
- unchanged PDF: skip it
- deleted PDF: remove its index
- failed PDF: report the error and continue

Rebuild reindexes every PDF. Manual tags and BibTeX entries are preserved by
matching them back to PDFs through their repository-relative paths.

## OneDrive use

The repository, PDFs, model, and SQLite database may be synchronized between
Windows and macOS. Use Recallary on only one computer at a time:

1. Exit Recallary on the current computer.
2. Wait for OneDrive synchronization to finish.
3. Wait for synchronization on the other computer.
4. Start Recallary there.

PDF paths stored in the database are relative to the repository, so Windows
and macOS can share the same index.

## Limitations

- Image-only or scanned PDFs are reported as `no_text`; OCR is not included.
- PDF extraction quality depends on the document's text layer and layout.
- Search works best when the description includes distinctive details such as
  method, device, population, experiment, metric, or result.
- The GUI opens PDFs in your system PDF reader; it does not include an embedded
  PDF reader.

## Remove

Delete this repository to remove Recallary's PDFs, database, downloaded model,
caches, logs, and runtime files. Remove the external Conda environment
separately:

```bash
conda env remove -n recallary
```

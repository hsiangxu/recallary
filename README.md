# Recallary

Recallary is a local desktop tool for finding papers you have already saved,
even when you only remember a vague description.

It is designed for one main job:

```text
Search your own local PDF library and return likely matching papers with
file paths, page numbers, and evidence snippets.
```

Recallary is not a chatbot, Zotero replacement, citation manager, or general
knowledge base. It does not generate answers or summaries. Search evidence is
extracted from your PDFs, or clearly marked as your own note evidence.

## What Recallary stores

```text
recallary/
|- Recallary.vbs   # Optional Windows double-click launcher
|- Recallary.app/  # Optional macOS double-click launcher
|- library/        # Your PDFs
|- data/           # Database, model, caches, logs, runtime files, trash
|- src/
|- tests/
|- environment.yml
`- pyproject.toml
```

Everything Recallary creates is kept inside this repository except the Conda
environment.

- PDFs go in `library/`
- database/model/cache/log/runtime files go in `data/`
- deleted PDFs are moved to `data/trash/`
- generated launchers stay in the repo root
- `library/`, `data/`, and generated launchers are ignored by Git

Each computer should have its own Conda environment. Do not synchronize Conda
environment folders through OneDrive.

## Install or update the Conda environment

First-time setup from the repository root:

```bash
conda env create -f environment.yml
conda activate recallary
```

If the environment already exists and `environment.yml` changed:

```bash
conda env update -n recallary -f environment.yml
conda activate recallary
```

If command entry points seem stale after code changes, refresh the editable
install:

```bash
python -m pip install -e .
```

## Start Recallary

From an activated Conda environment:

```bash
recallary
```

To create a double-click launcher for the current computer:

```bash
recallary make-launcher
```

This creates one launcher in the repository root:

- Windows: `Recallary.vbs`
- macOS: `Recallary.app`

The launcher records the current Conda environment's Python path. Re-run
`recallary make-launcher` if the Conda environment path or repository path
changes.

Launcher logs are written to:

- Windows: `data/logs/launcher-windows.log`
- macOS: `data/logs/launcher-macos.log`

The launcher prevents multiple GUI instances from starting at the same time. It
also waits for the GUI window to report that it is ready. If startup fails, the
launcher writes to the log, tries to show a system message box, and terminates
the failed GUI process instead of leaving it in the background.

## First setup inside the GUI

Open Recallary, then click:

```text
Setup / Check Model
```

This:

- creates `library/` and `data/`
- initializes `data/recallary.db`
- downloads `intfloat/multilingual-e5-small` into `data/models/`

The model download is the only normal Recallary operation that requires network
access. Setup does not index PDFs.

## Daily workflow

1. Open Recallary.
2. Add PDFs with `Add PDFs`, or manually copy PDFs under `library/`.
3. Confirm new PDFs appear under `Pending PDFs`.
4. Click `Index Library`.
5. Search with a description, for example:

```text
ankle exoskeleton using impedance control and metabolic cost
```

Search results show likely papers with:

- relative PDF path
- page-numbered PDF evidence
- note evidence, if your personal notes matched
- tags and BibTeX hints, when available

PDF matches are shown as:

```text
PDF page 7
```

Personal note matches are shown as:

```text
Note evidence
```

## Editing paper information

Select a paper in the GUI. The right side has tabs:

- `Basic`
- `Tags`
- `BibTeX`
- `Notes`

### Basic

Use `Display name` to change how a paper appears in the GUI and search results.
The field is prefilled from the title extracted from the PDF, so you can edit
from that starting point.

- `Save Display Name` saves your manual display name
- `Reset to Parsed Title` removes your manual override and returns to the PDF
  title
- `Open PDF` opens the PDF with your system PDF reader
- `Reveal in Folder` shows the PDF in the file manager
- `Delete Paper` moves the PDF to `data/trash/` and removes its index, tags,
  BibTeX entry, and notes from the database

### Tags

Tags are manual filters for narrowing search.

Example tags:

```text
controller-design
metrics
validation
education
```

Search can be filtered by selected tags in the GUI, or from CLI with:

```bash
recallary search "impedance controller validation" --tag controller-design
```

### BibTeX

Paste or edit a BibTeX entry for the selected paper. BibTeX is mainly for
identification and citation. It is not treated as PDF evidence.

### Notes

Notes are your own searchable text linked to a paper. Notes participate in
search, but they are not treated as PDF text and do not get page numbers.

If notes match a query, Recallary displays them as `Note evidence`, so it is
clear that the text came from you rather than the PDF.

To delete notes:

1. Clear the Notes text box.
2. Click `Save Notes`.

## Indexing behavior

Ordinary indexing is incremental:

- new PDF: index it
- changed PDF: replace that paper's index after successful processing
- unchanged PDF: skip it
- deleted PDF: remove its index
- failed PDF: report the error and continue

`Rebuild Index` reindexes every PDF. Manual display names, tags, BibTeX
entries, and notes are preserved by matching them back to PDFs through their
repository-relative paths.

## OneDrive use

The repository, PDFs, model, and SQLite database may be synchronized between
Windows and macOS.

Use Recallary on only one computer at a time:

1. Exit Recallary on the current computer.
2. Wait for OneDrive synchronization to finish.
3. Wait for synchronization on the other computer.
4. Start Recallary there.

PDF paths stored in the database are relative to the repository, so Windows and
macOS can share the same index.

## Troubleshooting

### Double-click launcher does not open the GUI

Check the launcher log:

- Windows: `data/logs/launcher-windows.log`
- macOS: `data/logs/launcher-macos.log`

If the Conda environment or repository path changed, recreate the launcher:

```bash
conda activate recallary
recallary make-launcher
```

### Recallary says another instance is already running

Close the existing Recallary GUI window first. If no window is visible, check
the launcher log. The launcher is designed to prevent multiple GUI instances.

### New PDFs are not searchable

Make sure they appear under `Pending PDFs`, then click:

```text
Index Library
```

### A PDF is reported as `no_text`

The PDF probably has no extractable text layer. Recallary does not perform OCR.

### Search quality is poor

Search works best when your description includes distinctive details such as:

- method
- device
- population
- experiment
- metric
- result

Personal Notes can also help you find papers later when your memory of the PDF
text is vague.

## CLI fallback

The GUI is the default, but command-line commands are still available:

```bash
recallary status
recallary index
recallary index --rebuild
recallary search "AI feedback in education with limited effects"
recallary search "impedance controller validation" --tag controller-design
```

Explicit CLI entry point:

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

## Limitations

- Image-only or scanned PDFs are reported as `no_text`; OCR is not included.
- PDF extraction quality depends on the document's text layer and layout.
- Search works best when the description includes distinctive paper-specific
  details.
- The GUI opens PDFs in your system PDF reader; it does not include an embedded
  PDF reader.

## Remove Recallary

Delete this repository to remove Recallary's PDFs, database, downloaded model,
caches, logs, runtime files, trash, and generated launchers.

Remove the external Conda environment separately:

```bash
conda env remove -n recallary
```

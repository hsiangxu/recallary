# Recallary

Recallary is a lightweight local tool for finding papers you have already
saved, even when you remember only a vague description. It returns a ranked
list of likely PDFs with file paths, PDF page numbers, and source excerpts.

Recallary does not generate answers or summaries. The evidence shown in search
results is extracted directly from the PDFs.

## Current scope

- Recursively indexes text-based PDFs in `library/`
- Updates only new, changed, or deleted files
- Combines SQLite full-text search with a small multilingual semantic model
- Runs the semantic model on CPU
- Stores PDFs, indexes, the model, caches, logs, and temporary files inside
  this repository
- Supports Windows and Apple Silicon macOS through separate local Conda
  environments
- Does not perform OCR

## Project storage

```text
recallary/
├── library/       # Put PDFs here
├── data/          # Database, model, caches, logs, and runtime files
├── src/
├── tests/
├── environment.yml
└── pyproject.toml
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

Each computer creates its own Conda environment. Do not synchronize Conda
environment directories through OneDrive.

## First setup

```bash
recallary setup
```

This command:

- creates `library/` and `data/`
- initializes `data/recallary.db`
- downloads `intfloat/multilingual-e5-small` into `data/models/`

The model download is the only normal Recallary operation that requires
network access. `setup` does not index PDFs.

## Use

Place PDFs anywhere under `library/`, including subdirectories, then run:

```bash
recallary index
```

Search with a natural-language description:

```bash
recallary search "ankle exoskeleton using impedance control and metabolic cost"
```

Limit the number of returned papers:

```bash
recallary search "AI feedback in education with limited effects" --limit 5
```

Inspect the library and database:

```bash
recallary status
```

Rebuild the entire index:

```bash
recallary index --rebuild
```

Ordinary `index` runs are incremental:

- new PDF: index it
- changed PDF: replace that paper's index after successful processing
- unchanged PDF: skip it
- deleted PDF: remove its index
- failed PDF: report the error and continue

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
- Search works best when the description includes a combination of distinctive
  details such as method, device, population, experiment, metric, or result.

## Remove

Delete this repository to remove Recallary's PDFs, database, downloaded model,
caches, logs, and runtime files. Remove the external Conda environment
separately:

```bash
conda env remove -n recallary
```

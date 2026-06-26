from __future__ import annotations

import re


def _clean_value(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] in "{\"" and text[-1] in "}\"":
        text = text[1:-1].strip()
    return re.sub(r"\s+", " ", text)


def _parse_with_regex(raw_bibtex: str) -> dict[str, str]:
    result = {
        "citekey": "",
        "entry_type": "",
        "title": "",
        "authors": "",
        "year": "",
    }
    header = re.search(r"@\s*([A-Za-z]+)\s*\{\s*([^,\s]+)", raw_bibtex)
    if header:
        result["entry_type"] = header.group(1).strip().lower()
        result["citekey"] = header.group(2).strip()

    for field, target in (
        ("title", "title"),
        ("author", "authors"),
        ("year", "year"),
    ):
        match = re.search(
            rf"\b{field}\s*=\s*(\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}}|\"[^\"]*\"|[^,\n]+)",
            raw_bibtex,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if match:
            result[target] = _clean_value(match.group(1))
    return result


def parse_bibtex(raw_bibtex: str) -> dict[str, str]:
    """Return common BibTeX fields while preserving the original raw entry elsewhere."""
    raw = raw_bibtex.strip()
    if not raw:
        return {
            "citekey": "",
            "entry_type": "",
            "title": "",
            "authors": "",
            "year": "",
        }

    try:
        import bibtexparser  # type: ignore[import-not-found]

        library = bibtexparser.parse_string(raw)
        entries = list(getattr(library, "entries", []) or [])
        if entries:
            entry = entries[0]
            fields = {
                str(getattr(field, "key", "")).lower(): getattr(field, "value", "")
                for field in getattr(entry, "fields", [])
            }
            return {
                "citekey": _clean_value(getattr(entry, "key", "")),
                "entry_type": _clean_value(getattr(entry, "entry_type", "")).lower(),
                "title": _clean_value(fields.get("title", "")),
                "authors": _clean_value(fields.get("author", "")),
                "year": _clean_value(fields.get("year", "")),
            }
    except Exception:
        pass

    return _parse_with_regex(raw)

"""Load files (unstructured + structured) into Documents with metadata.

Metadata comes from (lowest -> highest priority): defaults, an optional `manifest.json` in the folder,
and YAML-style front matter at the top of markdown/text files:

    ---
    title: Payments Platform Architecture
    kind: architecture
    owner: acme-platform-team
    doc_date: 2024-05-02
    ---
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED = {".md", ".markdown", ".txt", ".html", ".htm", ".pdf", ".csv", ".json"}
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)


@dataclass
class Document:
    source_key: str
    title: str
    kind: str
    text: str = ""
    owner: str = "unknown"
    doc_date: datetime | None = None
    mime: str = "text/plain"
    uri: str = ""
    records: list[dict] | None = None  # structured rows (csv/json)
    tags: list[str] = field(default_factory=list)


def parse_date(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m", "%d/%m/%Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _front_matter(text: str) -> tuple[dict, str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article"}

    def __init__(self):
        super().__init__()
        self.parts, self._skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in {"h1", "h2", "h3", "h4"}:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    pages = [p.extract_text() or "" for p in PdfReader(str(path)).pages]
    return "\n\n".join(f"# Page {i}\n\n{t}" for i, t in enumerate(pages, 1))


def _coerce(v: str):
    v = v.strip()
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return parse_date(v) or v


def load_file(path: Path, defaults: dict | None = None) -> Document:
    ext = path.suffix.lower()
    meta = dict(defaults or {})
    records = None
    if ext in (".md", ".markdown", ".txt"):
        fm, text = _front_matter(path.read_text(encoding="utf-8"))
        meta.update(fm)
        mime = "text/markdown" if ext != ".txt" else "text/plain"
    elif ext in (".html", ".htm"):
        p = _HTMLText()
        p.feed(path.read_text(encoding="utf-8"))
        text, mime = re.sub(r"\n{3,}", "\n\n", "".join(p.parts)).strip(), "text/html"
    elif ext == ".pdf":
        text, mime = _read_pdf(path), "application/pdf"
    elif ext == ".csv":
        with path.open(newline="", encoding="utf-8") as f:
            records = [{k: _coerce(v) for k, v in row.items()} for row in csv.DictReader(f)]
        text, mime = "", "text/csv"
    elif ext == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        records = data if isinstance(data, list) else data.get("records", [])
        text, mime = "", "application/json"
    else:
        raise ValueError(f"unsupported file type: {path.name}")

    return Document(
        source_key=meta.get("source_key", path.stem),
        title=meta.get("title", path.stem.replace("-", " ").title()),
        kind=meta.get("kind", "records" if records is not None else "document"),
        owner=meta.get("owner", "unknown"),
        doc_date=parse_date(meta.get("doc_date")),
        text=text,
        mime=mime,
        uri=meta.get("uri", str(path)),
        records=records,
        tags=[t.strip() for t in str(meta.get("tags", "")).split(",") if t.strip()],
    )


def load_folder(folder: Path) -> list[Document]:
    manifest = {}
    mf = folder / "manifest.json"
    if mf.exists():
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    return [load_file(p, manifest.get(p.name)) for p in sorted(folder.iterdir())
            if p.suffix.lower() in SUPPORTED and p.name != "manifest.json"]

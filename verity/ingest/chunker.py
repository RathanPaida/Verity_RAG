"""Heading-aware chunking that preserves section paths and character offsets for exact citations."""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)


@dataclass
class Chunk:
    text: str
    section_path: list[str]
    char_start: int
    char_end: int
    fields: dict | None = None


def approx_tokens(text: str) -> int:
    return int(len(text.split()) * 1.3) + 1


def _sections(text: str) -> list[tuple[list[str], int, int]]:
    """Return [(section_path, start, end)] covering the document."""
    heads = [(m.start(), m.end(), len(m.group(1)), m.group(2)) for m in _HEADING.finditer(text)]
    out, stack = [], []
    if not heads or heads[0][0] > 0:
        out.append(([], 0, heads[0][0] if heads else len(text)))
    for i, (hs, he, level, title) in enumerate(heads):
        stack = [s for s in stack if s[0] < level] + [(level, title)]
        end = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        out.append(([t for _, t in stack], he, end))
    return out


def _paragraphs(text: str, base: int) -> list[tuple[str, int, int]]:
    out = []
    for m in re.finditer(r"\S(?:.*?)(?=\n\s*\n|\Z)", text, re.S):
        para = m.group(0).rstrip()
        out.append((para, base + m.start(), base + m.start() + len(para)))
    return out


def chunk_text(text: str, max_tokens: int = 350, overlap_paragraphs: int = 1) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, s, e in _sections(text):
        paras = _paragraphs(text[s:e], s)
        if not paras:
            continue
        cur: list[tuple[str, int, int]] = []
        for p in paras:
            if cur and approx_tokens(" ".join(x[0] for x in cur + [p])) > max_tokens:
                chunks.append(_make(cur, path))
                cur = cur[-overlap_paragraphs:] if overlap_paragraphs else []
                if cur and approx_tokens(" ".join(x[0] for x in cur + [p])) > max_tokens:
                    cur = []
            cur.append(p)
        if cur:
            chunks.append(_make(cur, path))
    return chunks


def _make(paras, path) -> Chunk:
    prefix = " › ".join(path)
    body = "\n\n".join(p[0] for p in paras)
    # Section path is prepended so it is embedded & keyword-searchable too.
    return Chunk(text=f"{prefix}\n\n{body}" if prefix else body, section_path=list(path),
                 char_start=paras[0][1], char_end=paras[-1][2])


def chunk_records(records: list[dict], title: str) -> list[Chunk]:
    """One chunk per row; text is a readable serialization, `fields` keeps typed values for aggregation."""
    out = []
    for i, row in enumerate(records):
        text = f"{title} record {i + 1}: " + " | ".join(f"{k}: {v:%Y-%m-%d}" if hasattr(v, "strftime")
                                                       else f"{k}: {v}" for k, v in row.items())
        out.append(Chunk(text=text, section_path=[title, f"row {i + 1}"], char_start=i, char_end=i + 1,
                         fields=row))
    return out

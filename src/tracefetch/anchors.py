from __future__ import annotations

import hashlib
import re

from tracefetch.contracts import AnchorRecord


def anchor_text(lines: list[str], line_start: int, line_end: int) -> str:
    return "\n".join(lines[line_start - 1 : line_end])


def build_anchors(markdown: str) -> list[AnchorRecord]:
    lines = markdown.splitlines()
    anchors: list[AnchorRecord] = []
    current_heading: str | None = None
    index = 0
    while index < len(lines):
        if not lines[index].strip():
            index += 1
            continue
        start = index
        stripped = lines[index].lstrip()
        kind = "paragraph"
        if stripped.startswith("#") and re.match(r"^#{1,6}\s+", stripped):
            kind = "heading"
            current_heading = re.sub(r"^#{1,6}\s+", "", stripped).strip()
            index += 1
        elif stripped.startswith("```") or stripped.startswith("~~~"):
            kind = "code"
            fence = stripped[:3]
            index += 1
            while index < len(lines):
                closing = lines[index].lstrip().startswith(fence)
                index += 1
                if closing:
                    break
        elif stripped.startswith("|"):
            kind = "table"
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                index += 1
        elif re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", stripped):
            kind = "list"
            while index < len(lines):
                candidate = lines[index].lstrip()
                if not re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", candidate):
                    break
                index += 1
        else:
            while index < len(lines) and lines[index].strip():
                candidate = lines[index].lstrip()
                if index > start and (
                    re.match(r"^#{1,6}\s+", candidate)
                    or candidate.startswith("```")
                    or candidate.startswith("~~~")
                    or candidate.startswith("|")
                    or re.match(r"^(?:[-*+]\s+|\d+[.)]\s+)", candidate)
                ):
                    break
                index += 1
        end = max(start + 1, index)
        text = "\n".join(lines[start:end])
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        preview = re.sub(r"\s+", " ", text).strip()[:240]
        anchors.append(
            AnchorRecord(
                anchor_id=f"a_{digest[:12]}",
                kind=kind,  # type: ignore[arg-type]
                heading=current_heading,
                line_start=start + 1,
                line_end=end,
                sha256=digest,
                preview=preview,
            )
        )
    return anchors

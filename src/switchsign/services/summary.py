from __future__ import annotations

import re

# Match the Quick Summary heading leniently: "## Quick Summary",
# "## Quick summary of what's covered:", etc. all qualify.
_QUICK_SUMMARY_HEADING_RE = re.compile(r"^##\s+Quick Summary\b.*$", re.IGNORECASE | re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)


def extract_quick_summary(markdown_body: str) -> tuple[str, str]:
    """
    Return (quick_summary_markdown, body_without_quick_summary).

    Stage 9 branded PDFs expect `## Quick Summary` as the first section.
    If the section is missing, return an empty summary and the original body.
    """
    match = _QUICK_SUMMARY_HEADING_RE.search(markdown_body)
    if not match:
        return "", markdown_body.strip()

    summary_start = match.end()
    next_match = _NEXT_HEADING_RE.search(markdown_body, summary_start)
    summary_end = next_match.start() if next_match else len(markdown_body)

    summary = markdown_body[summary_start:summary_end].strip()
    before = markdown_body[: match.start()].strip()
    after = markdown_body[summary_end:].strip()
    remaining = "\n\n".join(part for part in (before, after) if part)
    return summary, remaining


# A numbered top-level section heading, e.g. "## 1. PROJECT OVERVIEW" or
# "# 2. Fees". This marks where the real contract body begins.
_FIRST_NUMBERED_SECTION_RE = re.compile(r"^#{1,4}\s*\d+[.\)]\s", re.MULTILINE)
# Phrases that identify an agreement title / party preamble the branded PDF
# renderer already generates on its own (so a copy in the body is redundant).
_PREAMBLE_MARKERS_RE = re.compile(
    r"entered into|made and entered|by and between|this agreement",
    re.IGNORECASE,
)


def strip_redundant_preamble(markdown_body: str) -> str:
    """
    Drop a redundant agreement-title / party-intro that authors sometimes put
    before the first numbered section. The branded PDF already renders its own
    formal preamble ("This Service Agreement is made and entered into ... by and
    between ..."), so a second copy in the body reads as a duplicate.

    Only the leading block before the first numbered section is considered, and
    only when it looks like a title/preamble — real content is left untouched.
    """
    match = _FIRST_NUMBERED_SECTION_RE.search(markdown_body)
    if not match or match.start() == 0:
        return markdown_body
    head = markdown_body[: match.start()]
    if _PREAMBLE_MARKERS_RE.search(head) or "AGREEMENT" in head.upper():
        return markdown_body[match.start() :].lstrip()
    return markdown_body


def derive_cover_overview(markdown_body: str) -> str:
    """
    Fallback cover blurb when the body has no explicit `## Quick Summary`.

    Returns the opening prose of the first numbered section (typically
    "Project Overview"), so the cover always carries the project's scope
    instead of sitting empty. Returns "" if no numbered section is found.
    """
    match = _FIRST_NUMBERED_SECTION_RE.search(markdown_body)
    if not match:
        return ""
    after = markdown_body[match.end() :]
    newline = after.find("\n")
    after = after[newline + 1 :] if newline != -1 else ""
    next_section = _NEXT_HEADING_RE.search(after)
    section = after[: next_section.start()] if next_section else after
    paragraphs = [block.strip() for block in section.strip().split("\n\n") if block.strip()]
    return paragraphs[0] if paragraphs else ""

from __future__ import annotations

import bleach
from markdown_it import MarkdownIt

_ALLOWED_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}

_ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel", "target"],
    "th": ["align"],
    "td": ["align"],
}

_ALLOWED_PROTOCOLS = {"http", "https", "mailto"}

_md = MarkdownIt("commonmark", {"linkify": True, "typographer": False}).enable("table").enable("strikethrough")


def _add_link_rel(attrs: dict[str, str], new: bool = False) -> dict[str, str]:
    href = attrs.get("href")
    if href:
        attrs["rel"] = "noopener"
        attrs["target"] = "_blank"
    return attrs


def render_contract_markdown(md: str) -> str:
    raw_html = _md.render(md)
    cleaned = bleach.clean(
        raw_html,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
        strip_comments=True,
    )
    return bleach.linkify(cleaned, callbacks=[_add_link_rel], skip_tags=["pre", "code"])

"""Safe rendering helpers for canonical document HTML."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

_ALLOWED_TAGS = {
    "a", "b", "blockquote", "br", "caption", "code", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "i", "li", "ol", "p", "pre", "strong", "table",
    "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
}
_ALLOWED_ATTRS = {"a": {"href", "title"}, "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan"}}


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        if self.skip_depth:
            self.skip_depth += 1
            return
        if tag in {"script", "style", "iframe", "object", "embed", "form", "svg", "link", "meta"}:
            self.skip_depth = 1
            return
        if tag not in _ALLOWED_TAGS:
            return
        rendered: list[str] = []
        for name, value in attrs:
            name = name.casefold()
            if name not in _ALLOWED_ATTRS.get(tag, set()) or value is None:
                continue
            if name == "href" and value.strip().casefold().startswith(("javascript:", "data:", "vbscript:")):
                continue
            rendered.append(f' {name}="{escape(value, quote=True)}"')
        self.parts.append(f"<{tag}{''.join(rendered)}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if tag.casefold() in _ALLOWED_TAGS:
            self.parts.append(f"</{tag.casefold()}>")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(escape(data))


def sanitize_document_html(value: str) -> str:
    parser = _Sanitizer()
    parser.feed(value or "")
    parser.close()
    return "".join(parser.parts)

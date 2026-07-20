from __future__ import annotations

import re

from slixmpp.xmlstream import ET

XHTML_IM_NS = "http://jabber.org/protocol/xhtml-im"
XHTML_NS = "http://www.w3.org/1999/xhtml"

_TABLE_SEPARATOR_RE = re.compile(
    r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$"
)
_ORDERED_LIST_RE = re.compile(r"^\s*\d+[.)]\s+")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_INLINE_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def build_xhtml_message(text: str) -> ET.Element | None:
    """Build an XHTML-IM payload for the provided message text.

    We preserve markdown-like structure (paragraphs, lists, fenced code, tables)
    so capable clients can render a readable rich message instead of raw markdown.
    """

    normalized = _normalize(text)
    if not normalized.strip():
        return None

    html = ET.Element(f"{{{XHTML_IM_NS}}}html")
    body = ET.SubElement(html, f"{{{XHTML_NS}}}body")

    for kind, payload in _parse_blocks(normalized):
        if kind == "code":
            pre = ET.SubElement(body, f"{{{XHTML_NS}}}pre")
            code = ET.SubElement(pre, f"{{{XHTML_NS}}}code")
            code.text = payload
            continue

        if kind == "table":
            headers, rows = payload
            _append_table(body, headers, rows)
            continue

        if kind == "ul":
            ul = ET.SubElement(body, f"{{{XHTML_NS}}}ul")
            for item in payload:
                li = ET.SubElement(ul, f"{{{XHTML_NS}}}li")
                _fill_inline(li, item)
            continue

        if kind == "ol":
            ol = ET.SubElement(body, f"{{{XHTML_NS}}}ol")
            for item in payload:
                li = ET.SubElement(ol, f"{{{XHTML_NS}}}li")
                _fill_inline(li, item)
            continue

        if kind == "h":
            # XHTML-IM's recommended profile has no h1-h6; a bold paragraph
            # renders on every client.
            p = ET.SubElement(body, f"{{{XHTML_NS}}}p")
            strong = ET.SubElement(p, f"{{{XHTML_NS}}}strong")
            _fill_inline(strong, payload)
            continue

        p = ET.SubElement(body, f"{{{XHTML_NS}}}p")
        _fill_inline(p, payload)

    return html


def _normalize(text: str) -> str:
    return (
        text.replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\u2028", "\n")
        .replace("\u2029", "\n")
    )


def _parse_blocks(text: str) -> list[tuple[str, object]]:
    lines = text.split("\n")
    out: list[tuple[str, object]] = []
    i = 0
    n = len(lines)

    while i < n:
        if not lines[i].strip():
            i += 1
            continue

        if lines[i].lstrip().startswith("```"):
            i += 1
            code_lines: list[str] = []
            while i < n and not lines[i].lstrip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < n and lines[i].lstrip().startswith("```"):
                i += 1
            out.append(("code", "\n".join(code_lines)))
            continue

        if i + 1 < n and _looks_like_table_row(lines[i]) and _is_table_separator(lines[i + 1]):
            headers = _parse_table_row(lines[i])
            i += 2
            rows: list[list[str]] = []
            while i < n and _looks_like_table_row(lines[i]) and lines[i].strip():
                rows.append(_parse_table_row(lines[i]))
                i += 1
            out.append(("table", (headers, rows)))
            continue

        if _is_unordered_list_item(lines[i]):
            items: list[str] = []
            while i < n and _is_unordered_list_item(lines[i]):
                items.append(_strip_unordered_marker(lines[i]))
                i += 1
            out.append(("ul", items))
            continue

        if _is_ordered_list_item(lines[i]):
            items = []
            while i < n and _is_ordered_list_item(lines[i]):
                items.append(_strip_ordered_marker(lines[i]))
                i += 1
            out.append(("ol", items))
            continue

        heading = _HEADING_RE.match(lines[i].strip())
        if heading:
            out.append(("h", heading.group(2)))
            i += 1
            continue

        para_lines = [lines[i]]
        i += 1
        while i < n and lines[i].strip():
            if lines[i].lstrip().startswith("```"):
                break
            if i + 1 < n and _looks_like_table_row(lines[i]) and _is_table_separator(lines[i + 1]):
                break
            if _is_unordered_list_item(lines[i]) or _is_ordered_list_item(lines[i]):
                break
            if _HEADING_RE.match(lines[i].strip()):
                break
            para_lines.append(lines[i])
            i += 1
        out.append(("p", "\n".join(para_lines).strip()))

    return out


def _parse_inline(text: str) -> list[tuple[str, object]]:
    """Split a single line into inline segments.

    Segments: ("text", str), ("strong", str), ("code", str), ("a", (label, href)).
    Unbalanced markers stay literal text.
    """
    out: list[tuple[str, object]] = []
    buf: list[str] = []
    i = 0
    n = len(text)

    def flush() -> None:
        if buf:
            out.append(("text", "".join(buf)))
            buf.clear()

    while i < n:
        two = text[i : i + 2]
        if two in ("**", "__"):
            close = text.find(two, i + 2)
            if close > i + 2:
                flush()
                out.append(("strong", text[i + 2 : close]))
                i = close + 2
                continue
        elif text[i] == "`":
            close = text.find("`", i + 1)
            if close > i + 1:
                flush()
                out.append(("code", text[i + 1 : close]))
                i = close + 1
                continue
        elif text[i] == "[":
            m = _INLINE_LINK_RE.match(text, i)
            if m:
                flush()
                out.append(("a", (m.group(1), m.group(2))))
                i = m.end()
                continue
        buf.append(text[i])
        i += 1

    flush()
    return out


def _fill_inline(node: ET.Element, text: str) -> None:
    """Set inline-formatted content on a node, with <br/> for newlines."""
    last: ET.Element | None = None

    def append_text(s: str) -> None:
        if not s:
            return
        if last is None:
            node.text = (node.text or "") + s
        else:
            last.tail = (last.tail or "") + s

    for line_no, line in enumerate(text.split("\n")):
        if line_no:
            last = ET.SubElement(node, f"{{{XHTML_NS}}}br")
        for kind, payload in _parse_inline(line):
            if kind == "text":
                append_text(payload)  # type: ignore[arg-type]
            elif kind == "strong":
                el = ET.SubElement(node, f"{{{XHTML_NS}}}strong")
                _fill_inline(el, payload)  # nested `code` / links inside bold
                last = el
            elif kind == "code":
                el = ET.SubElement(node, f"{{{XHTML_NS}}}code")
                el.text = payload
                last = el
            elif kind == "a":
                label, href = payload
                el = ET.SubElement(node, f"{{{XHTML_NS}}}a")
                el.set("href", href)
                el.text = label
                last = el


def _append_table(parent: ET.Element, headers: list[str], rows: list[list[str]]) -> None:
    table = ET.SubElement(parent, f"{{{XHTML_NS}}}table")
    thead = ET.SubElement(table, f"{{{XHTML_NS}}}thead")
    tr_head = ET.SubElement(thead, f"{{{XHTML_NS}}}tr")
    for h in headers:
        th = ET.SubElement(tr_head, f"{{{XHTML_NS}}}th")
        _fill_inline(th, h)

    if rows:
        tbody = ET.SubElement(table, f"{{{XHTML_NS}}}tbody")
        for row in rows:
            tr = ET.SubElement(tbody, f"{{{XHTML_NS}}}tr")
            for idx in range(len(headers)):
                cell = row[idx] if idx < len(row) else ""
                td = ET.SubElement(tr, f"{{{XHTML_NS}}}td")
                _fill_inline(td, cell)


def _looks_like_table_row(line: str) -> bool:
    stripped = line.strip()
    if "|" not in stripped:
        return False
    cells = _parse_table_row(stripped)
    return len(cells) >= 2


def _is_table_separator(line: str) -> bool:
    return bool(_TABLE_SEPARATOR_RE.match(line))


def _parse_table_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [part.strip() for part in s.split("|")]


def _is_unordered_list_item(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("- ") or stripped.startswith("* ") or stripped.startswith("+ ")


def _strip_unordered_marker(line: str) -> str:
    stripped = line.lstrip()
    return stripped[2:].strip() if len(stripped) >= 2 else stripped


def _is_ordered_list_item(line: str) -> bool:
    return bool(_ORDERED_LIST_RE.match(line))


def _strip_ordered_marker(line: str) -> str:
    return _ORDERED_LIST_RE.sub("", line, count=1).strip()

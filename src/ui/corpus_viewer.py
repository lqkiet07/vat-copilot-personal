"""Corpus viewer module for rendering structured legal documents to HTML."""

from html import escape
from html.parser import HTMLParser
import re
import json
from pathlib import Path

_CORPUS_CACHE: dict[str, list[dict]] = {}


def load_corpus_tree(
    corpus_path: str = "data/processed/vat_corpus.json",
) -> list[dict]:
    """Load legal corpus JSON tree from filesystem with in-memory caching."""
    target = Path(corpus_path)
    if not target.is_absolute() and not target.exists():
        fallback = Path(__file__).resolve().parents[2] / corpus_path
        if fallback.exists():
            target = fallback

    target = target.resolve()
    path_key = str(target)
    if path_key in _CORPUS_CACHE:
        return _CORPUS_CACHE[path_key]

    with open(target, "r", encoding="utf-8") as f:
        data: list[dict] = json.load(f)

    _CORPUS_CACHE[path_key] = data
    return data


class _TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.cell = None
        self.ignored = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object"}:
            self.ignored += 1
        if self.ignored:
            return
        if tag == "tr":
            self.rows.append([])
        elif tag in {"td", "th"}:
            if not self.rows:
                self.rows.append([])
            self.cell = []
        elif tag in {"br", "p", "div"} and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object"}:
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in {"td", "th"} and self.cell is not None:
            self.rows[-1].append("".join(self.cell).strip())
            self.cell = None
        elif tag in {"p", "div"} and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data):
        if not self.ignored and self.cell is not None:
            self.cell.append(data)


def _render_table(rows: list[list]) -> str:
    return '<table style="border-collapse: collapse; width: 100%;">' + "".join(
        "<tr>" + "".join(f"<td>{escape(str(cell))}</td>" for cell in row) + "</tr>"
        for row in rows
    ) + "</table>"


def _render_markdown_table(text: str) -> str:
    rows = [line.strip().strip("|").split("|") for line in text.splitlines() if line.strip()]
    rows = [[cell.strip() for cell in row] for row in rows]
    rows = [row for row in rows if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in row)]
    return _render_table(rows)


def _render_blocks(node: dict) -> str:
    parts = []
    table_blocks = 0
    for block in node.get("blocks", []):
        if isinstance(block, str):
            parts.append(f"<p>{escape(block)}</p>")
            continue
        kind = block.get("kind", block.get("type", ""))
        if kind != "table":
            parts.append(f'<p>{escape(block.get("text", ""))}</p>')
            continue
        if block.get("kind") == "table":
            table_blocks += 1
        rows = block.get("rows", [])
        if not rows and block.get("html"):
            parser = _TableParser()
            parser.feed(block["html"])
            parser.close()
            rows = parser.rows
        if rows:
            parts.append(_render_table(rows))
        elif block.get("text"):
            parts.append(f'<p>{escape(block["text"])}</p>')
    for table in node.get("tables", [])[table_blocks:]:
        parts.append(_render_markdown_table(table))
    return "\n".join(parts)


def _render_dates(node: dict) -> str:
    start = escape(node.get("valid_from") or "")
    end = escape(node.get("valid_to") or "")
    if not start and not end:
        return ""
    return f'<div class="ordinary-dates">Hiệu lực chung: {start} → {end}</div>'


def _render_provision_spans(spans: list[dict]) -> str:
    if not spans:
        return ""
    rendered = []
    for sp in spans:
        subj = escape(sp.get("subject", ""))
        vfrom = escape(sp.get("valid_from", ""))
        vto = escape(sp.get("valid_to", "9999-12-31"))
        evid = escape(sp.get("evidence", ""))
        evid_str = f" [Căn cứ: {evid}]" if evid else ""
        rendered.append(
            f'<div class="provision-span" style="margin-top: 2px; padding: 3px 6px; background-color: #f0f9ff; border-left: 3px solid #0ea5e9; font-size: 11px; color: #0369a1;">'
            f'<span>Hiệu lực riêng: {subj} ({vfrom} → {vto}){evid_str}</span></div>'
        )
    return "\n".join(rendered)


def render_article_html(article: dict, active_ids: list[str]) -> str:
    """Render a single article with anchor ID, hierarchy, and optional highlight."""
    art_id = article.get("id", "")
    is_active = art_id in (active_ids or [])
    css_class = "article highlighted-article" if is_active else "article"
    style_attr = (
        ' style="background-color: #fff9db; border-left: 4px solid #f59f00; padding: 10px; margin: 8px 0; border-radius: 4px;"'
        if is_active
        else ' style="padding: 10px; margin: 8px 0;"'
    )

    parts: list[str] = [f'<div id="art-{escape(art_id)}" class="{css_class}"{style_attr}>']
    code = article.get("code", "")
    title = article.get("title", "")
    header = f"{code}: {title}" if code and title else (code or title)
    parts.append(f'<h4 style="margin: 0 0 6px 0;">{escape(header)}</h4>')

    lead = article.get("lead", "")
    if lead:
        parts.append(f'<p class="article-lead" style="margin: 0 0 6px 0;">{escape(lead)}</p>')

    parts.append(_render_dates(article))
    parts.append(_render_blocks(article))
    art_spans = _render_provision_spans(article.get("provision_spans", []))
    if art_spans:
        parts.append(art_spans)

    for clause in article.get("clauses", []):
        cid = clause.get("id", "")
        ccode = clause.get("code", "")
        ccontent = clause.get("content", "")
        prefix = f"<strong>{escape(ccode)}:</strong> " if ccode else ""
        parts.append(
            f'<div id="cls-{escape(cid)}" class="clause" style="margin-left: 12px; margin-bottom: 4px;">'
            f'<p style="margin: 2px 0;">{prefix}{escape(ccontent)}</p>'
        )

        parts.append(_render_dates(clause))
        parts.append(_render_blocks(clause))
        cspans = _render_provision_spans(clause.get("provision_spans", []))
        if cspans:
            parts.append(cspans)

        for pt in clause.get("points", []):
            pid = pt.get("id", "")
            pcode = pt.get("code", "")
            pcontent = pt.get("content", "")
            pprefix = f"<strong>{escape(pcode)}:</strong> " if pcode else ""
            parts.append(
                f'<div id="pt-{escape(pid)}" class="point" style="margin-left: 16px;">'
                f'<p style="margin: 2px 0;">{pprefix}{escape(pcontent)}</p>'
            )

            parts.append(_render_dates(pt))
            parts.append(_render_blocks(pt))
            pspans = _render_provision_spans(pt.get("provision_spans", []))
            if pspans:
                parts.append(pspans)

            parts.append("</div>")
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


def render_document_tree_html(doc: dict, active_ids: list[str]) -> str:
    """Render complete legal document tree hierarchy to HTML."""
    doc_id = doc.get("id", "")
    title = doc.get("title", "")
    code = doc.get("code", "")

    parts: list[str] = [
        f'<div id="doc-{escape(doc_id)}" class="document" style="font-family: sans-serif; line-height: 1.5;">',
        f'<h2 style="margin-bottom: 4px;">{escape(title)}</h2>',
        f'<p class="doc-code" style="color: #666; margin-top: 0;"><strong>{escape(code)}</strong></p>',
    ]

    parts.append(_render_dates(doc))
    status = doc.get("status", "")
    role = doc.get("document_role", "")
    if status == "CONSOLIDATION_REFERENCE" or "consolidation" in role.lower():
        parts.append(
            '<div class="consolidation-banner" style="background-color: #fef3c7; border: 1px solid #f59e0b; padding: 8px 12px; margin-bottom: 12px; border-radius: 4px; color: #92400e; font-size: 13px;">'
            '<strong>Văn bản hợp nhất — tài liệu đối chiếu, không phải văn bản sửa đổi độc lập</strong>'
            '</div>'
        )

    chapters = doc.get("chapters", [])
    if chapters:
        for ch in chapters:
            ch_id = ch.get("id", "")
            ch_title = ch.get("title") or ch.get("code") or ""
            parts.append(f'<div id="ch-{escape(ch_id)}" class="chapter" style="margin-top: 16px;">')
            parts.append(f'<h3 style="border-bottom: 1px solid #ddd; padding-bottom: 4px;">{escape(ch_title)}</h3>')
            for art in ch.get("articles", []):
                parts.append(render_article_html(art, active_ids))
            parts.append("</div>")
    else:
        for art in doc.get("articles", []):
            parts.append(render_article_html(art, active_ids))

    appendices = doc.get("appendices", []) + doc.get("forms", [])
    if appendices:
        parts.append('<div class="document-appendices" style="margin-top: 24px; border-top: 2px solid #e2e8f0; padding-top: 12px;">')
        for app in appendices:
            app_id = app.get("id", "")
            app_title = app.get("title", "")
            parts.append(f'<div id="app-{escape(app_id)}" class="appendix" style="margin-top: 16px;">')
            parts.append(f'<h4 style="margin-bottom: 8px; color: #1e3a8a;">{escape(app_title)}</h4>')
            parts.append(_render_blocks(app))
            parts.append('</div>')
        parts.append('</div>')

    parts.append("</div>")
    return "\n".join(parts)


def _document_articles(document: dict) -> list[dict]:
    articles = list(document.get("articles", []))
    seen = {article.get("id") for article in articles}
    for chapter in document.get("chapters", []):
        for article in chapter.get("articles", []):
            if article.get("id") not in seen:
                articles.append(article)
                seen.add(article.get("id"))
    return articles


def _index_provision(node, parent, ancestors, article_id, kind, provisions):
    node_id = node.get("id", "")
    direct = [{**span, "source_id": node_id} for span in node.get("provision_spans", [])]
    inherited = parent.get("inherited_provision_spans", []) + parent.get("provision_spans", [])
    meta = {
        "id": node_id, "kind": kind, "parent_id": parent["id"],
        "article_id": article_id, "ancestor_ids": ancestors, "descendant_ids": [],
        "code": node.get("code", ""), "title": node.get("title", ""),
        "text": node.get("content") or node.get("lead", ""),
        "doc_id": parent["doc_id"], "doc_code": parent["doc_code"],
        "doc_title": parent["doc_title"],
        "document_role": node.get("document_role") or parent["document_role"],
        "valid_from": node.get("valid_from") or parent["valid_from"],
        "valid_to": node.get("valid_to") or parent["valid_to"],
        "status": node.get("status") or parent["status"],
        "provenance": node.get("provenance", parent["provenance"]),
        "has_provision_spans": bool(direct or inherited),
        "provision_spans": direct, "inherited_provision_spans": inherited,
    }
    provisions[node_id] = meta
    for key, child_kind in (("clauses", "clause"), ("points", "point")):
        for child in node.get(key, []):
            if not child.get("id"):
                continue
            child_meta = _index_provision(child, meta, ancestors + [node_id], article_id, child_kind, provisions)
            meta["descendant_ids"].extend([child_meta["id"], *child_meta["descendant_ids"]])
    return meta


def _appendix_metadata(document: dict, key: str) -> list[dict]:
    return [
        {
            "id": item.get("id", ""), "title": item.get("title", ""),
            "has_content": bool(item.get("blocks") or item.get("tables")),
            "provenance": item.get("provenance", document.get("provenance", {})),
        }
        for item in document.get(key, [])
    ]


def load_corpus_index(
    corpus_path: str = "data/processed/vat_corpus.json",
) -> dict[str, dict]:
    """Map every article id to its metadata and full clause text."""
    index: dict[str, dict] = {}
    for document in load_corpus_tree(corpus_path):
        doc_code = document.get("code", "")
        doc_id = document.get("id", "")
        doc_valid_from = document.get("valid_from", "")
        doc_valid_to = document.get("valid_to", "")
        doc_status = document.get("status", "")
        doc_role = document.get("document_role", "")
        has_appendices = bool(document.get("appendices"))
        for article in _document_articles(document):
            article_id = article.get("id")
            if not article_id:
                continue
            provisions = {}
            parent = {
                "id": doc_id, "doc_id": doc_id, "doc_code": doc_code,
                "doc_title": document.get("title", ""), "valid_from": doc_valid_from,
                "valid_to": doc_valid_to, "status": doc_status, "document_role": doc_role,
                "provenance": document.get("provenance", {}),
            }
            article_meta = _index_provision(article, parent, [], article_id, "article", provisions)
            lines = []
            all_spans = [span for item in provisions.values() for span in item["provision_spans"]]
            for clause in article.get("clauses", []):
                content = clause.get("content") or ""
                code = clause.get("code") or ""
                clause_header = f"{code}: {content}".strip(": ").strip() if code else content
                if clause_header:
                    lines.append(clause_header)
                for pt in clause.get("points", []):
                    pt_content = pt.get("content") or ""
                    pt_code = pt.get("code") or ""
                    if pt_content:
                        suffix = f" (theo {code})" if code else ""
                        lines.append(f"  {pt_code} {pt_content}{suffix}".strip())
            index[article_id] = {
                "code": article.get("code", ""),
                "title": article.get("title", ""),
                "doc_id": doc_id,
                "doc_code": doc_code,
                "doc_title": document.get("title", ""),
                "valid_from": article.get("valid_from") or doc_valid_from,
                "valid_to": article.get("valid_to") or doc_valid_to,
                "status": article.get("status") or doc_status,
                "document_role": doc_role,
                "has_provision_spans": len(all_spans) > 0,
                "provision_spans": all_spans,
                "has_appendices": has_appendices,
                "provenance": article_meta["provenance"],
                "provisions": provisions,
                "appendices": _appendix_metadata(document, "appendices"),
                "forms": _appendix_metadata(document, "forms"),
                "text": "\n".join(lines)[:30000],
            }
    return index

"""Corpus viewer module for rendering structured legal documents to HTML."""

from html import escape
import json
from pathlib import Path

_CORPUS_CACHE: dict[str, list[dict]] = {}


def load_corpus_tree(
    corpus_path: str = "data/processed/vat_corpus.json",
) -> list[dict]:
    """Load legal corpus JSON tree from filesystem with in-memory caching."""
    path_key = str(corpus_path)
    if path_key in _CORPUS_CACHE:
        return _CORPUS_CACHE[path_key]

    target = Path(corpus_path)
    if not target.is_absolute() and not target.exists():
        fallback = Path(__file__).resolve().parents[2] / corpus_path
        if fallback.exists():
            target = fallback

    with open(target, "r", encoding="utf-8") as f:
        data: list[dict] = json.load(f)

    _CORPUS_CACHE[path_key] = data
    return data


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

    parts: list[str] = [f'<div id="art-{art_id}" class="{css_class}"{style_attr}>']
    code = article.get("code", "")
    title = article.get("title", "")
    header = f"{code}: {title}" if code and title else (code or title)
    parts.append(f'<h4 style="margin: 0 0 6px 0;">{escape(header)}</h4>')

    lead = article.get("lead", "")
    if lead:
        parts.append(f'<p class="article-lead" style="margin: 0 0 6px 0;">{escape(lead)}</p>')

    for clause in article.get("clauses", []):
        cid = clause.get("id", "")
        ccode = clause.get("code", "")
        ccontent = clause.get("content", "")
        prefix = f"<strong>{escape(ccode)}:</strong> " if ccode else ""
        parts.append(
            f'<div id="cls-{cid}" class="clause" style="margin-left: 12px; margin-bottom: 4px;">'
            f'<p style="margin: 2px 0;">{prefix}{escape(ccontent)}</p>'
        )

        for pt in clause.get("points", []):
            pid = pt.get("id", "")
            pcode = pt.get("code", "")
            pcontent = pt.get("content", "")
            pprefix = f"<strong>{escape(pcode)}:</strong> " if pcode else ""
            parts.append(
                f'<div id="pt-{pid}" class="point" style="margin-left: 16px;">'
                f'<p style="margin: 2px 0;">{pprefix}{escape(pcontent)}</p></div>'
            )
        parts.append("</div>")

    parts.append("</div>")
    return "\n".join(parts)


def render_document_tree_html(doc: dict, active_ids: list[str]) -> str:
    """Render complete legal document tree hierarchy to HTML."""
    doc_id = doc.get("id", "")
    title = doc.get("title", "")
    code = doc.get("code", "")

    parts: list[str] = [
        f'<div id="doc-{doc_id}" class="document" style="font-family: sans-serif; line-height: 1.5;">',
        f'<h2 style="margin-bottom: 4px;">{escape(title)}</h2>',
        f'<p class="doc-code" style="color: #666; margin-top: 0;"><strong>{escape(code)}</strong></p>',
    ]

    chapters = doc.get("chapters", [])
    if chapters:
        for ch in chapters:
            ch_id = ch.get("id", "")
            ch_title = ch.get("title") or ch.get("code") or ""
            parts.append(f'<div id="ch-{ch_id}" class="chapter" style="margin-top: 16px;">')
            parts.append(f'<h3 style="border-bottom: 1px solid #ddd; padding-bottom: 4px;">{escape(ch_title)}</h3>')
            for art in ch.get("articles", []):
                parts.append(render_article_html(art, active_ids))
            parts.append("</div>")
    else:
        for art in doc.get("articles", []):
            parts.append(render_article_html(art, active_ids))

    parts.append("</div>")
    return "\n".join(parts)

"""Parse vbpl.vn HTML into Document/Chapter/Article/Clause/Point hierarchy.

Prioritizes semantic CSS classes with regex fallbacks. Preserves tables as Markdown.
"""
import re
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from src.parser.regex_patterns import (
    RE_ARTICLE,
    RE_CHAPTER,
    RE_CLAUSE,
    RE_POINT,
    RE_SECTION,
)
from src.parser.utils import fold_ascii, normalize_text


def _is_appendix(tag, text: str) -> bool:
    """Identify if element or text marks a document appendix/phu luc boundary."""
    classes = tag.get("class", []) if isinstance(tag, Tag) else []
    for c in classes:
        c_low = c.lower()
        if "appendix" in c_low or "phu-luc" in c_low or "phuluc" in c_low:
            return True
    ascii_text = fold_ascii(text).strip(" \t\r\n:.-[]()")
    return bool(re.match(r"(?i)^phu\s+luc(?:\s+(?:[IVXLCDM]+|\d+))?(?:\s*[:–—-]\s*\S.*)?$", ascii_text))


def _table_to_markdown(table: Tag) -> str:
    """Convert an HTML table into a Markdown table representation."""
    rows = []
    for tr in table.find_all("tr"):
        cells = [normalize_text(c.get_text(" ")) for c in tr.find_all(["th", "td"])]
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _classify(tag, text: str) -> str:
    """Classify element tag or text into document structural units."""
    if _is_appendix(tag, text):
        return "appendix"
    classes = tag.get("class", []) if isinstance(tag, Tag) else []
    for c in classes:
        if c == "prov-chapter":
            return "chapter"
        if c == "prov-article":
            return "article"
        if c == "prov-clause":
            return "clause"
        if c == "prov-item":
            return "point"
        if c == "prov-section":
            return "section"
    ascii_text = fold_ascii(text)
    if RE_ARTICLE.match(ascii_text):
        return "article"
    if RE_CHAPTER.match(ascii_text):
        return "chapter"
    if RE_SECTION.match(ascii_text):
        return "section"
    if RE_CLAUSE.match(ascii_text):
        return "clause"
    if RE_POINT.match(ascii_text):
        return "point"
    return "text"


def _split_article(text: str) -> tuple[str, str]:
    """Split article heading into article number and title."""
    m = RE_ARTICLE.match(fold_ascii(text))
    num = m.group(1) if m else ""
    title = normalize_text(text.split(".", 1)[1]) if "." in text else ""
    return num, title


def _split_clause(text: str) -> tuple[str, str]:
    """Split clause text into clause number and body."""
    m = RE_CLAUSE.match(fold_ascii(text))
    num = m.group(1) if m else ""
    body = normalize_text(text.split(".", 1)[1]) if "." in text else normalize_text(text)
    return num, body


def _split_point(text: str) -> tuple[str, str]:
    """Split point text into point letter and body."""
    letter = fold_ascii(text).split(")", 1)[0].strip()
    body = normalize_text(text.split(")", 1)[1])
    return letter, body


def _split_chapter(text: str) -> tuple[str, str]:
    """Split chapter heading into chapter number and title."""
    m = RE_CHAPTER.match(fold_ascii(text))
    num = m.group(1) if m else ""
    title = normalize_text(text)
    return num, title


def _handle_table(el, article: dict | None) -> None:
    """Attach Markdown table to the active article if present."""
    if article is not None:
        md = _table_to_markdown(el)
        if md:
            article["tables"].append(md)


def _handle_chapter(text: str, doc: dict) -> dict:
    """Create and register a new chapter in the document."""
    num, title = _split_chapter(text)
    chapter = {
        "id": f"{doc['id']}_C{num or len(doc['chapters']) + 1}",
        "code": f"Chuong {num}",
        "title": title,
        "articles": [],
    }
    doc["chapters"].append(chapter)
    return chapter


def _handle_article(text: str, doc: dict, chapter: dict | None) -> dict:
    """Create and register a new article under document and optional chapter."""
    num, title = _split_article(text)
    article = {
        "id": f"{doc['id']}_D{num}",
        "code": f"Dieu {num}",
        "title": title,
        "content": text,
        "lead": "",
        "tables": [],
        "clauses": [],
        "valid_from": doc["valid_from"],
        "valid_to": doc["valid_to"],
    }
    doc["articles"].append(article)
    if chapter is not None:
        chapter["articles"].append(article)
    return article


def _handle_clause(text: str, doc: dict, article: dict, clause: dict | None) -> dict | None:
    """Append unnumbered text or create a new numbered clause under the article."""
    if not RE_CLAUSE.match(fold_ascii(text)):
        if clause is not None:
            clause["content"] += " " + text
        elif not article["clauses"]:
            article["lead"] = (article["lead"] + " " + text).strip() if article["lead"] else text
        return clause
    num, body = _split_clause(text)
    base_id = f"{article['id']}_K{num}"
    existing_ids = {c["id"] for c in article["clauses"]}
    if base_id not in existing_ids:
        cid = base_id
    else:
        idx = 1
        while f"{base_id}_{idx}" in existing_ids:
            idx += 1
        cid = f"{base_id}_{idx}"
    new_clause = {
        "id": cid,
        "code": f"Khoan {num}",
        "content": body,
        "points": [],
        "valid_from": doc["valid_from"],
        "valid_to": doc["valid_to"],
    }
    article["clauses"].append(new_clause)
    return new_clause


def _handle_point(text: str, doc: dict, article: dict, clause: dict | None) -> dict | None:
    """Append unlettered text to active point or create a new point."""
    if ")" not in text:
        if clause is not None and clause["points"]:
            clause["points"][-1]["content"] += " " + text
        return clause
    letter, body = _split_point(text)
    if clause is None:
        doc["unresolved_blocks"].append({
            "text": text, "article_id": article["id"], "reason": "point_without_clause",
        })
        return None

    base_id = f"{clause['id']}_{letter}"
    existing_ids = {p["id"] for p in clause["points"]}
    if base_id not in existing_ids:
        pid = base_id
    else:
        idx = 1
        while f"{base_id}_{idx}" in existing_ids:
            idx += 1
        pid = f"{base_id}_{idx}"

    point = {
        "id": pid,
        "code": f"Diem {letter}",
        "content": body,
        "valid_from": doc["valid_from"],
        "valid_to": doc["valid_to"],
    }
    clause["points"].append(point)
    return clause


def _handle_other(text: str, doc: dict, article: dict | None, clause: dict | None = None,
                  continuation: bool = False) -> None:
    if article is None:
        doc["preamble"].append(text)
    elif clause is not None and continuation:
        target = clause["points"][-1] if clause["points"] else clause
        target["content"] = (target["content"] + " " + text).strip()
    elif clause is not None:
        doc["unresolved_blocks"].append({
            "text": text, "article_id": article["id"], "reason": "uncertain_continuation",
        })
    else:
        article["lead"] = (article["lead"] + " " + text).strip()


def _update_quotes(text: str, quotes: list[str]) -> None:
    for index, char in enumerate(text):
        if char == "“":
            quotes.append(char)
        elif char == "”" and quotes:
            quotes.pop()
        elif char == '"':
            opens_nested = (
                quotes and quotes[-1] == "“"
                and (index == 0 or text[index - 1].isspace() or text[index - 1] in "(:")
                and index + 1 < len(text) and text[index + 1].isalnum()
            )
            if quotes and not opens_nested:
                quotes.pop()
            else:
                quotes.append(char)


def _is_administrative(text: str) -> bool:
    folded = fold_ascii(text)
    if re.match(r"^(?:noi nhan\s*:|(?:KT|TM|TL|TUQ)\.|\(?da ky\)?)", folded, re.IGNORECASE):
        return True
    return text.isupper() and bool(re.fullmatch(
        r"(?:PHO\s+)?(?:CHU TICH QUOC HOI|THU TUONG(?:\s+CHINH PHU)?|BO TRUONG(?:\s+BO\s+[A-Z\s]+)?)",
        folded,
    ))


def _is_form_catalog(el: Tag) -> bool:
    if el.name != "table":
        return False
    rows = el.find_all("tr")
    if not rows:
        return False
    headings = [fold_ascii(normalize_text(c.get_text(" "))).lower()
                for c in rows[0].find_all(["td", "th"])]
    return headings[:2] == ["mau", "ten mau"] and any(
        re.match(r"^mau so\s+\d+\b", fold_ascii(normalize_text(c.get_text(" "))), re.IGNORECASE)
        for row in rows[1:] for c in row.find_all(["td", "th"])
    )


def _source_elements(root):
    for child in root.children:
        if isinstance(child, Comment):
            continue
        if isinstance(child, NavigableString):
            if normalize_text(str(child)):
                yield child
            continue
        if not isinstance(child, Tag) or child.name in {"script", "style", "head"}:
            continue
        if child.name in {"p", "table"}:
            yield child
            if child.name == "table":
                yield from child.find_all(["p", "table"])
        elif child.find(["p", "table", "script", "style"]):
            yield from _source_elements(child)
        elif normalize_text(child.get_text(" ")):
            yield child


def parse_vbpl_document(html_content: str, metadata: dict) -> dict:
    """Parse HTML into DocumentNode hierarchy using metadata."""
    soup = BeautifulSoup(html_content, "html.parser")
    root = soup.select_one(".preview-content") or soup
    if not root.find(["p", "table"]):
        raise ValueError("Unsupported source: expected HTML with p or table blocks; PDF/archive/text requires a separate adapter")
    doc_id = metadata["doc_id"]
    doc = {
        "id": doc_id,
        "code": metadata.get("code", ""),
        "title": metadata.get("title", ""),
        "valid_from": metadata.get("valid_from", ""),
        "valid_to": metadata.get("valid_to", "9999-12-31"),
        "status": metadata.get("status", "ACTIVE"),
        "preamble": [],
        "chapters": [],
        "articles": [],
        "appendices": [],
        "forms": [],
        "source_blocks": [],
        "unresolved_blocks": [],
        "administrative_blocks": [],
    }
    chapter, article, clause = None, None, None
    appendix = None
    form = None
    unresolved_reason = None
    article_ids = set()
    quotes = []
    quote_start = None
    layout_tables = set()
    table_blocks = {}
    administrative = False

    for el in _source_elements(root):
        parents = el.find_parents("table")
        if parents and any(id(parent) not in layout_tables for parent in parents):
            continue
        text = normalize_text(el.get_text(" ") if isinstance(el, Tag) else str(el))
        if not text:
            continue
        if parents:
            block = table_blocks[id(parents[-1])]
        else:
            block = {
                "index": len(doc["source_blocks"]), "kind": {"table": "table", "p": "paragraph"}.get(el.name, "unsupported"),
                "text": text, "html": str(el) if isinstance(el, Tag) else el.output_ready(),
            }
            doc["source_blocks"].append(block)
        if el.name not in {"p", "table"}:
            doc["unresolved_blocks"].append({
                **block, "article_id": article["id"] if article else None,
                "reason": "unsupported_source_block",
            })
            if appendix is not None:
                appendix["blocks"].append(block)
            elif form is not None:
                form["blocks"].append(block)
            continue
        kind = _classify(el, text) if el.name != "table" else "table"
        if not quotes and kind == "appendix":
            appendix = {
                "id": f"{doc_id}_PL{len(doc['appendices']) + 1}",
                "title": text, "blocks": [], "tables": [],
            }
            doc["appendices"].append(appendix)
            article, clause = None, None
        if appendix is not None:
            appendix["blocks"].append(block)
            if el.name == "table":
                appendix["tables"].append(_table_to_markdown(el))
            continue
        form_heading = re.match(r"^mau so\s+\d+\s*[.:]\s+\S", fold_ascii(text), re.IGNORECASE)
        if not quotes and el.name == "p" and form_heading and (administrative or form is not None or unresolved_reason):
            form = {
                "id": f"{doc_id}_FORM{block['index']}",
                "title": text, "blocks": [], "tables": [],
            }
            doc["forms"].append(form)
            article, clause = None, None
            administrative = False
            unresolved_reason = None
        if form is not None:
            form["blocks"].append(block)
            if el.name == "table":
                form["tables"].append(_table_to_markdown(el))
            continue
        if not quotes and administrative and _is_form_catalog(el):
            unresolved_reason = "unknown_form_boundary"
            article, clause = None, None
            administrative = False
        if not quotes and kind == "article" and not unresolved_reason:
            num, _ = _split_article(text)
            if f"{doc_id}_D{num}" in article_ids:
                unresolved_reason = "duplicate_article_identity"
                article, clause = None, None
        if unresolved_reason:
            doc["unresolved_blocks"].append({**block, "article_id": None, "reason": unresolved_reason})
            continue
        if el.name == "table":
            if not quotes and (
                administrative or "document-recipient-signature" in el.get("class", [])
                or _is_administrative(text)
                or any(_is_administrative(normalize_text(p.get_text(" "))) for p in el.find_all("p"))
            ):
                doc["administrative_blocks"].append(block)
            elif not quotes and any(_classify(p, normalize_text(p.get_text(" "))) == "article"
                                    for p in el.find_all("p")):
                layout_tables.add(id(el))
                table_blocks[id(el)] = block
            else:
                _handle_table(el, article)
                if quotes:
                    _update_quotes(text, quotes)
                    if not quotes:
                        quote_start = None
            continue
        if quotes or text.startswith(("“", '"')):
            if not quotes:
                quote_start = block["index"]
            _handle_other(text, doc, article, clause, continuation=True)
            _update_quotes(text, quotes)
            if not quotes:
                quote_start = None
            continue
        if kind in {"chapter", "section", "article", "clause", "point"}:
            administrative = False
        if administrative or _is_administrative(text):
            administrative = True
            if not doc["administrative_blocks"] or doc["administrative_blocks"][-1] is not block:
                doc["administrative_blocks"].append(block)
            continue
        if kind == "chapter":
            chapter = _handle_chapter(text, doc)
            article, clause = None, None
        elif kind == "section":
            article, clause = None, None
        elif kind == "article":
            article = _handle_article(text, doc, chapter)
            article_ids.add(article["id"])
            clause = None
        elif kind == "clause" and article is not None:
            if "prov-clause" in el.get("class", []):
                text = re.sub(r"^(\d+)\s*[.)](?!\d)\s*", r"\1. ", text)
            clause = _handle_clause(text, doc, article, clause)
        elif kind == "point" and article is not None:
            clause = _handle_point(text, doc, article, clause)
        else:
            _handle_other(text, doc, article, clause, continuation="prov-content" in el.get("class", []))
    if quotes and quote_start is not None:
        doc["unresolved_blocks"].extend(
            {**block, "article_id": article["id"] if article else None, "reason": "unclosed_quotation"}
            for block in doc["source_blocks"][quote_start:]
        )
    return doc

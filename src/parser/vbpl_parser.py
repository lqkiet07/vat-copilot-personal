"""Parse vbpl.vn HTML into Document/Chapter/Article/Clause/Point hierarchy.

Prioritizes semantic CSS classes with regex fallbacks. Preserves tables as Markdown.
"""
from bs4 import BeautifulSoup, Tag
from src.parser.regex_patterns import (
    RE_ARTICLE,
    RE_CHAPTER,
    RE_CLAUSE,
    RE_POINT,
    RE_SECTION,
)
from src.parser.utils import fold_ascii, normalize_text


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
    new_clause = {
        "id": f"{article['id']}_K{num}",
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
        clause = {
            "id": f"{article['id']}_K0",
            "code": "Khoan 0",
            "content": "",
            "points": [],
            "valid_from": doc["valid_from"],
            "valid_to": doc["valid_to"],
        }
        article["clauses"].append(clause)
    point = {
        "id": f"{clause['id']}_{letter}",
        "code": f"Diem {letter}",
        "content": body,
        "valid_from": doc["valid_from"],
        "valid_to": doc["valid_to"],
    }
    clause["points"].append(point)
    return clause


def _handle_other(text: str, doc: dict, article: dict | None) -> None:
    """Store preamble or article leading text."""
    if article is None:
        doc["preamble"].append(text)
    elif not article["clauses"] and not article["lead"]:
        article["lead"] = text


def parse_vbpl_document(html_content: str, metadata: dict) -> dict:
    """Parse HTML into DocumentNode hierarchy using metadata."""
    soup = BeautifulSoup(html_content, "html.parser")
    root = soup.select_one(".preview-content") or soup
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
    }
    chapter, article, clause = None, None, None

    for el in root.find_all(["p", "table"], recursive=True):
        if el.name == "table":
            _handle_table(el, article)
            continue
        text = normalize_text(el.get_text(" "))
        if not text:
            continue
        kind = _classify(el, text)
        if kind == "chapter":
            chapter = _handle_chapter(text, doc)
            article, clause = None, None
        elif kind == "section":
            article, clause = None, None
        elif kind == "article":
            article = _handle_article(text, doc, chapter)
            clause = None
        elif kind == "clause" and article is not None:
            clause = _handle_clause(text, doc, article, clause)
        elif kind == "point" and article is not None:
            clause = _handle_point(text, doc, article, clause)
        else:
            _handle_other(text, doc, article)
    return doc

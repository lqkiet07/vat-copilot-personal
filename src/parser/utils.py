"""Text processing utilities for legal document parsing."""
import unicodedata


def normalize_text(text: str) -> str:
    """Collapse multiple whitespaces into a single space and strip boundaries."""
    return " ".join(text.split())


def fold_ascii(text: str) -> str:
    """Normalize text to ASCII by mapping Vietnamese 'd' and removing diacritics."""
    text = text.replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", text)
        if unicodedata.category(c) != "Mn"
    )

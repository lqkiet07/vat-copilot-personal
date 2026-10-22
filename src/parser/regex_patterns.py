"""Standard regex patterns for Vietnamese legal document structure.

Note: Clauses are numbered as `1. ` (without 'Khoan');
Points are lettered as `a) ` (Vietnamese alphabet characters).
"""
import re

# Note: Patterns match text that has already been ASCII-folded (unaccented).
RE_CHAPTER = re.compile(r"(?m)^Chuong\s+([IVXLCDM]+)\b[\s.:\-]*(.*)$")
RE_ARTICLE = re.compile(r"(?m)^Dieu\s+(\d+)\.\s*(.*)$")
RE_CLAUSE = re.compile(r"(?m)^(\d+)\.\s+(.*)$")
RE_POINT = re.compile(r"(?m)^([a-y])\)\s+(.*)$")
RE_SECTION = re.compile(r"(?m)^Muc\s+(\d+)\.\s*(.*)$")

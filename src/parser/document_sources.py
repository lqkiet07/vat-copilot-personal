from collections import Counter
from io import BytesIO, StringIO
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile
import struct
import xml.etree.ElementTree as ET
import zlib


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 2048
MAX_PART_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_XML_DEPTH = 128
MAX_XML_NODES = 250000
MAX_XML_NAMESPACE_BYTES = 4096
MAX_XML_NAMESPACE_TOTAL_BYTES = 1024 * 1024
MAX_XML_NAME_BYTES = 8 * 1024 * 1024
MAX_XML_ATTRIBUTE_BYTES = 8 * 1024 * 1024
MAX_XML_TEXT_BYTES = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
DOCUMENT = "word/document.xml"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
DOCUMENT_RELS = "word/_rels/document.xml.rels"
SUPPORTED_ELEMENTS = {
    "document", "body", "p", "r", "t", "tab", "br", "cr", "tbl", "tr", "tc",
    "tblGrid", "gridCol", "bookmarkStart", "bookmarkEnd", "proofErr",
    "lastRenderedPageBreak", "hdr", "ftr", "headerReference", "footerReference",
    "footnotes", "endnotes", "footnote", "endnote", "footnoteReference", "endnoteReference",
    "footnoteRef", "endnoteRef", "separator", "continuationSeparator", "continuationNotice",
}
PROPERTY_ELEMENTS = {"pPr", "rPr", "tblPr", "trPr", "tcPr", "sectPr", "tblPrEx"}


class _OutputBudget:
    def __init__(self):
        self.remaining = MAX_OUTPUT_BYTES

    def record(self):
        pass

    def consume(self, text):
        self.remaining -= len(text.encode("utf-8"))
        if self.remaining < 0:
            raise ValueError("Output byte limit exceeded")
        return text


class _BoundedSink:
    def __init__(self, budget):
        self.budget = budget
        budget.record()
        self.stream = StringIO()

    def write(self, text):
        self.budget.consume(text)
        return self.stream.write(text)

    def getvalue(self):
        return self.stream.getvalue()


class _SafeTreeBuilder(ET.TreeBuilder):
    def __init__(self, totals):
        super().__init__()
        self.totals = totals
        self.depth = 0

    def charge(self, category, size, limit):
        self.totals[category] += size
        if self.totals[category] > limit:
            raise ValueError(f"XML {category} limit exceeded")

    def start_ns(self, prefix, uri):
        size = len(uri.encode("utf-8"))
        if size > MAX_XML_NAMESPACE_BYTES:
            raise ValueError("XML namespace URI limit exceeded")
        self.charge("namespace", size + len((prefix or "").encode("utf-8")),
                    MAX_XML_NAMESPACE_TOTAL_BYTES)

    def start(self, tag, attrs):
        self.depth += 1
        if self.depth > MAX_XML_DEPTH:
            raise ValueError("XML depth limit exceeded")
        self.charge("node", 1, MAX_XML_NODES)
        self.charge("name", len(tag.encode("utf-8")), MAX_XML_NAME_BYTES)
        self.charge("attribute", sum(len(k.encode("utf-8")) + len(v.encode("utf-8"))
                                     for k, v in attrs.items()), MAX_XML_ATTRIBUTE_BYTES)
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def data(self, data):
        self.charge("text", len(data.encode("utf-8")), MAX_XML_TEXT_BYTES)
        return super().data(data)

    def doctype(self, name, pubid, system):
        raise ValueError("DTD/entity declarations are forbidden")


def _parse_xml(data: bytes, name: str, totals):
    normalized = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in normalized or b"<!ENTITY" in normalized:
        raise ValueError(f"DTD/entity declarations are forbidden: {name}")
    try:
        return ET.fromstring(data, parser=ET.XMLParser(target=_SafeTreeBuilder(totals)))
    except (ET.ParseError, LookupError) as exc:
        raise ValueError(f"Invalid XML part: {name}") from exc


def _preflight_record(stream, end):
    header = stream.read(46)
    if len(header) != 46 or header[:4] != b'PK\x01\x02':
        raise ValueError('Invalid ZIP central directory record')
    compressed, expanded = struct.unpack_from('<II', header, 20)
    name_size, extra_size, comment_size, disk = struct.unpack_from('<HHHH', header, 28)
    offset = struct.unpack_from('<I', header, 42)[0]
    if disk or 0xffffffff in (compressed, expanded, offset):
        raise ValueError('Unsupported ZIP64 or multidisk ZIP')
    if stream.tell() + name_size + extra_size + comment_size > end or offset + 30 > end:
        raise ValueError('Invalid ZIP central directory bounds')
    stream.seek(name_size, 1)
    extra = stream.read(extra_size)
    position = 0
    while position < len(extra):
        if position + 4 > len(extra):
            raise ValueError('Invalid ZIP extra field')
        kind, size = struct.unpack_from('<HH', extra, position)
        position += 4 + size
        if kind == 1:
            raise ValueError('Unsupported ZIP64 ZIP')
        if position > len(extra):
            raise ValueError('Invalid ZIP extra field')
    stream.seek(comment_size, 1)


def _preflight_zip(stream, limits, input_limit=None):
    position = stream.tell()
    try:
        stream.seek(0, 2)
        size = stream.tell()
        if size > (limits['MAX_ARCHIVE_BYTES'] if input_limit is None else input_limit):
            raise ValueError('Archive byte size limit exceeded')
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
        index = tail.rfind(b'PK\x05\x06')
        if index < 0 or index + 22 > len(tail):
            raise ValueError('Invalid ZIP end of central directory')
        disk, central_disk, disk_count, count, central_size, offset, comment = struct.unpack_from(
            '<HHHHIIH', tail, index + 4)
        end = size - len(tail) + index
        if disk or central_disk or disk_count != count or count == 65535 or 0xffffffff in (central_size, offset):
            raise ValueError('Unsupported ZIP64 or multidisk ZIP')
        if index + 22 + comment != len(tail) or offset + central_size != end:
            raise ValueError('Invalid ZIP central directory bounds')
        if count > limits['MAX_MEMBERS']:
            raise ValueError('Archive member count limit exceeded')
        if central_size > limits['MAX_CENTRAL_DIRECTORY_BYTES']:
            raise ValueError('Archive central directory byte limit exceeded')
        stream.seek(offset)
        actual = 0
        while stream.tell() < end:
            actual += 1
            if actual > limits['MAX_MEMBERS']:
                raise ValueError('Archive member count limit exceeded')
            _preflight_record(stream, end)
        if actual != count or stream.tell() != end:
            raise ValueError('Invalid ZIP central directory count or bounds')
    finally:
        stream.seek(position)


def _validate_members(archive: ZipFile, *, require_docx=True, limits=None):
    limits = globals() if limits is None else limits
    members = archive.infolist()
    if len(members) > limits['MAX_MEMBERS']:
        raise ValueError("Archive member count limit exceeded")
    names = set()
    total = 0
    for info in members:
        name = info.orig_filename
        pieces = name.rstrip("/").split("/")
        if (not name or name.startswith("/") or "\\" in name or ":" in name
                or "\x00" in name or any(p in {"", ".", ".."} for p in pieces)):
            raise ValueError("Unsafe archive path")
        if name in names:
            raise ValueError("Archive contains duplicate names")
        names.add(name)
        total += info.file_size
        if info.file_size > limits['MAX_PART_BYTES'] or total > limits['MAX_TOTAL_BYTES']:
            raise ValueError("Archive uncompressed size limit exceeded")
        if info.file_size > max(info.compress_size, 1) * limits['MAX_COMPRESSION_RATIO']:
            raise ValueError("Archive compression ratio limit exceeded")
        if info.flag_bits & 1 or info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
            raise ValueError("Encrypted or unsupported archive compression")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError("Unsafe archive path: symbolic link")
    if require_docx and not {"[Content_Types].xml", DOCUMENT} <= names:
        raise ValueError("Missing required DOCX package parts")
    return members


def _read_package(content: bytes, budget):
    parts = {}
    warnings = []
    totals = Counter()
    stream = BytesIO(content)
    _preflight_zip(stream, globals())
    with ZipFile(stream) as archive:
        for info in _validate_members(archive):
            if info.is_dir():
                continue
            with archive.open(info) as stream:
                data = stream.read(MAX_PART_BYTES + 1)
            if len(data) > MAX_PART_BYTES or len(data) != info.file_size:
                raise ValueError("Archive part size limit or size mismatch")
            name = info.filename
            root = _parse_xml(data, name, totals) if name.endswith((".xml", ".rels")) else None
            parts[name] = root
            if name.endswith(".rels"):
                for relation in root.iter():
                    if relation.get("TargetMode") == "External":
                        warnings.append(budget.consume(f"External relationship not followed: {name}#{relation.get('Id', '')}"))
                if name != DOCUMENT_RELS:
                    warnings.append(budget.consume(f"Relationships not resolved: {name}"))
    document = parts[DOCUMENT]
    content_types = parts["[Content_Types].xml"]
    valid_type = content_types.tag == f"{{{CT}}}Types" and any(
        node.tag == f"{{{CT}}}Override" and node.get("PartName") == "/" + DOCUMENT
        and node.get("ContentType") == MAIN_TYPE for node in content_types
    )
    if not valid_type or document.tag != f"{{{W}}}document":
        raise ValueError("Not a supported DOCX document")
    return parts, warnings


def _element_warnings(root, budget):
    warnings = set()

    def add(tag):
        warning = f"Unsupported element: {tag}"
        if warning not in warnings:
            warnings.add(budget.consume(warning))
    stack = [root]
    while stack:
        node = stack.pop()
        local = node.tag.rsplit("}", 1)[-1]
        if node.tag.startswith(f"{{{W}}}") and local in PROPERTY_ELEMENTS:
            for child in node.iter():
                child_name = child.tag.rsplit("}", 1)[-1]
                if child_name.endswith("Reference") and child.tag not in {
                    f"{{{W}}}headerReference", f"{{{W}}}footerReference"
                }:
                    add(child.tag)
            continue
        if node.tag in {f"{{{W}}}fldSimple", f"{{{W}}}fldChar", f"{{{W}}}instrText"}:
            warning = "Field not evaluated; cached field text retained."
            if warning not in warnings:
                warnings.add(budget.consume(warning))
        if not node.tag.startswith(f"{{{W}}}") or local not in SUPPORTED_ELEMENTS:
            add(node.tag)
        stack.extend(node)
    return sorted(warnings)


def _block_text(element, budget, include_field_instructions=False):
    sink = _BoundedSink(budget)
    boundaries = {f"{{{W}}}{name}": separator for name, separator in
                  (("p", "\n"), ("tbl", "\n"), ("tr", "\n"), ("tc", "\t"))}

    def visit(node):
        if node.tag.startswith(f"{{{W}}}") and node.tag.rsplit("}", 1)[-1] in PROPERTY_ELEMENTS:
            return
        if node.tag == f"{{{W}}}t" or (include_field_instructions and node.tag == f"{{{W}}}instrText"):
            sink.write(node.text or "")
        elif node.tag == f"{{{W}}}tab":
            sink.write("\t")
        elif node.tag in {f"{{{W}}}br", f"{{{W}}}cr"}:
            sink.write("\n")
        seen = False
        for child in node:
            if child.tag in boundaries:
                if seen:
                    sink.write(boundaries[child.tag])
                seen = True
            visit(child)

    visit(element)
    return sink.getvalue()


def _extract_blocks(body, budget, part=DOCUMENT, path="/w:document/w:body"):
    blocks = []
    counts = Counter()
    for element in body:
        local = element.tag.rsplit("}", 1)[-1]
        counts[element.tag] += 1
        if element.tag == f"{{{W}}}sectPr":
            continue
        name = f"w:{local}" if element.tag.startswith(f"{{{W}}}") else element.tag
        kind = {f"{{{W}}}p": "paragraph", f"{{{W}}}tbl": "table"}.get(element.tag, "unsupported")
        locator = f"{part}:{path}/{name}[{counts[element.tag]}]"
        text = _block_text(element, budget)
        budget.consume(kind)
        budget.consume(locator)
        sink = _BoundedSink(budget)
        ET.ElementTree(element).write(sink, encoding="unicode")
        blocks.append({
            "index": len(blocks),
            "kind": kind,
            "text": text,
            "raw_xml": sink.getvalue(),
            "locator": locator,
        })
    return blocks


def _relative_target(target):
    if (not target or target.startswith('/') or any(c in target for c in '\\:%?#')
            or any(ord(c) < 33 for c in target)):
        return None
    pieces = ['word']
    for piece in target.split('/'):
        if piece == '..':
            if not pieces:
                return None
            pieces.pop()
        elif piece not in {'', '.'}:
            pieces.append(piece)
        elif piece == '':
            return None
    return '/'.join(pieces)


def _relationships(parts):
    root = parts.get(DOCUMENT_RELS)
    if root is None:
        return {}
    if root.tag != f'{{{PR}}}Relationships':
        raise ValueError('Invalid document relationships root')
    relations = {}
    for node in root:
        rid = node.get('Id')
        if node.tag != f'{{{PR}}}Relationship' or not rid or rid in relations:
            raise ValueError('Invalid or duplicate document relationship IDs')
        relations[rid] = node
    return relations


def _story_target(reference, relations, parts):
    kind = reference.tag.rsplit('}', 1)[-1].removesuffix('Reference')
    relation = relations.get(reference.get(f'{{{R}}}id'))
    if (relation is None or relation.get('Type') != f'{R}/{kind}'
            or relation.get('TargetMode', 'Internal') != 'Internal'
            or reference.get(f'{{{W}}}type') not in {'default', 'first', 'even'}):
        return None
    part = _relative_target(relation.get('Target', ''))
    root = parts.get(part)
    if root is None or root.tag != f'{{{W}}}{"hdr" if kind == "header" else "ftr"}':
        return None
    types = parts['[Content_Types].xml']
    overrides = [n.get('ContentType') for n in types
                 if n.tag == f'{{{CT}}}Override' and n.get('PartName') == '/' + part]
    defaults = [n.get('ContentType') for n in types if n.tag == f'{{{CT}}}Default'
                and n.get('Extension') == part.rsplit('.', 1)[-1]]
    expected = f'application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml'
    return part if (overrides or defaults) == [expected] else None


def _section_references(node, path='/w:document'):
    counts = Counter()
    for child in node:
        counts[child.tag] += 1
        name = 'w:' + child.tag.rsplit('}', 1)[-1] if child.tag.startswith(f'{{{W}}}') else child.tag
        child_path = f'{path}/{name}[{counts[child.tag]}]'
        if child.tag == f'{{{W}}}sectPr':
            yield child, child_path
        else:
            yield from _section_references(child, child_path)


def _extract_stories(parts, budget, warnings):
    relations = _relationships(parts)
    stories = {}
    contexts = []
    for section_index, (section, path) in enumerate(_section_references(parts[DOCUMENT])):
        counts = Counter()
        for ref in section:
            counts[ref.tag] += 1
            if ref.tag not in {f'{{{W}}}headerReference', f'{{{W}}}footerReference'}:
                continue
            kind = ref.tag.rsplit('}', 1)[-1].removesuffix('Reference')
            part = _story_target(ref, relations, parts)
            sink = _BoundedSink(budget)
            ET.ElementTree(ref).write(sink, encoding='unicode')
            contexts.append({
                'section_index': section_index,
                'kind': budget.consume(kind),
                'reference_type': budget.consume(ref.get(f'{{{W}}}type', '')),
                'relationship_id': budget.consume(ref.get(f'{{{R}}}id', '')),
                'story_part': budget.consume(part) if part else None,
                'locator': budget.consume(f'{DOCUMENT}:{path}/w:{kind}Reference[{counts[ref.tag]}]'),
                'raw_xml': sink.getvalue(),
            })
            if part is None:
                warnings.append(budget.consume(f'Unresolved {kind} reference: {ref.get(f"{{{R}}}id", "")}'))
            elif part not in stories:
                root = parts[part]
                warnings.extend(_element_warnings(root, budget))
                stories[part] = {'part': budget.consume(part), 'kind': budget.consume(kind),
                                 'blocks': _extract_blocks(root, budget, part, '/w:' + root.tag.rsplit('}', 1)[-1])}
    return list(stories.values()), contexts


def _package_warnings(parts, stories, note_parts, budget, warnings):
    extracted = {DOCUMENT, '[Content_Types].xml', DOCUMENT_RELS, *note_parts,
                 *(s['part'] for s in stories)}
    for name in parts:
        if name not in extracted:
            warnings.append(budget.consume(f'Unsupported package part not extracted: {name}'))
    supported = {f'{R}/{kind}' for kind in ('header', 'footer', 'footnotes', 'endnotes')}
    if any(r.get('Type') not in supported for r in _relationships(parts).values()):
        warnings.append(budget.consume(f'Relationships not resolved: {DOCUMENT_RELS}'))


def extract_docx_blocks(content: bytes, *, _budget=None) -> dict:
    from .document_notes import extract_notes

    if not isinstance(content, bytes):
        raise TypeError("DOCX content must be bytes")
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError("Archive byte size limit exceeded")
    budget = _OutputBudget() if _budget is None else _budget
    try:
        parts, warnings = _read_package(content, budget)
    except (BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
        raise ValueError("Invalid or unsupported DOCX ZIP archive") from exc
    document = parts[DOCUMENT]
    stories, contexts = _extract_stories(parts, budget, warnings)
    notes, note_contexts, note_parts = extract_notes(parts, stories, budget, warnings)
    _package_warnings(parts, stories, note_parts, budget, warnings)
    bodies = document.findall(f"{{{W}}}body")
    if len(bodies) != 1:
        raise ValueError("DOCX requires exactly one document body")
    body = bodies[0]
    warnings.extend(_element_warnings(document, budget))
    completeness = budget.consume("incomplete" if warnings else "unverified")
    warnings.extend(budget.consume(warning) for warning in (
        "Evidence extraction only; completeness, rendering, legal hierarchy and coverage are not certified.",
        "raw_xml is namespace-preserving XML serialization, not byte-identical source XML.",
    ))
    source_format = budget.consume("docx")
    return {
        "source_format": source_format,
        "blocks": _extract_blocks(body, budget),
        "stories": stories,
        "reference_contexts": contexts,
        "notes": notes,
        "note_reference_contexts": note_contexts,
        "completeness": completeness,
        "warnings": warnings,
    }

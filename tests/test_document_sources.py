from importlib import import_module, util
from io import BytesIO
from pathlib import Path
import hashlib
import json
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo
import xml.etree.ElementTree as ET

import pytest


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"


def package(body="", extras=None, compression=ZIP_STORED, namespaces=""):
    stream = BytesIO()
    with ZipFile(stream, "w", compression=compression) as archive:
        archive.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="{CT}"><Override PartName="/word/document.xml" '
            f'ContentType="{MAIN_TYPE}"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{W}" {namespaces}><w:body>{body}</w:body></w:document>',
        )
        for name, value in (extras or {}).items():
            info = ZipInfo("placeholder")
            info.filename = name
            archive.writestr(info, value, compress_type=compression)
    return stream.getvalue()


def test_public_adapter_exists():
    assert util.find_spec("src.parser.document_sources") is not None, (
        "DOCX evidence adapter is not implemented"
    )


@pytest.fixture
def adapter():
    return import_module("src.parser.document_sources")


def test_preserves_paragraph_table_paragraph_order_and_locators(adapter):
    content = package(
        '<w:p><w:r><w:t xml:space="preserve"> Before </w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Cell</w:t></w:r></w:p>'
        '</w:tc></w:tr></w:tbl><w:p><w:r><w:t>After</w:t></w:r></w:p>'
    )
    result = adapter.extract_docx_blocks(content)
    assert result["source_format"] == "docx"
    assert [b["kind"] for b in result["blocks"]] == ["paragraph", "table", "paragraph"]
    assert [b["text"] for b in result["blocks"]] == [" Before ", "Cell", "After"]
    assert [b["index"] for b in result["blocks"]] == [0, 1, 2]
    assert [b["locator"] for b in result["blocks"]] == [
        "word/document.xml:/w:document/w:body/w:p[1]",
        "word/document.xml:/w:document/w:body/w:tbl[1]",
        "word/document.xml:/w:document/w:body/w:p[2]",
    ]
    assert ET.fromstring(result["blocks"][0]["raw_xml"]).tag == f"{{{W}}}p"
    assert result["completeness"] == "unverified"
    assert result["warnings"]
    assert "articles" not in result


def test_retains_empty_blocks_and_text_breaks(adapter):
    result = adapter.extract_docx_blocks(package(
        '<w:p/><w:p><w:r><w:t>A</w:t><w:tab/><w:t>B</w:t>'
        '<w:br/><w:t>C</w:t></w:r></w:p>'
    ))
    assert [b["text"] for b in result["blocks"]] == ["", "A\tB\nC"]


def test_preserves_merged_cell_xml_without_expanding_cells(adapter):
    table = (
        '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/>'
        '<w:vMerge w:val="restart"/></w:tcPr><w:p><w:r><w:t>Merged</w:t>'
        '</w:r></w:p></w:tc></w:tr><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/>'
        '<w:vMerge/></w:tcPr><w:p/></w:tc></w:tr></w:tbl>'
    )
    result = adapter.extract_docx_blocks(package(table))
    root = ET.fromstring(result["blocks"][0]["raw_xml"])
    assert len(root.findall(f".//{{{W}}}tc")) == 2
    assert [e.get(f"{{{W}}}val") for e in root.iter(f"{{{W}}}gridSpan")] == ["2", "2"]
    assert [e.get(f"{{{W}}}val") for e in root.iter(f"{{{W}}}vMerge")] == ["restart", None]


@pytest.mark.parametrize("element", ["drawing", "pict", "txbxContent", "object", "altChunk", "sdt", "footnoteReference"])
def test_flags_unsupported_elements_even_with_body_text(adapter, element):
    result = adapter.extract_docx_blocks(package(
        f'<w:p><w:r><w:t>Readable</w:t><w:{element}/></w:r></w:p>'
    ))
    assert result["completeness"] == "incomplete"
    assert any(element in warning for warning in result["warnings"])
    assert element in result["blocks"][0]["raw_xml"]


@pytest.mark.parametrize("part", [
    "word/header1.xml", "word/footer1.xml", "word/footnotes.xml",
    "word/endnotes.xml", "word/comments.xml", "word/media/image1.png",
    "word/embeddings/object.bin", "customXml/item1.xml", "unexpected.bin",
])
def test_flags_unsupported_package_parts(adapter, part):
    payload = '<unsupported/>' if part.endswith(".xml") else b"binary"
    result = adapter.extract_docx_blocks(package('<w:p/>', {part: payload}))
    assert result["completeness"] == "incomplete"
    assert any(part in warning for warning in result["warnings"])


def test_keeps_unknown_top_level_evidence_in_order(adapter):
    result = adapter.extract_docx_blocks(package('<w:p/><w:sdt><w:p/></w:sdt><w:p/>'))
    assert [b["kind"] for b in result["blocks"]] == ["paragraph", "unsupported", "paragraph"]
    assert "sdt" in result["blocks"][1]["raw_xml"]
    assert result["completeness"] == "incomplete"


def test_flags_external_relationship_without_accessing_target(adapter):
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="image" Target="file:///private/secret" '
        'TargetMode="External"/></Relationships>'
    )
    result = adapter.extract_docx_blocks(package('<w:p/>', {"word/_rels/document.xml.rels": rels}))
    assert result["completeness"] == "incomplete"
    assert any("External" in warning for warning in result["warnings"])


@pytest.mark.parametrize("content", [b"not a zip", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"PK\x03\x04"])
def test_rejects_invalid_zip_and_legacy_doc(adapter, content):
    with pytest.raises(ValueError):
        adapter.extract_docx_blocks(content)


def test_rejects_non_bytes_input(adapter):
    with pytest.raises(TypeError):
        adapter.extract_docx_blocks("not bytes")


@pytest.mark.parametrize("name", ["../escape.xml", "/absolute.xml", "C:/drive.xml", "word/../../escape", "word\\escape.xml"])
def test_rejects_unsafe_archive_paths(adapter, name):
    with pytest.raises(ValueError, match="path"):
        adapter.extract_docx_blocks(package(extras={name: b"x"}))


@pytest.mark.parametrize("limit,value", [
    ("MAX_ARCHIVE_BYTES", 16), ("MAX_MEMBERS", 1),
    ("MAX_PART_BYTES", 32), ("MAX_TOTAL_BYTES", 64),
])
def test_rejects_archive_limits_before_parsing(adapter, monkeypatch, limit, value):
    content = package('<w:p/>')
    monkeypatch.setattr(adapter, limit, value)
    with pytest.raises(ValueError, match="limit"):
        adapter.extract_docx_blocks(content)


def test_rejects_excessive_compression_ratio(adapter):
    with pytest.raises(ValueError, match="compression"):
        adapter.extract_docx_blocks(package(extras={"huge.bin": b"0" * 100000}, compression=ZIP_DEFLATED))


def test_rejects_duplicate_archive_names(adapter):
    stream = BytesIO(package())
    with pytest.warns(UserWarning, match="Duplicate"), ZipFile(stream, "a") as archive:
        archive.writestr("word/document.xml", "<invalid/>")
    with pytest.raises(ValueError, match="duplicate"):
        adapter.extract_docx_blocks(stream.getvalue())


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16", "utf-32"])
def test_rejects_dtd_and_entities_in_any_xml_part(adapter, encoding):
    xml = '<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///private/secret">]><x>&secret;</x>'
    with pytest.raises(ValueError, match="DTD|entity"):
        adapter.extract_docx_blocks(package(extras={"word/header1.xml": xml.encode(encoding)}))


@pytest.mark.parametrize("extras", [
    {"word/header1.xml": b"<broken>"},
    {"[Content_Types].xml": b"<Types/>"},
    {"word/document.xml": f'<w:document xmlns:w="{W}"/>'.encode()},
    {"word/document.xml": b"<not_a_document/>"},
])
def test_rejects_malformed_xml_or_non_docx_structure(adapter, extras):
    if "[Content_Types].xml" in extras or "word/document.xml" in extras:
        stream = BytesIO()
        with ZipFile(BytesIO(package())) as original, ZipFile(stream, "w") as archive:
            for info in original.infolist():
                archive.writestr(info.filename, extras.get(info.filename, original.read(info)))
        content = stream.getvalue()
    else:
        content = package(extras=extras)
    with pytest.raises(ValueError):
        adapter.extract_docx_blocks(content)


def test_rejects_zip_without_required_docx_parts(adapter):
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("other.txt", "text")
    with pytest.raises(ValueError):
        adapter.extract_docx_blocks(stream.getvalue())


@pytest.mark.parametrize("blocks,uri_size,limit", [(12, 256, 1600), (1, 2048, 600)])
def test_rejects_namespace_output_amplification_while_serializing(
    adapter, monkeypatch, blocks, uri_size, limit
):
    content = package(
        '<w:p x:val="1"/>' * blocks,
        namespaces=f'xmlns:x="urn:{"n" * uri_size}"',
    )
    monkeypatch.setattr(adapter, "MAX_OUTPUT_BYTES", limit, raising=False)
    started = []
    completed = []
    original_write = ET.ElementTree.write

    def observed_write(tree, sink, *args, **kwargs):
        started.append(True)
        original_write(tree, sink, *args, **kwargs)
        completed.append(True)

    def forbidden_tostring(*args, **kwargs):
        pytest.fail("Output must not be materialized by tostring")

    monkeypatch.setattr(ET.ElementTree, "write", observed_write)
    monkeypatch.setattr(ET, "tostring", forbidden_tostring)
    with pytest.raises(ValueError, match="[Oo]utput.*limit"):
        adapter.extract_docx_blocks(content)
    assert len(started) > len(completed)
    assert len(completed) < blocks


@pytest.mark.parametrize("limit,value,body", [
    ("MAX_XML_DEPTH", 8, '<w:p>' * 12 + '</w:p>' * 12),
    ("MAX_XML_NODES", 20, '<w:p/>' * 24),
    ("MAX_XML_NAMESPACE_BYTES", 128, '<x:p xmlns:x="urn:' + 'n' * 160 + '"/>'),
    ("MAX_XML_NAMESPACE_TOTAL_BYTES", 256,
     ''.join(f'<x:p xmlns:x="urn:{i}' + 'n' * 80 + '"/>' for i in range(4))),
    ("MAX_XML_ATTRIBUTE_BYTES", 256, '<w:p value="' + 'a' * 300 + '"/>'),
    ("MAX_XML_TEXT_BYTES", 256, '<w:p><w:r><w:t>' + 't' * 300 + '</w:t></w:r></w:p>'),
])
def test_rejects_xml_structural_limits_before_serialization(adapter, monkeypatch, limit, value, body):
    monkeypatch.setattr(adapter, limit, value, raising=False)

    def forbidden_serialization(*args, **kwargs):
        pytest.fail("Structural limits must be checked before serialization")

    monkeypatch.setattr(ET, "tostring", forbidden_serialization)
    monkeypatch.setattr(ET.ElementTree, "write", forbidden_serialization)
    with pytest.raises(ValueError, match="XML.*limit"):
        adapter.extract_docx_blocks(package(body))


def test_xml_node_budget_is_shared_by_package_parts(adapter, monkeypatch):
    monkeypatch.setattr(adapter, "MAX_XML_NODES", 10, raising=False)
    with pytest.raises(ValueError, match="XML.*limit"):
        adapter.extract_docx_blocks(package('<w:p/>', {"extra.xml": '<x>' + '<y/>' * 8 + '</x>'}))


def test_preserves_table_row_cell_and_paragraph_boundaries(adapter):
    def paragraph(text):
        return f'<w:p><w:r><w:t>{text}</w:t></w:r></w:p>'

    table = ('<w:tbl><w:tr><w:tc>' + paragraph('12') + paragraph('extra')
             + '</w:tc><w:tc>' + paragraph('34') + '</w:tc></w:tr>'
             + '<w:tr><w:tc><w:p/></w:tc><w:tc>' + paragraph('56')
             + '</w:tc></w:tr></w:tbl>')
    result = adapter.extract_docx_blocks(package(table))
    assert result['blocks'][0]['text'] == '12\nextra\t34\n\t56'
    assert '1234' not in result['blocks'][0]['text']
    assert result['completeness'] != 'complete'


@pytest.mark.parametrize("body,extras", [
    ('<w:p><w:r><w:t>' + 'é' * 80 + '</w:t></w:r></w:p>', None),
    ('<w:p/>', {'x' * 160 + '.bin': b'x'}),
    ('<x:p xmlns:x="urn:' + 'n' * 100 + '"/>', None),
])
def test_total_output_budget_includes_all_returned_strings(adapter, monkeypatch, body, extras):
    content = package(body, extras)
    result = adapter.extract_docx_blocks(content)

    def string_bytes(value):
        if isinstance(value, str):
            return len(value.encode('utf-8'))
        if isinstance(value, dict):
            return sum(string_bytes(item) for item in value.values())
        if isinstance(value, list):
            return sum(string_bytes(item) for item in value)
        return 0

    size = string_bytes(result)
    monkeypatch.setattr(adapter, "MAX_OUTPUT_BYTES", size, raising=False)
    assert adapter.extract_docx_blocks(content) == result
    monkeypatch.setattr(adapter, "MAX_OUTPUT_BYTES", size - 1)
    with pytest.raises(ValueError, match="[Oo]utput.*limit"):
        adapter.extract_docx_blocks(content)


R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"


def story_package(body, relations, parts, types=None):
    extras = {"word/_rels/document.xml.rels": f'<Relationships xmlns="{PR}">{relations}</Relationships>'}
    extras.update(parts)
    content = package(body, extras, namespaces=f'xmlns:r="{R}"')
    stream = BytesIO()
    overrides = ''.join(
        f'<Override PartName="/{name}" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.{kind}+xml"/>'
        for name, kind in (types or {}).items()
    )
    with ZipFile(BytesIO(content)) as original, ZipFile(stream, 'w') as archive:
        for info in original.infolist():
            data = original.read(info)
            if info.filename == '[Content_Types].xml':
                data = data.replace(b'</Types>', overrides.encode() + b'</Types>')
            archive.writestr(info, data)
    return stream.getvalue()


def relationship(rid, kind, target, mode=''):
    return f'<Relationship Id="{rid}" Type="{R}/{kind}" Target="{target}" {mode}/>'


def test_stories_preserve_order_shared_section_contexts_and_body(adapter):
    body = ('<w:p><w:pPr><w:sectPr><w:headerReference w:type="first" r:id="h"/>'
            '</w:sectPr></w:pPr><w:r><w:t>Body</w:t></w:r></w:p>'
            '<w:sectPr><w:footerReference w:type="default" r:id="f"/>'
            '<w:headerReference w:type="even" r:id="h"/></w:sectPr>')
    header = (f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>H1</w:t></w:r></w:p>'
              '<w:tbl><w:tr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr>'
              '<w:p><w:r><w:t>H2</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p/></w:hdr>')
    content = story_package(body, relationship('h', 'header', './header1.xml') +
                            relationship('f', 'footer', '../word/footer1.xml'),
                            {'word/header1.xml': header, 'word/footer1.xml': f'<w:ftr xmlns:w="{W}"><w:p/></w:ftr>'},
                            {'word/header1.xml': 'header', 'word/footer1.xml': 'footer'})
    result = adapter.extract_docx_blocks(content)
    assert result['blocks'] == adapter.extract_docx_blocks(package(body, namespaces=f'xmlns:r="{R}"'))['blocks']
    assert [s['part'] for s in result['stories']] == ['word/header1.xml', 'word/footer1.xml']
    assert [b['text'] for b in result['stories'][0]['blocks']] == ['H1', 'H2', '']
    block = result['stories'][0]['blocks'][1]
    assert block['locator'] == 'word/header1.xml:/w:hdr/w:tbl[1]'
    assert ET.fromstring(block['raw_xml']).find(f'.//{{{W}}}gridSpan').get(f'{{{W}}}val') == '2'
    contexts = result['reference_contexts']
    assert [(c['section_index'], c['reference_type'], c['relationship_id'], c['story_part']) for c in contexts] == [
        (0, 'first', 'h', 'word/header1.xml'), (1, 'default', 'f', 'word/footer1.xml'),
        (1, 'even', 'h', 'word/header1.xml')]
    assert all(c['locator'].startswith('word/document.xml:') and c['raw_xml'] for c in contexts)
    assert not any('Unsupported element' in w and 'Reference' in w for w in result['warnings'])
    assert not any('not extracted: word/header1.xml' in w for w in result['warnings'])


def test_property_tabs_ignored_run_tabs_and_cached_fields_retained(adapter):
    header = (f'<w:hdr xmlns:w="{W}"><w:p><w:pPr><w:tabs><w:tab w:val="right"/></w:tabs></w:pPr>'
              '<w:r><w:t>PAGE</w:t><w:tab/></w:r><w:fldSimple w:instr="PAGE">'
              '<w:r><w:t>17</w:t></w:r></w:fldSimple></w:p></w:hdr>')
    result = adapter.extract_docx_blocks(story_package(
        '<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>',
        relationship('h', 'header', 'header1.xml'), {'word/header1.xml': header}, {'word/header1.xml': 'header'}))
    assert result['stories'][0]['blocks'][0]['text'] == 'PAGE\t17'
    assert any('field not evaluated' in w.lower() for w in result['warnings'])


@pytest.mark.parametrize('variant', ['dangling', 'duplicate', 'wrong_type', 'suffix_type', 'external',
                                    'traversal', 'encoded', 'absolute', 'wrong_content', 'wrong_root', 'bad_rels'])
def test_invalid_story_references_are_explicitly_unresolved_or_rejected(adapter, variant):
    rel = relationship('h', 'header', 'header1.xml')
    parts = {'word/header1.xml': f'<w:hdr xmlns:w="{W}"><w:p/></w:hdr>'}
    types = {'word/header1.xml': 'header'}
    if variant == 'dangling':
        parts = {}
    elif variant == 'duplicate':
        rel += rel
    elif variant == 'wrong_type':
        rel = relationship('h', 'footer', 'header1.xml')
    elif variant == 'suffix_type':
        rel = rel.replace(R + '/header', 'urn:evil/header')
    elif variant == 'external':
        rel = relationship('h', 'header', 'file:///private/secret', 'TargetMode="External"')
    elif variant in {'traversal', 'encoded', 'absolute'}:
        target = {'traversal': '../../word/header1.xml', 'encoded': '%2e%2e/header1.xml', 'absolute': '/word/header1.xml'}[variant]
        rel = relationship('h', 'header', target)
    elif variant == 'wrong_content':
        types = {'word/header1.xml': 'footer'}
    elif variant == 'wrong_root':
        parts['word/header1.xml'] = f'<w:ftr xmlns:w="{W}"/>'
    content = story_package('<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>', rel, parts, types)
    if variant == 'bad_rels':
        content = story_package(
            '<w:sectPr><w:headerReference w:type="default" r:id="h"/></w:sectPr>',
            '<x:Relationship xmlns:x="urn:evil" Id="h"/>', parts, types)
    try:
        result = adapter.extract_docx_blocks(content)
    except ValueError:
        return
    assert result['stories'] == []
    assert result['reference_contexts'][0]['story_part'] is None
    assert any('unresolved' in w.lower() and 'h' in w for w in result['warnings'])


def test_story_and_context_output_share_exact_budget(adapter, monkeypatch):
    content = story_package('<w:sectPr>' + '<w:headerReference w:type="default" r:id="h"/>' * 5 + '</w:sectPr>',
                            relationship('h', 'header', 'header1.xml'),
                            {'word/header1.xml': f'<w:hdr xmlns:w="{W}"><w:p><w:r><w:t>é</w:t></w:r></w:p></w:hdr>'},
                            {'word/header1.xml': 'header'})
    result = adapter.extract_docx_blocks(content)
    assert len(result['stories']) == 1

    def size(value):
        if isinstance(value, str):
            return len(value.encode())
        if isinstance(value, dict):
            return sum(size(v) for v in value.values())
        if isinstance(value, list):
            return sum(size(v) for v in value)
        return 0

    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', size(result))
    assert adapter.extract_docx_blocks(content) == result
    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', size(result) - 1)
    with pytest.raises(ValueError, match='Output.*limit'):
        adapter.extract_docx_blocks(content)


def test_story_xml_budget_is_shared_across_referenced_parts(adapter, monkeypatch):
    content = story_package(
        '<w:sectPr><w:headerReference w:type="default" r:id="h"/>'
        '<w:footerReference w:type="default" r:id="f"/></w:sectPr>',
        relationship('h', 'header', 'header1.xml') + relationship('f', 'footer', 'footer1.xml'),
        {'word/header1.xml': f'<w:hdr xmlns:w="{W}"><w:p/></w:hdr>',
         'word/footer1.xml': f'<w:ftr xmlns:w="{W}"><w:p/></w:ftr>'},
        {'word/header1.xml': 'header', 'word/footer1.xml': 'footer'})
    with ZipFile(BytesIO(content)) as archive:
        nodes = sum(sum(1 for _ in ET.fromstring(archive.read(n)).iter()) for n in archive.namelist())
    monkeypatch.setattr(adapter, 'MAX_XML_NODES', nodes)
    assert len(adapter.extract_docx_blocks(content)['stories']) == 2
    monkeypatch.setattr(adapter, 'MAX_XML_NODES', nodes - 1)
    with pytest.raises(ValueError, match='XML.*limit'):
        adapter.extract_docx_blocks(content)


def test_raw_appendix_retains_six_body_blocks_and_rid9_header(adapter):
    path = Path(__file__).resolve().parents[1] / 'data/raw/Phụ lục I.docx'
    if not path.exists():
        pytest.skip('Local raw appendix unavailable')
    result = adapter.extract_docx_blocks(path.read_bytes())
    assert len(result['blocks']) == 6
    assert any(c['relationship_id'] == 'rId9' and c['story_part'] for c in result['reference_contexts'])
    assert any(b['text'] == '17' and 'PAGE' in b['raw_xml'] for s in result['stories'] for b in s['blocks'])
    assert any('field not evaluated' in w.lower() for w in result['warnings'])


def notes_package(body='', footnotes=None, endnotes=None, relation_kind='footnotes', mode=''):
    parts = {}
    types = {}
    relations = ''
    for kind, entries in [('endnotes', endnotes), ('footnotes', footnotes)]:
        if entries is None:
            continue
        part = f'word/{kind}.xml'
        parts[part] = f'<w:{kind} xmlns:w="{W}">{entries}</w:{kind}>'
        types[part] = kind
        relations += relationship(kind, relation_kind if kind == 'footnotes' else kind, kind + '.xml', mode)
    return story_package(body, relations, parts, types)


def note(kind, note_id, text='', note_type=''):
    attrs = f'w:id="{note_id}"' if note_id is not None else ''
    if note_type:
        attrs += f' w:type="{note_type}"'
    return f'<w:{kind} {attrs}><w:p><w:r><w:t>{text}</w:t></w:r></w:p><w:p/></w:{kind}>'


def test_notes_preserve_package_entry_and_block_order_without_body_splicing(adapter):
    body = '<w:p><w:r><w:t>Body</w:t><w:footnoteReference w:id="9"/><w:endnoteReference w:id="4"/><w:footnoteReference w:id="9"/></w:r></w:p>'
    content = notes_package(body, note('footnote', '9', 'Foot') + note('footnote', '-1', '', 'separator') +
                            note('footnote', '2', 'Orphan') + note('footnote', '0', '', 'continuationSeparator'),
                            note('endnote', '4', 'End'))
    result = adapter.extract_docx_blocks(content)
    assert result['blocks'] == adapter.extract_docx_blocks(package(body))['blocks']
    assert [(n['kind'], n['note_id'], n['note_type']) for n in result['notes']] == [
        ('endnote', '4', None), ('footnote', '9', None), ('footnote', '-1', 'separator'),
        ('footnote', '2', None), ('footnote', '0', 'continuationSeparator')]
    assert [b['text'] for b in result['notes'][1]['blocks']] == ['Foot', '']
    assert result['notes'][1]['blocks'][0]['locator'] == 'word/footnotes.xml:/w:footnotes/w:footnote[1]/w:p[1]'
    assert ET.fromstring(result['notes'][1]['raw_xml']).get(f'{{{W}}}id') == '9'
    contexts = result['note_reference_contexts']
    assert [c['note_id'] for c in contexts] == ['9', '4', '9']
    assert [c['note_part'] for c in contexts] == ['word/footnotes.xml', 'word/endnotes.xml', 'word/footnotes.xml']
    assert len({c['locator'] for c in contexts}) == 3
    assert all(c['source_part'] == 'word/document.xml' and c['raw_xml'] for c in contexts)
    assert any('Orphan' in w and '2' in w for w in result['warnings'])
    assert not any('Unsupported package part not extracted: word/footnotes' in w for w in result['warnings'])
    assert result['completeness'] != 'complete'


@pytest.mark.parametrize('variant', ['missing_id', 'duplicate_id', 'numeric_duplicate', 'dangling', 'missing_ref_id',
                                     'relationship_id', 'wrong_type', 'suffix_type', 'wrong_content', 'wrong_root',
                                     'external', 'traversal'])
def test_invalid_note_ids_and_targets_are_unresolved_or_rejected(adapter, variant):
    entries = note('footnote', None if variant == 'missing_id' else '9', 'Foot')
    if variant in {'duplicate_id', 'numeric_duplicate'}:
        entries += note('footnote', '09' if variant == 'numeric_duplicate' else '9', 'Duplicate')
    ref_id = {'dangling': '3', 'relationship_id': 'footnotes'}.get(variant, '9')
    ref = '<w:footnoteReference/>' if variant == 'missing_ref_id' else f'<w:footnoteReference w:id="{ref_id}"/>'
    rel = relationship('footnotes', 'footnotes', 'footnotes.xml')
    types = {'word/footnotes.xml': 'footnotes'}
    root = f'<w:footnotes xmlns:w="{W}">{entries}</w:footnotes>'
    if variant == 'wrong_type':
        rel = relationship('footnotes', 'endnotes', 'footnotes.xml')
    elif variant == 'suffix_type':
        rel = rel.replace(R + '/footnotes', 'urn:evil/footnotes')
    elif variant == 'wrong_content':
        types['word/footnotes.xml'] = 'endnotes'
    elif variant == 'wrong_root':
        root = root.replace('w:footnotes', 'w:endnotes')
    elif variant == 'external':
        rel = relationship('footnotes', 'footnotes', 'https://invalid.test/notes', 'TargetMode="External"')
    elif variant == 'traversal':
        rel = relationship('footnotes', 'footnotes', '../../word/footnotes.xml')
    try:
        result = adapter.extract_docx_blocks(story_package('<w:p><w:r>' + ref + '</w:r></w:p>', rel,
                                                          {'word/footnotes.xml': root}, types))
    except ValueError:
        return
    assert result['note_reference_contexts'][0]['note_part'] is None
    assert any('Unresolved footnote reference' in w for w in result['warnings'])


def test_notes_and_repeated_references_share_exact_output_and_xml_budgets(adapter, monkeypatch):
    content = notes_package('<w:p><w:r>' + '<w:footnoteReference w:id="9"/>' * 8 + '</w:r></w:p>',
                            note('footnote', '9', 'é' * 20))
    result = adapter.extract_docx_blocks(content)
    assert len(result['notes']) == 1
    assert len(result['note_reference_contexts']) == 8

    def size(value):
        if isinstance(value, str):
            return len(value.encode())
        if isinstance(value, dict):
            return sum(size(v) for v in value.values())
        if isinstance(value, list):
            return sum(size(v) for v in value)
        return 0

    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', size(result))
    assert adapter.extract_docx_blocks(content) == result
    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', size(result) - 1)
    with pytest.raises(ValueError, match='Output.*limit'):
        adapter.extract_docx_blocks(content)
    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', 16 * 1024 * 1024)
    with ZipFile(BytesIO(content)) as archive:
        nodes = sum(sum(1 for _ in ET.fromstring(archive.read(n)).iter()) for n in archive.namelist())
    monkeypatch.setattr(adapter, 'MAX_XML_NODES', nodes)
    assert len(adapter.extract_docx_blocks(content)['notes']) == 1
    monkeypatch.setattr(adapter, 'MAX_XML_NODES', nodes - 1)
    with pytest.raises(ValueError, match='XML.*limit'):
        adapter.extract_docx_blocks(content)


def test_raw_appendix_notes_are_separators_only_with_unchanged_body_and_header(adapter):
    path = Path(__file__).resolve().parents[1] / 'data/raw/Phụ lục I.docx'
    if not path.exists():
        pytest.skip('Local raw appendix unavailable')
    result = adapter.extract_docx_blocks(path.read_bytes())
    assert len(result['blocks']) == 6
    assert len(result['notes']) == 4
    assert [(n['kind'], n['note_type']) for n in result['notes']] == [
        ('footnote', 'separator'), ('footnote', 'continuationSeparator'),
        ('endnote', 'separator'), ('endnote', 'continuationSeparator')]
    assert not any(b['text'] for n in result['notes'] for b in n['blocks'])
    assert result['note_reference_contexts'] == []
    assert all(s['kind'] != 'footer' for s in result['stories'])
    assert any(b['text'] == '17' and 'PAGE' in b['raw_xml'] for s in result['stories'] for b in s['blocks'])
    assert any('numbering.xml' in w for w in result['warnings'])
    assert any('styles.xml' in w for w in result['warnings'])


def test_empty_document_does_not_certify_completeness(adapter):
    result = adapter.extract_docx_blocks(package())
    assert result["blocks"] == []
    assert result["completeness"] != "complete"
    assert result["warnings"]

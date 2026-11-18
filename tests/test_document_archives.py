from importlib import import_module, util
from io import BytesIO
import hashlib
import json
import struct
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo
import xml.etree.ElementTree as ET

import pytest

from tests.test_document_sources import package


def bundle(entries, compression=0):
    stream = BytesIO()
    with ZipFile(stream, 'w', compression=compression) as archive:
        for name, data in entries:
            archive.writestr(name, data)
    return stream.getvalue()


def test_blocked_page_does_not_claim_payload_crc_verified(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_PROCESSING_BYTES', 0)
    result = adapter.extract_zip_page(BytesIO(bundle([('a.doc', b'legacy')])), extract=True)
    assert result['members'] == []
    assert result['crc_verified'] is False
    assert result['crc_scope'] == 'returned_members_only'


def test_public_zip_adapter_exists():
    assert util.find_spec('src.parser.document_archives') is not None, 'ZIP evidence adapter missing'


@pytest.fixture
def adapter():
    assert util.find_spec('src.parser.document_archives') is not None, 'ZIP evidence adapter missing'
    return import_module('src.parser.document_archives')


def test_preserves_order_unicode_paths_hashes_and_docx_evidence(adapter):
    docx = package('<w:p><w:r><w:t>Evidence</w:t></w:r></w:p>')
    entries = [('hồ sơ/', b''), ('hồ sơ/B.DOCX', docx), ('a.doc', b'legacy')]
    result = adapter.extract_zip_sources(bundle(entries))
    assert result['source_format'] == 'zip'
    assert result['completeness'] == 'incomplete'
    assert [m['path'] for m in result['members']] == [n for n, _ in entries]
    assert [m['sha256'] for m in result['members']] == [hashlib.sha256(d).hexdigest() for _, d in entries]
    assert [m['status'] for m in result['members']] == ['directory', 'extracted', 'unsupported']
    assert result['members'][1]['format'] == 'docx'
    assert result['members'][1]['evidence']['blocks'][0]['text'] == 'Evidence'
    assert result['warnings']


def test_explicit_unsupported_and_office_temp_classification_without_recursion(adapter):
    entries = [('a.doc', b'legacy'), ('b.pdf', b'%PDF'), ('c.rar', b'Rar!'),
               ('d.zip', b'not parsed'), ('other.bin', b'x'), ('~$locked.docx', b'not docx')]
    members = adapter.extract_zip_sources(bundle(entries))['members']
    assert [m['format'] for m in members] == ['doc', 'pdf', 'rar', 'zip', 'unknown', 'docx']
    assert [m['status'] for m in members] == ['unsupported'] * 5 + ['ignored_office_lock']
    assert all('evidence' not in m for m in members)


@pytest.mark.parametrize('name', ['../x', '/x', 'C:/x', 'x\\y', 'x//y', './x', 'x/../y', 'x\x00y'])
def test_rejects_unsafe_paths(adapter, name):
    info = ZipInfo('placeholder')
    info.filename = name
    with pytest.raises(ValueError, match='path'):
        adapter.extract_zip_sources(bundle([(info, b'x')]))


def test_rejects_duplicates_and_symlinks(adapter):
    with pytest.warns(UserWarning, match='Duplicate'):
        duplicate = bundle([('x', b'a'), ('x', b'b')])
    with pytest.raises(ValueError, match='duplicate'):
        adapter.extract_zip_sources(duplicate)
    link = ZipInfo('link')
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with pytest.raises(ValueError, match='symbolic'):
        adapter.extract_zip_sources(bundle([(link, b'target')]))


@pytest.mark.parametrize('content', [b'', b'not zip', b'PK\x03\x04'])
def test_rejects_invalid_zip(adapter, content):
    with pytest.raises(ValueError):
        adapter.extract_zip_sources(content)


def test_rejects_non_bytes(adapter):
    with pytest.raises(TypeError):
        adapter.extract_zip_sources('not bytes')


def test_crc_checked_even_for_unsupported_and_ignored_members(adapter):
    for name in ('a.doc', '~$a.docx', 'a.zip'):
        content = bytearray(bundle([(name, b'unique-payload')]))
        offset = content.index(b'unique-payload')
        content[offset] ^= 1
        with pytest.raises(ValueError, match='ZIP'):
            adapter.extract_zip_sources(bytes(content))


def test_rejects_encrypted_members_before_reading(adapter):
    content = bytearray(bundle([('x', b'x')]))
    for signature, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):
        position = content.index(signature) + offset
        struct.pack_into('<H', content, position, 1)
    with pytest.raises(ValueError, match='Encrypted'):
        adapter.extract_zip_sources(bytes(content))


@pytest.mark.parametrize('limit,value', [('MAX_ARCHIVE_BYTES', 16), ('MAX_MEMBERS', 1),
                                          ('MAX_PART_BYTES', 2), ('MAX_TOTAL_BYTES', 4)])
def test_rejects_outer_limits(adapter, monkeypatch, limit, value):
    monkeypatch.setattr(adapter, limit, value)
    with pytest.raises(ValueError, match='limit'):
        adapter.extract_zip_sources(bundle([('a.doc', b'abc'), ('b.doc', b'abc')]))


def test_rejects_compression_bomb(adapter):
    with pytest.raises(ValueError, match='ratio'):
        adapter.extract_zip_sources(bundle([('x', b'0' * 100000)], ZIP_DEFLATED))


def test_rejects_invalid_docx_instead_of_treating_as_legacy(adapter):
    with pytest.raises(ValueError):
        adapter.extract_zip_sources(bundle([('x.docx', b'legacy')]))


def test_nested_processing_budget_is_cumulative_before_second_docx_serialization(adapter, monkeypatch):
    docx = package('<w:p/>')
    with ZipFile(BytesIO(docx)) as archive:
        expanded = sum(i.file_size for i in archive.infolist())
    monkeypatch.setattr(adapter, 'MAX_PROCESSING_BYTES', 2 * len(docx) + expanded)
    writes = []
    original = ET.ElementTree.write

    def observed(tree, sink, *args, **kwargs):
        writes.append(True)
        return original(tree, sink, *args, **kwargs)

    monkeypatch.setattr(ET.ElementTree, 'write', observed)
    with pytest.raises(ValueError, match='processing.*limit'):
        adapter.extract_zip_sources(bundle([('a.docx', docx), ('b.docx', docx)]))
    assert len(writes) == 1


def test_shared_output_budget_stops_serialization_not_post_counting(adapter, monkeypatch):
    docx = package('<w:p x:val="1"/>' * 20, namespaces='xmlns:x="urn:' + 'n' * 1000 + '"')
    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', 4096)
    started = []
    completed = []
    original = ET.ElementTree.write

    def observed(tree, sink, *args, **kwargs):
        started.append(True)
        original(tree, sink, *args, **kwargs)
        completed.append(True)

    monkeypatch.setattr(ET.ElementTree, 'write', observed)
    with pytest.raises(ValueError, match='[Oo]utput.*limit'):
        adapter.extract_zip_sources(bundle([(f'{i}.docx', docx) for i in range(20)]))
    assert 0 < len(started) < 400
    assert len(completed) <= len(started)


def test_fragmented_xml_is_charged_by_size_not_write_count(adapter, monkeypatch):
    docx = package('<w:p>' + '<w:r><w:t>x</w:t></w:r>' * 2000 + '</w:p>')
    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', 1024 * 1024)
    result = adapter.extract_zip_sources(bundle([('a.docx', docx)]))
    assert len(json.dumps(result).encode()) < 1024 * 1024
    assert result['members'][0]['evidence']['blocks'][0]['text'] == 'x' * 2000


@pytest.mark.parametrize('nested', [False, True])
def test_count_limit_is_checked_before_zipfile_construction(adapter, monkeypatch, nested):
    source = import_module('src.parser.document_sources')
    content = package() if nested else bundle([('a', b''), ('b', b'')])
    target = source if nested else adapter
    monkeypatch.setattr(target, 'MAX_MEMBERS', 1)

    def forbidden(*args, **kwargs):
        pytest.fail('ZipFile constructed before bounded preflight')

    monkeypatch.setattr(target, 'ZipFile', forbidden)
    with pytest.raises(ValueError, match='count.*limit'):
        (source.extract_docx_blocks if nested else adapter.extract_zip_sources)(content)


@pytest.mark.parametrize('mutation', ['under_count', 'over_count', 'disk', 'zip64', 'size', 'offset', 'truncated'])
def test_preflight_rejects_inconsistent_directory_before_zipfile(adapter, monkeypatch, mutation):
    content = bytearray(bundle([('a', b''), ('b', b'')]))
    end = content.rindex(b'PK\x05\x06')
    if mutation in {'under_count', 'over_count', 'zip64'}:
        count = {'under_count': 1, 'over_count': 3, 'zip64': 65535}[mutation]
        struct.pack_into('<HH', content, end + 8, count, count)
    elif mutation == 'disk':
        struct.pack_into('<H', content, end + 4, 1)
    elif mutation == 'size':
        struct.pack_into('<I', content, end + 12, 1)
    elif mutation == 'offset':
        struct.pack_into('<I', content, end + 16, end + 100)
    else:
        content.pop()

    def forbidden(*args, **kwargs):
        pytest.fail('ZipFile constructed before directory validation')

    monkeypatch.setattr(adapter, 'ZipFile', forbidden)
    with pytest.raises(ValueError):
        adapter.extract_zip_sources(bytes(content))


def test_seekable_pages_enumerate_without_payload_reads_and_extract_with_continuation(adapter, tmp_path):
    assert hasattr(adapter, 'extract_zip_page'), 'Seekable paged ZIP API missing'
    entries = [('a.docx', package('<w:p/>')), ('b.doc', b'legacy'), ('c.docx', package('<w:p/>'))]
    path = tmp_path / 'source.zip'
    path.write_bytes(bundle(entries))
    first = adapter.extract_zip_page(path, page_size=2)
    assert [m['status'] for m in first['members']] == ['pending', 'unsupported']
    assert all('sha256' not in m for m in first['members'])
    assert first['crc_verified'] is False
    assert first['next_start'] == 2
    with path.open('rb') as stream:
        stream.seek(7)
        last = adapter.extract_zip_page(stream, start=2, page_size=2, extract=True)
        assert stream.tell() == 7
    assert last['next_start'] is None
    assert last['status'] == 'complete'
    assert last['members'][0]['status'] == 'extracted'
    assert last['crc_verified'] is True


def test_page_returns_completed_members_when_next_member_exceeds_budget(adapter, monkeypatch):
    assert hasattr(adapter, 'extract_zip_page'), 'Seekable paged ZIP API missing'
    content = bundle([('a.doc', b'abc'), ('b.doc', b'abc')])
    monkeypatch.setattr(adapter, 'MAX_PROCESSING_BYTES', 3)
    result = adapter.extract_zip_page(BytesIO(content), extract=True, page_size=2)
    assert [m['path'] for m in result['members']] == ['a.doc']
    assert result['next_start'] == 1
    assert result['status'] == 'processing_limit'
    monkeypatch.setattr(adapter, 'MAX_PROCESSING_BYTES', 2)
    blocked = adapter.extract_zip_page(BytesIO(content), start=1, extract=True)
    assert blocked['members'] == []
    assert blocked['next_start'] == 1
    assert blocked['status'] == 'member_limit'


def test_page_cap_is_independent_of_legacy_bytes_cap(adapter, monkeypatch):
    assert hasattr(adapter, 'extract_zip_page'), 'Seekable paged ZIP API missing'
    content = bundle([('a.doc', b'legacy')])
    monkeypatch.setattr(adapter, 'MAX_ARCHIVE_BYTES', 1)
    assert adapter.extract_zip_page(BytesIO(content))['status'] == 'complete'
    with pytest.raises(ValueError, match='limit'):
        adapter.extract_zip_sources(content)
    monkeypatch.setattr(adapter, 'MAX_SEEKABLE_ARCHIVE_BYTES', len(content) - 1)
    with pytest.raises(ValueError, match='limit'):
        adapter.extract_zip_page(BytesIO(content))


def test_nested_docx_preflight_runs_before_nested_zipfile(adapter, monkeypatch):
    source = import_module('src.parser.document_sources')
    content = bundle([('a.docx', package())])
    monkeypatch.setattr(source, 'MAX_MEMBERS', 1)
    original = adapter.ZipFile
    calls = []

    def observed(*args, **kwargs):
        calls.append(True)
        assert len(calls) == 1, 'Nested ZipFile constructed before preflight'
        return original(*args, **kwargs)

    monkeypatch.setattr(adapter, 'ZipFile', observed)
    with pytest.raises(ValueError, match='count.*limit'):
        adapter.extract_zip_sources(content)


def test_directory_byte_cap_and_actual_record_count_are_bounded(adapter, monkeypatch):
    content = bytearray(bundle([('a', b''), ('b', b'')]))
    end = content.rindex(b'PK\x05\x06')
    struct.pack_into('<HH', content, end + 8, 1, 1)
    monkeypatch.setattr(adapter, 'MAX_MEMBERS', 1)
    with pytest.raises(ValueError, match='count.*limit'):
        adapter.extract_zip_sources(bytes(content))
    monkeypatch.setattr(adapter, 'MAX_CENTRAL_DIRECTORY_BYTES', 16)
    with pytest.raises(ValueError, match='directory.*limit'):
        adapter.extract_zip_sources(bundle([('a', b'')]))


def test_enumeration_does_not_open_payload_but_extraction_checks_crc(adapter, monkeypatch):
    content = bytearray(bundle([('a.doc', b'unique-payload')]))
    content[content.index(b'unique-payload')] ^= 1
    enumerated = adapter.extract_zip_page(BytesIO(content))
    assert enumerated['members'][0]['status'] == 'unsupported'
    assert enumerated['crc_verified'] is False
    with pytest.raises(ValueError, match='ZIP'):
        adapter.extract_zip_page(BytesIO(content), extract=True)


def test_output_exhaustion_preserves_completed_page_members(adapter, monkeypatch):
    entries = [('a.doc', b'x'), ('b.docx', package('<w:p>' + '<w:r><w:t>x</w:t></w:r>' * 500 + '</w:p>'))]
    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', 4096)
    result = adapter.extract_zip_page(BytesIO(bundle(entries)), page_size=2, extract=True)
    assert [m['path'] for m in result['members']] == ['a.doc']
    assert result['status'] == 'output_limit'
    assert result['next_start'] == 1
    assert len(json.dumps(result).encode()) <= 4096
    blocked = adapter.extract_zip_page(BytesIO(bundle(entries)), start=1, extract=True)
    assert blocked['status'] == 'member_limit'
    assert blocked['next_start'] == 1


@pytest.mark.parametrize('kwargs', [{'start': -1}, {'start': True}, {'page_size': 0},
                                     {'page_size': 513}, {'extract': 'yes'}, {'start': 2}])
def test_invalid_page_arguments_are_rejected(adapter, kwargs):
    with pytest.raises((ValueError, TypeError)):
        adapter.extract_zip_page(BytesIO(bundle([('a', b'')])), **kwargs)


def test_zip_comments_empty_archive_and_binary_stream_contract(adapter):
    stream = BytesIO()
    with ZipFile(stream, 'w') as archive:
        archive.comment = b'ordinary comment'
    assert adapter.extract_zip_page(stream)['total_members'] == 0
    assert not stream.closed
    with pytest.raises(TypeError):
        adapter.extract_zip_page(b'not a seekable stream')


def test_nested_member_budget_is_shared(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_PROCESSING_MEMBERS', 5)
    docx = package('<w:p/>')
    with pytest.raises(ValueError, match='processing.*limit'):
        adapter.extract_zip_sources(bundle([('a.docx', docx), ('b.docx', docx)]))


def test_unsupported_compression_is_rejected(adapter):
    content = bytearray(bundle([('x', b'x')]))
    for signature, offset in ((b'PK\x03\x04', 8), (b'PK\x01\x02', 10)):
        position = content.index(signature) + offset
        struct.pack_into('<H', content, position, 99)
    with pytest.raises(ValueError, match='compression'):
        adapter.extract_zip_sources(bytes(content))


def test_report_metadata_is_bounded_without_docx(adapter, monkeypatch):
    monkeypatch.setattr(adapter, 'MAX_OUTPUT_BYTES', 5000)
    with pytest.raises(ValueError, match='[Oo]utput.*limit'):
        adapter.extract_zip_sources(bundle([('長' * 200 + str(i), b'') for i in range(8)]))

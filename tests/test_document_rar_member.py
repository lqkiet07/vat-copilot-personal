import base64
import ctypes
import os
from pathlib import Path
import hashlib
import json
import stat

import pytest

from src.parser import document_rar as rar
from tests.test_document_worker import ArchiveFunction, ArchiveLibrary, docker, rar_source, worker
import ctypes.util



def member_payload(source, content=b'hello'):
    entry = dict(index=1, path='../same', type='file', size=len(content), encrypted='no', safe=False)
    return dict(job='rar_member', source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                format='rar4', encrypted='no', tools={'libarchive': 'libarchive 3.6.2'},
                entries=[dict(index=0, path='folder/', type='directory', size=0, encrypted='no', safe=True), entry],
                member_index=1, member_sha256=hashlib.sha256(content).hexdigest(),
                member_base64=base64.b64encode(content).decode('ascii'))


def test_member_returns_bytes_and_identity(worker, rar_source, monkeypatch):
    data = member_payload(rar_source)
    calls, _ = docker(monkeypatch, worker, rar_source, output=json.dumps(data).encode())
    result = worker.run_document_job(rar_source, job='rar_member', member_index=1)
    assert result['member_bytes'] == b'hello'
    assert result['entries'] == data['entries']
    assert result['member_sha256'] == hashlib.sha256(b'hello').hexdigest()
    assert calls[1][-2:] == ['rar_member', '1']
    assert calls[1].count('--mount') == 1
    assert all(flag in calls[1] for flag in ['--network=none', '--read-only', '--user=10001:10001'])
    assert result['image_id'].startswith('sha256:')


@pytest.mark.parametrize('index', [None, True, -1, 512, '1', 1.0])
def test_member_invalid_index_before_process(worker, rar_source, monkeypatch, index):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('process invoked'))
    with pytest.raises(ValueError):
        worker.run_document_job(rar_source, job='rar_member', member_index=index)


@pytest.mark.parametrize('job,page,index', [('rar_list', 1, 0), ('pdf_text_page', 1, 0),
                                         ('ocr_page', 1, 0), ('legacy_doc_to_docx', 1, 0), ('rar_member', 2, 0)])
def test_member_parameter_is_not_page(worker, rar_source, monkeypatch, job, page, index):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('process invoked'))
    with pytest.raises(ValueError):
        worker.run_document_job(rar_source, job=job, page=page, member_index=index)


@pytest.mark.parametrize('failure', ['base64', 'hash', 'size', 'directory', 'encrypted', 'index',
                                    'source', 'tools', 'extra', 'duplicate', 'oversize', 'malformed'])
def test_member_rejects_protocol(worker, rar_source, monkeypatch, failure):
    data = member_payload(rar_source)
    if failure == 'base64':
        data['member_base64'] = '%%%'
    elif failure == 'hash':
        data['member_sha256'] = '0' * 64
    elif failure == 'size':
        data['entries'][1]['size'] += 1
    elif failure == 'directory':
        data['entries'][1]['type'] = 'directory'
    elif failure == 'encrypted':
        data['encrypted'] = 'yes'
        data['entries'][1]['encrypted'] = 'yes'
    elif failure == 'index':
        data['member_index'] = True
    elif failure == 'source':
        data['source_sha256'] = '0' * 64
    elif failure == 'tools':
        data['tools'] = {}
    elif failure == 'extra':
        data['page'] = 1
    elif failure == 'oversize':
        data = member_payload(rar_source, b'x' * (2 * 1024 * 1024 + 1))
    output = json.dumps(data).encode()
    if failure == 'duplicate':
        output = output[:-1] + b',"member_index":1}'
    elif failure == 'malformed':
        output = b'{'
    docker(monkeypatch, worker, rar_source, output=output)
    with pytest.raises(ValueError):
        worker.run_document_job(rar_source, job='rar_member', member_index=1)


class MemberLibrary(ArchiveLibrary):
    def __init__(self, failure=None, size=12):
        super().__init__([b'same', b'same'], failure=failure)
        self.reads, self.skips, self.offset = [], [], 0
        self.content = b'hello world!'
        self.archive_read_data = ArchiveFunction(self.read_data)
        self.archive_read_data_skip = ArchiveFunction(self.skip)
        self.archive_entry_size = ArchiveFunction(lambda e: size)
        self.archive_entry_size_is_set = ArchiveFunction(lambda e: failure != 'unknown_size')
        if failure == 'directory':
            self.filetype = stat.S_IFDIR
        if failure == 'link':
            self.filetype = stat.S_IFLNK

    def skip(self, archive):
        self.skips.append(self.position)
        return -30 if self.failure == 'skip' else 0

    def read_data(self, archive, buffer, size):
        self.reads.append(self.position)
        assert size <= 65536
        if self.failure in ('crc', 'unsupported') and self.offset:
            return -30
        content = self.content[:-1] if self.failure == 'truncated' else self.content
        chunk = content[self.offset:self.offset + size]
        ctypes.memmove(buffer, chunk, len(chunk))
        self.offset += len(chunk)
        return len(chunk)


def invoke(monkeypatch, source, library, index=1):
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: 'archive')
    monkeypatch.setattr(ctypes, 'CDLL', lambda name: library)
    assert callable(getattr(rar, 'read_member', None)), 'bounded member reader missing'
    return rar.read_member(source, index)


def test_inner_reads_only_numeric_target_and_frees(monkeypatch, rar_source):
    library = MemberLibrary()
    data = invoke(monkeypatch, rar_source, library)
    assert base64.b64decode(data['member_base64']) == b'hello world!'
    assert library.skips == [0] and set(library.reads) == {1}
    assert library.closed and library.freed
    assert library.archive_read_data.restype is ctypes.c_ssize_t
    assert library.archive_read_data.argtypes == [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]


@pytest.mark.parametrize('failure', ['directory', 'link', 'hardlink', 'encrypted', 'unknown_size',
                                    'truncated', 'crc', 'unsupported', 'skip', 'header', 'close', 'free'])
def test_inner_member_fails_without_partial_result(monkeypatch, rar_source, failure):
    library = MemberLibrary(failure)
    with pytest.raises(ValueError):
        invoke(monkeypatch, rar_source, library)
    assert library.closed and library.freed


@pytest.mark.parametrize('size', [-1, 2 * 1024 * 1024 + 1])
def test_inner_rejects_size_before_read(monkeypatch, rar_source, size):
    library = MemberLibrary(size=size)
    with pytest.raises(ValueError):
        invoke(monkeypatch, rar_source, library)
    assert not library.reads and library.closed and library.freed


@pytest.mark.parametrize('size', [0, 2 * 1024 * 1024])
def test_member_accepts_exact_payload_bounds(worker, rar_source, monkeypatch, size):
    content = b'x' * size
    docker(monkeypatch, worker, rar_source, output=json.dumps(member_payload(rar_source, content)).encode())
    result = worker.run_document_job(rar_source, job='rar_member', member_index=1)
    assert result['member_bytes'] == content


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'stdout', 'stderr'])
def test_member_process_failures_cleanup(worker, rar_source, monkeypatch, failure):
    options = {'output': json.dumps(member_payload(rar_source)).encode()}
    if failure == 'exit':
        options['code'] = 1
    elif failure == 'timeout':
        monkeypatch.setattr(worker, 'PROCESS_TIMEOUT', 0.01)
        options['timeout'] = True
    else:
        options['output' if failure == 'stdout' else 'stderr'] = b'x' * (4 * 1024 * 1024 + 1)
    calls, processes = docker(monkeypatch, worker, rar_source, **options)
    with pytest.raises((ValueError, TimeoutError)):
        worker.run_document_job(rar_source, job='rar_member', member_index=1)
    assert calls[-1][1:3] == ['rm', '--force'] and processes[1].killed


def test_inner_traversal_budget(monkeypatch, rar_source):
    library = MemberLibrary(size=64 * 1024 * 1024)
    with pytest.raises(ValueError):
        invoke(monkeypatch, rar_source, library)
    assert not library.reads and library.closed and library.freed


def test_inner_payload_exceeds_declared_size(monkeypatch, rar_source):
    library = MemberLibrary(size=11)
    with pytest.raises(ValueError):
        invoke(monkeypatch, rar_source, library)
    assert library.closed and library.freed


@pytest.mark.skipif(os.getenv('DOCUMENT_WORKER_SMOKE') != '1', reason='Optional actual Docker smoke')
def test_actual_four_rar_member_smoke(worker):
    candidates = sorted((Path(__file__).resolve().parents[1] / 'data/raw').glob('*.rar'))
    assert len(candidates) == 4
    for source in candidates:
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        metadata = worker.run_document_job(source, job='rar_list')
        entry = next(e for e in metadata['entries'] if e['type'] == 'file'
                     and e['encrypted'] == 'no' and 0 < e['size'] <= 2 * 1024 * 1024)
        result = worker.run_document_job(source, job='rar_member', member_index=entry['index'])
        assert result['entries'][entry['index']] == entry
        assert result['source_sha256'] == before == hashlib.sha256(source.read_bytes()).hexdigest()
        assert len(result['member_bytes']) == entry['size']
        assert hashlib.sha256(result['member_bytes']).hexdigest() == result['member_sha256']
        print(json.dumps(dict(source_sha256=before, member_index=entry['index'], size=entry['size'],
                              member_sha256=result['member_sha256'], image_id=result['image_id'],
                              tools=result['tools'], unchanged=True)))


def test_rar5_unsupported_global_encryption_preserves_explicit_entry_status(worker, rar_source, monkeypatch):
    data = member_payload(rar_source)
    data['encrypted'] = 'unsupported'
    docker(monkeypatch, worker, rar_source, output=json.dumps(data).encode())
    assert worker.run_document_job(rar_source, job='rar_member', member_index=1)['encrypted'] == 'unsupported'


def test_inner_unsupported_global_encryption_reads_explicit_clear_entry(monkeypatch, rar_source):
    library = MemberLibrary()
    library.archive_read_has_encrypted_entries = ArchiveFunction(lambda a: -2)
    data = invoke(monkeypatch, rar_source, library)
    assert data['encrypted'] == 'unsupported'
    assert base64.b64decode(data['member_base64']) == library.content


def chunk_payload(source, content, offset=0):
    data = member_payload(source, content)
    chunk = content[offset:offset + 512 * 1024]
    data.update(job='rar_member_chunk', offset=offset,
                chunk_sha256=hashlib.sha256(chunk).hexdigest(),
                member_base64=base64.b64encode(chunk).decode('ascii'))
    return data


def test_chunked_reader_streams_large_member_and_hashes_whole(monkeypatch, rar_source):
    content = b'a' * (2 * 1024 * 1024 + 17)
    library = MemberLibrary(size=len(content))
    library.content = content
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: 'archive')
    monkeypatch.setattr(ctypes, 'CDLL', lambda name: library)
    assert callable(getattr(rar, 'read_member_chunk', None)), 'chunk reader missing'
    data = rar.read_member_chunk(rar_source, 1, 512 * 1024)
    assert base64.b64decode(data['member_base64']) == content[512 * 1024:1024 * 1024]
    assert data['member_sha256'] == hashlib.sha256(content).hexdigest()
    assert data['chunk_sha256'] == hashlib.sha256(content[512 * 1024:1024 * 1024]).hexdigest()
    assert len(rar._encode(data)) + 1 <= 2 * 1024 * 1024
    assert library.closed and library.freed


@pytest.mark.parametrize('size', [16 * 1024 * 1024, 16 * 1024 * 1024 + 1])
def test_chunk_reader_ceiling_before_payload_allocation(monkeypatch, rar_source, size):
    library = MemberLibrary(size=size)
    library.content = b'x' * min(size, 16 * 1024 * 1024)
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: 'archive')
    monkeypatch.setattr(ctypes, 'CDLL', lambda name: library)
    assert callable(getattr(rar, 'read_member_chunk', None)), 'chunk reader missing'
    if size > 16 * 1024 * 1024:
        with pytest.raises(ValueError):
            rar.read_member_chunk(rar_source, 1, 0)
        assert not library.reads
    else:
        data = rar.read_member_chunk(rar_source, 1, size - 512 * 1024)
        assert len(base64.b64decode(data['member_base64'])) == 512 * 1024
    assert library.closed and library.freed


@pytest.mark.parametrize('offset', [True, -1, '0', 1.5, 16 * 1024 * 1024])
def test_chunk_rejects_bad_offset_before_process(worker, rar_source, monkeypatch, offset):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('process invoked'))
    assert callable(getattr(worker, 'read_rar_member', None)), 'public large member helper missing'
    with pytest.raises(ValueError):
        worker.run_document_job(rar_source, job='rar_member_chunk', member_index=1, offset=offset)


def test_public_large_member_helper_reassembles_identity(worker, rar_source, monkeypatch):
    content = b'x' * (2 * 1024 * 1024 + 31)
    calls = []
    def capture(args, **kwargs):
        calls.append((args, kwargs))
        if args[1:3] == ['image', 'inspect']:
            return b'sha256:' + b'a' * 64
        assert args[-3:-1] == ['rar_member_chunk', '1']
        assert kwargs['limit'] == 2 * 1024 * 1024
        assert args.count('--mount') == 1 and args[-1].isdecimal()
        return json.dumps(chunk_payload(rar_source, content, int(args[-1]))).encode()
    monkeypatch.setattr(worker, '_capture', capture)
    assert callable(getattr(worker, 'read_rar_member', None)), 'public large member helper missing'
    result = worker.read_rar_member(rar_source, 1)
    assert result['member_bytes'] == content
    assert len(result['chunks']) == 5
    assert result['member_sha256'] == hashlib.sha256(content).hexdigest()
    assert all('member_base64' not in c and 'member_bytes' not in c for c in result['chunks'])
    assert all('--read-only' in a and '--network=none' in a for a, k in calls if a[1] == 'run')


@pytest.mark.parametrize('failure', ['offset', 'index', 'source', 'hash', 'chunk_hash', 'size',
                                    'tools', 'duplicate', 'oversize', 'truncated', 'extra', 'encrypted'])
def test_chunk_validator_rejects_untrusted_protocol(rar_source, failure):
    data = chunk_payload(rar_source, b'hello')
    if failure == 'offset':
        data['offset'] = True
    elif failure == 'index':
        data['member_index'] = True
    elif failure == 'source':
        data['source_sha256'] = '0' * 64
    elif failure == 'hash':
        data['member_sha256'] = 'bad'
    elif failure == 'chunk_hash':
        data['chunk_sha256'] = '0' * 64
    elif failure == 'size':
        data['entries'][1]['size'] = 16 * 1024 * 1024 + 1
    elif failure == 'tools':
        data['tools'] = {}
    elif failure == 'truncated':
        data['member_base64'] = ''
    elif failure == 'extra':
        data['extra'] = 1
    elif failure == 'encrypted':
        data['entries'][1]['encrypted'] = 'yes'
    output = json.dumps(data).encode()
    if failure == 'duplicate':
        output = output[:-1] + b',"offset":0}'
    elif failure == 'oversize':
        output = b' ' * (2 * 1024 * 1024 + 1)
    assert callable(getattr(rar, 'validate_chunk_result', None)), 'chunk validator missing'
    with pytest.raises(ValueError):
        rar.validate_chunk_result(output, hashlib.sha256(rar_source.read_bytes()).hexdigest(),
                                  'rar4', 'sha256:' + 'a' * 64, 1, 0)


@pytest.mark.parametrize('failure', ['member_sha256', 'entries', 'image_id', 'tools', 'source_sha256'])
def test_large_helper_rejects_cross_chunk_identity(worker, rar_source, monkeypatch, failure):
    content = b'x' * (512 * 1024 + 1)
    count = 0
    def job(source, **kwargs):
        nonlocal count
        offset = kwargs['offset']
        data = chunk_payload(rar_source, content, offset)
        data.update(member_bytes=content[offset:offset + 512 * 1024], image_id='sha256:' + 'a' * 64)
        if count:
            if failure == 'entries':
                data['entries'][1]['path'] = 'different'
            elif failure == 'tools':
                data['tools'] = {'libarchive': 'changed'}
            else:
                data[failure] = '0' * 64
        count += 1
        return data
    monkeypatch.setattr(worker, 'run_document_job', job)
    assert callable(getattr(worker, 'read_rar_member', None)), 'public large member helper missing'
    with pytest.raises(ValueError):
        worker.read_rar_member(rar_source, 1)


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'stdout', 'stderr'])
def test_chunk_process_failures_cleanup(worker, rar_source, monkeypatch, failure):
    options = {'output': json.dumps(chunk_payload(rar_source, b'hello')).encode()}
    if failure == 'exit':
        options['code'] = 1
    elif failure == 'timeout':
        monkeypatch.setattr(worker, 'PROCESS_TIMEOUT', 0.01)
        options['timeout'] = True
    else:
        options['output' if failure == 'stdout' else 'stderr'] = b'x' * (2 * 1024 * 1024 + 1)
    calls, processes = docker(monkeypatch, worker, rar_source, **options)
    with pytest.raises((ValueError, TimeoutError)):
        worker.run_document_job(rar_source, job='rar_member_chunk', member_index=1, offset=0)
    assert calls[-1][1:3] == ['rm', '--force'] and processes[1].killed


@pytest.mark.parametrize('failure', ['truncated', 'crc', 'close', 'free', 'encrypted', 'link'])
def test_chunk_reader_failure_frees_without_result(monkeypatch, rar_source, failure):
    library = MemberLibrary(failure)
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: 'archive')
    monkeypatch.setattr(ctypes, 'CDLL', lambda name: library)
    with pytest.raises(ValueError):
        rar.read_member_chunk(rar_source, 1, 0)
    assert library.closed and library.freed


def test_inner_missing_index(monkeypatch, rar_source):
    library = MemberLibrary()
    with pytest.raises(ValueError):
        invoke(monkeypatch, rar_source, library, 3)
    assert not library.reads and library.closed and library.freed

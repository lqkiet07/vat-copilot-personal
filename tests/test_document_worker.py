from importlib import import_module, util
from io import BytesIO
import base64
import hashlib
import os
from pathlib import Path
import sys
from zipfile import ZipFile
import xml.etree.ElementTree as ET
import json
import subprocess
import stat
from types import SimpleNamespace

import pytest


IMAGE_ID = 'sha256:' + 'a' * 64


def test_public_worker_exists():
    assert util.find_spec('src.parser.document_worker') is not None, 'Document worker runner missing'


@pytest.fixture
def worker():
    return import_module('src.parser.document_worker')


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'hồ sơ.pdf'
    path.write_bytes(b'%PDF-test')
    return path


def payload(source, job='pdf_text_page', page=1):
    tools = {'pdftotext': 'pdftotext version 22.12.0'}
    if job == 'ocr_page':
        tools = {'pdftoppm': 'pdftoppm version 22.12.0', 'tesseract': 'tesseract 5.3.0'}
    return json.dumps({'text': 'Điều 1', 'tools': tools, 'job': job, 'page': page,
                       'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}).encode()


class Process:
    def __init__(self, stdout=b'', stderr=b'', code=0, timeout=False):
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)
        self.returncode = code
        self.timeout = timeout
        self.killed = False

    def wait(self, timeout=None):
        if self.timeout and not self.killed:
            raise subprocess.TimeoutExpired('docker', timeout)
        return self.returncode

    def kill(self):
        self.killed = True

    def communicate(self, *args, **kwargs):
        pytest.fail('Unbounded communicate is forbidden')


def docker(monkeypatch, worker, source, *, output=None, stderr=b'', code=0, timeout=False, image=IMAGE_ID):
    calls = []
    processes = []

    def popen(args, **kwargs):
        assert kwargs == {'stdin': subprocess.DEVNULL, 'stdout': subprocess.PIPE,
                          'stderr': subprocess.PIPE, 'shell': False}
        calls.append(args)
        if args[1:3] == ['image', 'inspect']:
            process = Process((image + '\n').encode())
        elif args[1] == 'run':
            process = Process(payload(source) if output is None else output, stderr, code, timeout)
        else:
            assert args[1:3] == ['rm', '--force']
            process = Process()
        processes.append(process)
        return process

    monkeypatch.setattr(worker.subprocess, 'Popen', popen)
    return calls, processes


@pytest.mark.parametrize('job,page', [('pdf_text_page', 1), ('ocr_page', 2)])
def test_runs_only_pinned_image_with_single_readonly_file_and_provenance(worker, source, monkeypatch, job, page):
    calls, _ = docker(monkeypatch, worker, source, output=payload(source, job, page))
    result = worker.run_document_job(source, job=job, page=page)
    assert calls[0] == ['docker', 'image', 'inspect', '--format', '{{.Id}}', 'vat-document-worker:local']
    args = calls[1]
    for flag in ['--network=none', '--read-only', '--user=10001:10001', '--cap-drop=ALL',
                 '--security-opt=no-new-privileges', '--cpus=2', '--memory=2g',
                 '--memory-swap=2g', '--pids-limit=128', '--pull=never']:
        assert flag in args
    assert args[args.index('--tmpfs') + 1] == '/tmp:rw,noexec,nosuid,nodev,size=512m,mode=1777'
    assert args.count('--mount') == 1
    assert args[args.index('--mount') + 1] == f'type=bind,source={source.resolve()},target=/input/source,readonly'
    assert not any(arg in args for arg in ['-v', '--volume', '--privileged', '--env-file'])
    assert 'docker.sock' not in ' '.join(args)
    assert args[args.index(IMAGE_ID) + 1:][:2] == ['python3', '-c']
    assert args[-2:] == [job, str(page)]
    assert result['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert result['image_id'] == IMAGE_ID
    assert result['page'] == page and result['job'] == job
    assert result['review_required'] is True
    assert result['completeness'] == 'incomplete'
    assert result['text'] == 'Điều 1' and result['tools']
    assert source.read_bytes() == b'%PDF-test'


@pytest.mark.parametrize('page', [True, False, 0, -1, 10001, '1', 1.0, None])
def test_rejects_invalid_page_before_docker(worker, source, monkeypatch, page):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('Docker invoked'))
    with pytest.raises(ValueError, match='page'):
        worker.run_document_job(source, job='ocr_page', page=page)


@pytest.mark.parametrize('job', ['legacy_doc', 'rar_listing', 'sh', 'ocr_page; id', None])
def test_explicitly_rejects_unsupported_jobs(worker, source, monkeypatch, job):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('Docker invoked'))
    with pytest.raises(ValueError, match='Unsupported'):
        worker.run_document_job(source, job=job, page=1)


@pytest.mark.parametrize('kind', ['missing', 'directory', 'large', 'comma', 'empty'])
def test_rejects_unsafe_input_before_docker(worker, source, tmp_path, monkeypatch, kind):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('Docker invoked'))
    path = source
    if kind == 'missing':
        path = tmp_path / 'missing.pdf'
    elif kind == 'directory':
        path = tmp_path
    elif kind == 'large':
        monkeypatch.setattr(worker, 'MAX_INPUT_BYTES', 2)
    elif kind == 'comma':
        path = tmp_path / 'a,b.pdf'
        path.write_bytes(b'pdf')
    else:
        path.write_bytes(b'')
    with pytest.raises(ValueError, match='[Ss]ource|[Ii]nput'):
        worker.run_document_job(path, job='pdf_text_page', page=1)


@pytest.mark.parametrize('image', ['vat-document-worker:local', 'sha256:123', 'sha256:' + 'g' * 64])
def test_rejects_nonimmutable_image_inspection(worker, source, monkeypatch, image):
    calls, _ = docker(monkeypatch, worker, source, image=image)
    with pytest.raises(ValueError, match='image'):
        worker.run_document_job(source, job='pdf_text_page', page=1)
    assert len(calls) == 1


@pytest.mark.parametrize('stream', ['stdout', 'stderr'])
def test_cancels_named_container_when_output_limit_exceeded(worker, source, monkeypatch, stream):
    monkeypatch.setattr(worker, 'MAX_OUTPUT_BYTES', 512)
    options = {'output': b'x' * 513} if stream == 'stdout' else {'stderr': b'x' * 513}
    calls, processes = docker(monkeypatch, worker, source, **options)
    with pytest.raises(ValueError, match='[Oo]utput.*limit'):
        worker.run_document_job(source, job='pdf_text_page', page=1)
    name = calls[1][calls[1].index('--name') + 1]
    assert name.startswith('vat-document-') and len(name.removeprefix('vat-document-')) == 32
    assert calls[2] == ['docker', 'rm', '--force', name]
    assert processes[1].killed


def test_timeout_removes_named_container_and_kills_host_process(worker, source, monkeypatch):
    monkeypatch.setattr(worker, 'PROCESS_TIMEOUT', 0.01)
    calls, processes = docker(monkeypatch, worker, source, timeout=True)
    with pytest.raises(TimeoutError):
        worker.run_document_job(source, job='pdf_text_page', page=1)
    assert calls[2] == ['docker', 'rm', '--force', calls[1][calls[1].index('--name') + 1]]
    assert processes[1].killed


@pytest.mark.parametrize('output', [b'not json', b'\xff', b'[]', b'{}', b'{"text": 1}',
                                   b'{"text":"x","tools":{},"job":"pdf_text_page","page":true}'])
def test_rejects_malformed_output(worker, source, monkeypatch, output):
    docker(monkeypatch, worker, source, output=output)
    with pytest.raises(ValueError, match='output'):
        worker.run_document_job(source, job='pdf_text_page', page=1)


@pytest.mark.parametrize('field,value', [('page', 2), ('job', 'ocr_page'), ('source_sha256', '0' * 64),
                                        ('tools', {'pdftotext': ''}), ('text', 7)])
def test_rejects_wrong_output_provenance(worker, source, monkeypatch, field, value):
    data = json.loads(payload(source))
    data[field] = value
    docker(monkeypatch, worker, source, output=json.dumps(data).encode())
    with pytest.raises(ValueError, match='output'):
        worker.run_document_job(source, job='pdf_text_page', page=1)


def test_rejects_special_file_before_open(worker, source, monkeypatch):
    monkeypatch.setattr(worker.Path, 'stat', lambda *a, **k: SimpleNamespace(st_mode=stat.S_IFIFO, st_size=1))
    monkeypatch.setattr(worker.Path, 'open', lambda *a, **k: pytest.fail('Special file opened'))
    with pytest.raises(ValueError, match='Source'):
        worker.run_document_job(source, job='pdf_text_page', page=1)


def test_output_cap_cancels_without_waiting_for_eof(worker, source, monkeypatch):
    monkeypatch.setattr(worker, 'MAX_OUTPUT_BYTES', 512)
    calls, processes = docker(monkeypatch, worker, source, output=b'x' * 513)
    original = worker.subprocess.Popen

    class Endless:
        def read(self, size):
            assert size <= 8192
            return b'x' * size

        def close(self):
            pass

    def popen(args, **kwargs):
        process = original(args, **kwargs)
        if args[1] == 'run':
            process.stdout = Endless()
            process.timeout = True
        return process

    monkeypatch.setattr(worker.subprocess, 'Popen', popen)
    with pytest.raises(ValueError, match='Output.*limit'):
        worker.run_document_job(source, job='pdf_text_page', page=1)
    assert calls[-1][1:3] == ['rm', '--force']
    assert processes[1].killed


def test_source_change_during_job_is_rejected(worker, source, monkeypatch):
    docker(monkeypatch, worker, source)
    original = worker.subprocess.Popen

    def popen(args, **kwargs):
        process = original(args, **kwargs)
        if args[1] == 'run':
            source.write_bytes(b'changed')
        return process

    monkeypatch.setattr(worker.subprocess, 'Popen', popen)
    with pytest.raises(ValueError, match='Source changed'):
        worker.run_document_job(source, job='pdf_text_page', page=1)


def test_cleanup_failure_still_reaps_host_process(worker, source, monkeypatch):
    monkeypatch.setattr(worker, 'PROCESS_TIMEOUT', 0.01)
    calls, processes = docker(monkeypatch, worker, source, timeout=True)
    original = worker.subprocess.Popen
    reaped = []

    def popen(args, **kwargs):
        if args[1] == 'rm':
            raise OSError('Docker unavailable during cleanup')
        process = original(args, **kwargs)
        if args[1] == 'run':
            wait = process.wait

            def tracked_wait(timeout=None):
                if process.killed:
                    reaped.append(True)
                return wait(timeout)

            process.wait = tracked_wait
        return process

    monkeypatch.setattr(worker.subprocess, 'Popen', popen)
    with pytest.raises(OSError, match='cleanup'):
        worker.run_document_job(source, job='pdf_text_page', page=1)
    assert processes[1].killed and reaped


def test_nonzero_exit_is_not_evidence(worker, source, monkeypatch):
    docker(monkeypatch, worker, source, code=1, stderr=b'parser failed')
    with pytest.raises(ValueError, match='failed'):
        worker.run_document_job(source, job='pdf_text_page', page=1)


@pytest.fixture
def legacy_source(tmp_path):
    path = tmp_path / 'untrusted name.doc'
    path.write_bytes(bytes.fromhex('d0cf11e0a1b11ae1') + b'bounded-test')
    return path


def docx_bytes():
    stream = BytesIO()
    with ZipFile(stream, 'w') as archive:
        archive.writestr('[Content_Types].xml',
                         '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                         '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
        archive.writestr('word/document.xml',
                         '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                         '<w:body><w:p><w:r><w:t>Evidence</w:t></w:r></w:p></w:body></w:document>')
    return stream.getvalue()


def legacy_payload(source, content=None):
    content = docx_bytes() if content is None else content
    return dict(job='legacy_doc_to_docx', page=1,
                source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                converted_sha256=hashlib.sha256(content).hexdigest(),
                docx_base64=base64.b64encode(content).decode('ascii'),
                tools={'libreoffice': 'LibreOffice 7.4.7.2'})


def test_legacy_returns_bounded_bytes_and_provenance_without_host_output(worker, legacy_source, monkeypatch):
    before = set(legacy_source.parent.iterdir())
    calls, _ = docker(monkeypatch, worker, legacy_source,
                      output=json.dumps(legacy_payload(legacy_source)).encode())
    result = worker.run_document_job(legacy_source, job='legacy_doc_to_docx')
    args = calls[1]
    assert args.count('--mount') == 1 and args.count('--tmpfs') == 1
    assert args[args.index('--mount') + 1].endswith('target=/input/source,readonly')
    assert all(flag in args for flag in ['--network=none', '--read-only', '--cap-drop=ALL',
                                        '--security-opt=no-new-privileges', '--pull=never'])
    assert args[args.index(IMAGE_ID) + 1:][:2] == ['python3', '-c']
    assert set(legacy_source.parent.iterdir()) == before
    assert result['docx_bytes'] == docx_bytes()
    assert result['converted_sha256'] == hashlib.sha256(docx_bytes()).hexdigest()
    assert result['source_sha256'] == legacy_payload(legacy_source)['source_sha256']
    assert result['image_id'] == IMAGE_ID and result['tools']['libreoffice']
    assert result['review_required'] is True and result['completeness'] == 'incomplete'


@pytest.mark.parametrize('kind', ['executable', 'renamed', 'oversized', 'page'])
def test_legacy_input_is_rejected_before_docker(worker, legacy_source, monkeypatch, kind):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('Docker invoked'))
    page = 1
    if kind == 'executable':
        legacy_source.write_bytes(b'MZ executable')
    elif kind == 'renamed':
        legacy_source = legacy_source.rename(legacy_source.with_suffix('.exe'))
    elif kind == 'oversized':
        with legacy_source.open('r+b') as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    else:
        page = 2
    with pytest.raises(ValueError, match='[Ss]ource|[Ii]nput|page'):
        worker.run_document_job(legacy_source, job='legacy_doc_to_docx', page=page)


@pytest.mark.parametrize('kind', ['base64', 'encoded_limit', 'decoded_limit', 'corrupt', 'not_docx',
                                  'hash', 'tools', 'traversal', 'bomb'])
def test_legacy_rejects_untrusted_conversion_evidence(worker, legacy_source, monkeypatch, kind):
    content = docx_bytes()
    if kind in ('traversal', 'bomb'):
        stream = BytesIO(content)
        with ZipFile(stream, 'a') as archive:
            archive.writestr('../escape' if kind == 'traversal' else 'large.xml',
                             b'x' if kind == 'traversal' else b'x' * (16 * 1024 * 1024 + 1),
                             compress_type=8)
        content = stream.getvalue()
    elif kind == 'corrupt':
        content = b'not ZIP'
    elif kind == 'not_docx':
        stream = BytesIO()
        with ZipFile(stream, 'w') as archive:
            archive.writestr('other', 'not DOCX')
        content = stream.getvalue()
    elif kind == 'decoded_limit':
        content = b'x' * (2 * 1024 * 1024 + 1)
    data = legacy_payload(legacy_source, content)
    if kind == 'base64':
        data['docx_base64'] = '%%%'
    elif kind == 'encoded_limit':
        data['docx_base64'] = 'A' * (4 * 1024 * 1024 + 1)
    elif kind == 'hash':
        data['converted_sha256'] = '0' * 64
    elif kind == 'tools':
        data['tools'] = {'libreoffice': ''}
    docker(monkeypatch, worker, legacy_source, output=json.dumps(data).encode())
    with pytest.raises(ValueError):
        worker.run_document_job(legacy_source, job='legacy_doc_to_docx')


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'stdout', 'stderr'])
def test_legacy_process_failures_remove_named_container(worker, legacy_source, monkeypatch, failure):
    options = {'output': json.dumps(legacy_payload(legacy_source)).encode()}
    if failure == 'exit':
        options['code'] = 1
    elif failure == 'timeout':
        monkeypatch.setattr(worker, 'PROCESS_TIMEOUT', 0.01)
        options['timeout'] = True
    else:
        options['output' if failure == 'stdout' else 'stderr'] = b'x' * (4 * 1024 * 1024 + 1)
    calls, processes = docker(monkeypatch, worker, legacy_source, **options)
    with pytest.raises((ValueError, TimeoutError)):
        worker.run_document_job(legacy_source, job='legacy_doc_to_docx')
    assert calls[-1] == ['docker', 'rm', '--force', calls[1][calls[1].index('--name') + 1]]
    assert processes[1].killed


def test_legacy_inner_worker_uses_isolated_locked_profile_and_fixed_command(worker, legacy_source, tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(sys, 'argv', ['worker', 'legacy_doc_to_docx', '1'])
    monkeypatch.setitem(sys.modules, 'resource', SimpleNamespace(RLIMIT_FSIZE=1, setrlimit=lambda *a: None))

    def run(args, **kwargs):
        calls.append(args)
        assert kwargs['stdin'] == subprocess.DEVNULL and kwargs['check'] is True
        assert 0 < kwargs['timeout'] <= 90 and not kwargs.get('shell', False)
        if '--version' in args:
            kwargs['stdout'].write(b'LibreOffice 7.4.7.2\n')
            return
        profile_arg = next(arg for arg in args if arg.startswith('-env:UserInstallation='))
        assert profile_arg == '-env:UserInstallation=' + (tmp_path / 'profile').as_uri()
        registry = ET.parse(tmp_path / 'profile/user/registrymodifications.xcu')
        ns = '{http://openoffice.org/2001/registry}'
        settings = {(item.get(ns + 'path'), prop.get(ns + 'name')): prop.findtext('value')
                    for item in registry.getroot() for prop in item}
        security = '/org.openoffice.Office.Common/Security/Scripting'
        assert settings[(security, 'MacroSecurityLevel')] == '3'
        assert settings[(security, 'DisableMacrosExecution')] == 'true'
        assert settings[(security, 'BlockUntrustedRefererLinks')] == 'true'
        assert settings[('/org.openoffice.Office.Writer/Content/Update', 'Link')] == '2'
        assert settings[('/org.openoffice.Office.Writer/Filter/Import/VBA', 'Executable')] == 'false'
        assert settings[('/org.openoffice.Office.Writer/GlobalTemplateList', 'Paths')] in ('', None)
        assert '--headless' in args and '--norestore' in args and '--nodefault' in args
        assert Path(args[-1]) == tmp_path / 'document.doc'
        assert '--infilter=MS Word 97' in args
        assert args[args.index('--convert-to') + 1] == 'docx:Office Open XML Text'
        assert (tmp_path / 'document.doc').read_bytes() == legacy_source.read_bytes()
        (tmp_path / 'document.docx').write_bytes(docx_bytes())

    monkeypatch.setattr(subprocess, 'run', run)
    script = worker._LEGACY_WORKER.replace('/tmp', tmp_path.as_posix()).replace('/input/source', legacy_source.as_posix())
    exec(compile(script, '<legacy-worker>', 'exec'), {})
    data = json.loads(capsys.readouterr().out)
    assert base64.b64decode(data['docx_base64'], validate=True) == docx_bytes()
    assert data['converted_sha256'] == hashlib.sha256(docx_bytes()).hexdigest()
    assert len(calls) == 2


@pytest.mark.skipif(os.getenv('DOCUMENT_WORKER_SMOKE') != '1', reason='Optional actual Docker smoke')
def test_actual_legacy_zip_member_smoke(worker, tmp_path):
    root = Path(__file__).resolve().parents[1]
    candidates = sorted((root / 'data/raw').glob('*.zip'))
    for path in candidates:
        with ZipFile(path) as archive:
            members = [info for info in archive.infolist() if info.filename.lower().endswith('.doc')
                       and not info.is_dir() and 0 < info.file_size <= 16 * 1024 * 1024]
            if not members:
                continue
            info = members[0]
            with archive.open(info) as stream:
                content = stream.read(16 * 1024 * 1024 + 1)
            assert len(content) == info.file_size
            source = tmp_path / 'document.doc'
            source.write_bytes(content)
            result = worker.run_document_job(source, job='legacy_doc_to_docx')
            from src.parser.document_sources import extract_docx_blocks
            parsed = extract_docx_blocks(result['docx_bytes'])
            assert parsed['blocks']
            print(json.dumps({key: value for key, value in result.items()
                              if key not in {'docx_bytes', 'docx_base64'}}, ensure_ascii=True))
            print(json.dumps({'archive': path.name, 'member': info.filename,
                              'input_bytes': len(content), 'output_bytes': len(result['docx_bytes']),
                              'blocks': len(parsed['blocks'])}, ensure_ascii=True))
            return
    pytest.fail('No bounded legacy DOC member available')


@pytest.fixture
def rar_source(tmp_path):
    path = tmp_path / 'untrusted name.rar'
    path.write_bytes(b'Rar!\x1a\x07\x00' + b'bounded-test')
    return path


def rar_payload(source):
    return dict(job='rar_list', source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                format='rar4', encrypted='no', tools={'libarchive': 'libarchive 3.6.2'},
                entries=[dict(index=0, path='folder/', type='directory', size=0, encrypted='no', safe=True),
                         dict(index=1, path='folder/a\nb.doc', type='file', size=12, encrypted='no', safe=False),
                         dict(index=2, path='../escape', type='file', size=1, encrypted='no', safe=False)])


def test_rar_listing_is_metadata_only_with_pinned_provenance(worker, rar_source, monkeypatch):
    before = set(rar_source.parent.iterdir())
    data = rar_payload(rar_source)
    calls, _ = docker(monkeypatch, worker, rar_source, output=json.dumps(data).encode())
    result = worker.run_document_job(rar_source, job='rar_list')
    assert result['entries'] == data['entries']
    assert result['image_id'] == IMAGE_ID and result['completeness'] == 'incomplete'
    assert result['review_required'] is True
    assert set(rar_source.parent.iterdir()) == before
    args = calls[1]
    assert args[-2:] == ['rar_list', '1'] and args.count('--mount') == 1
    assert all(flag in args for flag in ['--network=none', '--read-only', '--user=10001:10001'])
    assert 'archive_read_extract' not in args[-3]


@pytest.mark.parametrize('kind', ['magic', 'size', 'page'])
def test_rar_rejects_input_before_docker(worker, rar_source, monkeypatch, kind):
    monkeypatch.setattr(worker.subprocess, 'Popen', lambda *a, **k: pytest.fail('Docker invoked'))
    if kind == 'magic':
        rar_source.write_bytes(b'PK\x03\x04renamed ZIP')
    elif kind == 'size':
        with rar_source.open('r+b') as stream:
            stream.truncate(64 * 1024 * 1024 + 1)
    with pytest.raises(ValueError):
        worker.run_document_job(rar_source, job='rar_list', page=2 if kind == 'page' else 1)


@pytest.mark.parametrize('kind', ['index_bool', 'index_order', 'size_bool', 'size_negative', 'size_overflow',
                                  'path_type', 'path_limit', 'safe_lie', 'safe_int', 'encrypted_bool',
                                  'type_link', 'extra_field', 'entries_limit', 'format', 'format_magic',
                                  'encryption', 'encryption_contradiction', 'tools', 'hash', 'duplicate_json',
                                  'output_limit', 'missing_field'])
def test_rar_rejects_untrusted_metadata(worker, rar_source, monkeypatch, kind):
    data = rar_payload(rar_source)
    entry = data['entries'][0]
    changes = {'index_bool': ('index', False), 'index_order': ('index', 4), 'size_bool': ('size', True),
               'size_negative': ('size', -1), 'size_overflow': ('size', 2 ** 63),
               'path_type': ('path', []), 'path_limit': ('path', 'a' * 4097),
               'safe_lie': ('safe', False), 'safe_int': ('safe', 1),
               'encrypted_bool': ('encrypted', False), 'type_link': ('type', 'symlink')}
    if kind in changes:
        key, value = changes[kind]
        entry[key] = value
    elif kind == 'extra_field':
        entry['payload'] = 'forbidden'
    elif kind == 'entries_limit':
        data['entries'] *= 172
    elif kind in ('format', 'format_magic'):
        data['format'] = 'zip' if kind == 'format' else 'rar5'
    elif kind == 'encryption':
        data['encrypted'] = 'invalid'
    elif kind == 'encryption_contradiction':
        entry['encrypted'] = 'yes'
    elif kind == 'tools':
        data['tools'] = {'libarchive': ''}
    elif kind == 'hash':
        data['source_sha256'] = '0' * 64
    elif kind == 'missing_field':
        del data['encrypted']
    output = json.dumps(data).encode()
    if kind == 'duplicate_json':
        output = output[:-1] + b', "encrypted":"yes"}'
    elif kind == 'output_limit':
        output += b' ' * (1024 * 1024)
    docker(monkeypatch, worker, rar_source, output=output)
    with pytest.raises(ValueError, match='output|limit'):
        worker.run_document_job(rar_source, job='rar_list')


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'stdout', 'stderr'])
def test_rar_process_failures_cleanup(worker, rar_source, monkeypatch, failure):
    options = {'output': json.dumps(rar_payload(rar_source)).encode()}
    if failure == 'exit':
        options['code'] = 1
    elif failure == 'timeout':
        monkeypatch.setattr(worker, 'PROCESS_TIMEOUT', 0.01)
        options['timeout'] = True
    else:
        options['output' if failure == 'stdout' else 'stderr'] = b'x' * (1024 * 1024 + 1)
    calls, processes = docker(monkeypatch, worker, rar_source, **options)
    with pytest.raises((ValueError, TimeoutError)):
        worker.run_document_job(rar_source, job='rar_list')
    assert calls[-1] == ['docker', 'rm', '--force', calls[1][calls[1].index('--name') + 1]]
    assert processes[1].killed


class ArchiveFunction:
    def __init__(self, call):
        self.call = call

    def __call__(self, *args):
        return self.call(*args)


class ArchiveLibrary:
    def __init__(self, names, *, failure=None, filetype=stat.S_IFREG):
        self.position = -1
        self.closed = self.freed = False
        self.names = names
        self.failure = failure
        self.filetype = filetype
        functions = {
            'archive_read_new': lambda: 123,
            'archive_version_string': lambda: b'libarchive 3.6.2',
            'archive_read_support_format_rar': lambda a: 0,
            'archive_read_support_format_rar5': lambda a: 0,
            'archive_read_open_filename': lambda *a: 0,
            'archive_read_next_header': self.next_header,
            'archive_format': lambda a: 0x50000 if failure == 'format' else 0xD0000,
            'archive_entry_pathname_utf8': lambda e: names[self.position],
            'archive_entry_filetype': lambda e: self.filetype,
            'archive_entry_size_is_set': lambda e: 1,
            'archive_entry_size': lambda e: -1 if failure == 'size' else 12,
            'archive_entry_is_encrypted': lambda e: 1 if failure == 'encrypted' else 0,
            'archive_entry_hardlink': lambda e: b'target' if failure == 'hardlink' else None,
            'archive_entry_symlink': lambda e: None,
            'archive_read_has_encrypted_entries': lambda a: 1 if failure == 'encrypted' else 0,
            'archive_read_data_skip': lambda a: -30 if failure == 'skip' else 0,
            'archive_error_string': lambda a: b'Encrypted archive' if failure == 'header' else b'broken',
            'archive_read_close': self.close,
            'archive_read_free': self.free,
        }
        for name, call in functions.items():
            setattr(self, name, ArchiveFunction(call))

    def next_header(self, archive, entry):
        import ctypes
        self.position += 1
        ctypes.cast(entry, ctypes.POINTER(ctypes.c_void_p))[0] = 456
        if self.failure == 'header':
            return -30
        return 0 if self.position < len(self.names) else 1

    def close(self, archive):
        self.closed = True
        return -30 if self.failure == 'close' else 0

    def free(self, archive):
        self.freed = True
        return -30 if self.failure == 'free' else 0


def invoke_rar_inner(monkeypatch, rar_source, library):
    import ctypes
    import ctypes.util
    module = import_module('src.parser.document_rar')
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: 'libarchive.so.13')
    monkeypatch.setattr(ctypes, 'CDLL', lambda name: library)
    return module.list_metadata(rar_source)


def test_rar_inner_preserves_newlines_and_unsafe_names(monkeypatch, rar_source):
    library = ArchiveLibrary([b'normal.doc', b'a\nb.doc', b'../escape'])
    result = invoke_rar_inner(monkeypatch, rar_source, library)
    assert [e['path'] for e in result['entries']] == ['normal.doc', 'a\nb.doc', '../escape']
    assert [e['safe'] for e in result['entries']] == [True, False, False]
    assert library.closed and library.freed
    import ctypes
    assert library.archive_read_new.restype is ctypes.c_void_p
    assert library.archive_entry_size.restype is ctypes.c_int64
    assert library.archive_read_next_header.argtypes == [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]


@pytest.mark.parametrize('failure', ['format', 'size', 'hardlink', 'skip', 'header', 'close', 'free',
                                    'path_limit', 'entries_limit', 'output_limit', 'type', 'utf8'])
def test_rar_inner_fails_closed_and_releases_library(monkeypatch, rar_source, failure):
    names = [b'normal.doc']
    if failure == 'path_limit':
        names = [b'a' * 4097]
    elif failure == 'entries_limit':
        names *= 513
    elif failure == 'output_limit':
        names = [('x' * 3000 + str(i)).encode() for i in range(512)]
    elif failure == 'utf8':
        names = [b'\xff']
    library = ArchiveLibrary(names, failure=failure, filetype=stat.S_IFLNK if failure == 'type' else stat.S_IFREG)
    with pytest.raises(ValueError):
        invoke_rar_inner(monkeypatch, rar_source, library)
    assert library.closed and library.freed


@pytest.mark.parametrize('error', ['encrypted_metadata', 'format_mismatch', 'invalid_archive'])
def test_rar_reports_explicit_archive_failure(worker, rar_source, monkeypatch, error):
    docker(monkeypatch, worker, rar_source, output=json.dumps({'job': 'rar_list', 'error': error}).encode())
    with pytest.raises(ValueError, match=error):
        worker.run_document_job(rar_source, job='rar_list')


@pytest.mark.parametrize('status', ['yes', 'unknown', 'unsupported'])
def test_rar_accepts_explicit_encryption_status(worker, rar_source, monkeypatch, status):
    data = rar_payload(rar_source)
    data['encrypted'] = status
    if status == 'yes':
        data['entries'][0]['encrypted'] = 'yes'
    docker(monkeypatch, worker, rar_source, output=json.dumps(data).encode())
    assert worker.run_document_job(rar_source, job='rar_list')['encrypted'] == status


def test_rar_accepts_exact_member_and_path_limits(worker, rar_source, monkeypatch):
    data = rar_payload(rar_source)
    data['entries'] = [dict(index=i, path='a' * 4096 if i == 0 else str(i), type='file',
                            size=2 ** 63 - 1, encrypted='no', safe=True) for i in range(512)]
    docker(monkeypatch, worker, rar_source, output=json.dumps(data).encode())
    assert len(worker.run_document_job(rar_source, job='rar_list')['entries']) == 512


def test_rar_inner_marks_encryption_without_extracting(monkeypatch, rar_source):
    library = ArchiveLibrary([b'encrypted.doc'], failure='encrypted')
    data = invoke_rar_inner(monkeypatch, rar_source, library)
    assert data['encrypted'] == 'yes' and data['entries'][0]['encrypted'] == 'yes'
    assert library.closed and library.freed


@pytest.mark.parametrize('path', ['/absolute', 'C:/drive', 'a\\b', 'a\x00b', 'a/../b',
                                  './a', 'a//b', 'CON.doc', 'a.', 'a ', ''])
def test_rar_inner_flags_unsafe_paths(monkeypatch, rar_source, path):
    data = invoke_rar_inner(monkeypatch, rar_source, ArchiveLibrary([path.encode()]))
    assert data['entries'][0]['path'] == path and data['entries'][0]['safe'] is False


@pytest.mark.skipif(os.getenv('DOCUMENT_WORKER_SMOKE') != '1', reason='Optional actual Docker smoke')
def test_actual_four_rar_metadata_smoke(worker):
    root = Path(__file__).resolve().parents[1]
    candidates = sorted((root / 'data/raw').glob('*.rar'))
    assert len(candidates) == 4
    for source in candidates:
        result = worker.run_document_job(source, job='rar_list')
        assert result['entries'] and result['tools']['libarchive']
        print(json.dumps(dict(archive=source.name, entries=len(result['entries']),
                              files=sum(e['type'] == 'file' for e in result['entries']),
                              directories=sum(e['type'] == 'directory' for e in result['entries']),
                              unsafe=sum(not e['safe'] for e in result['entries']),
                              encrypted=result['encrypted'], image_id=result['image_id'],
                              tools=result['tools']), ensure_ascii=True))
        assert result['completeness'] == 'incomplete' and result['review_required'] is True
        assert not any(key in result for key in ('blocks', 'payload', 'text'))

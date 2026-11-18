import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import threading
import time
import uuid


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
PROCESS_TIMEOUT = 120
MAX_PAGE = 10000
MAX_DOC_BYTES = 16 * 1024 * 1024
MAX_DOCX_BYTES = 2 * 1024 * 1024
_JOBS = {'pdf_text_page': {'pdftotext'}, 'ocr_page': {'pdftoppm', 'tesseract'},
          'legacy_doc_to_docx': {'libreoffice'}, 'rar_list': {'libarchive'}, 'rar_member': {'libarchive'},
          'rar_member_chunk': {'libarchive'}}
from . import document_rar

_RAR_WORKER = Path(document_rar.__file__).read_text(encoding='utf-8')
MAX_RAR_OUTPUT_BYTES = document_rar.MAX_METADATA_BYTES
_LEGACY_WORKER = r'''
import base64, hashlib, json, resource, subprocess, sys
from pathlib import Path
from io import BytesIO
from zipfile import ZipFile
import xml.etree.ElementTree as ET
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
if sys.argv[1:] != ['legacy_doc_to_docx', '1']:
    raise ValueError('Unsupported job or page')
source = Path('/input/source')
if not source.is_file() or not 8 <= source.stat().st_size <= 16 * 1024 * 1024:
    raise ValueError('Invalid source')
with source.open('rb') as stream:
    content = stream.read(16 * 1024 * 1024 + 1)
if len(content) > 16 * 1024 * 1024 or content[:8] != bytes.fromhex('d0cf11e0a1b11ae1'):
    raise ValueError('Invalid OLE source')
digest = hashlib.sha256(content).hexdigest()
Path('/tmp/document.doc').write_bytes(content)
del content
profile = Path('/tmp/profile')
(profile / 'user').mkdir(parents=True)
ns = 'http://openoffice.org/2001/registry'
ET.register_namespace('oor', ns)
root = ET.Element('{%s}items' % ns)
settings = {
    '/org.openoffice.Office.Common/Security/Scripting': {
        'MacroSecurityLevel': '3', 'DisableMacrosExecution': 'true',
        'BlockUntrustedRefererLinks': 'true', 'SecureURL': ''},
    '/org.openoffice.Office.Writer/Content/Update': {
        'Link': '2', 'Field': 'false', 'Chart': 'false'},
    '/org.openoffice.Office.Writer/Filter/Import/VBA': {
        'Load': 'false', 'Executable': 'false'},
    '/org.openoffice.Office.Writer/GlobalTemplateList': {'Paths': ''},
}
for path, values in settings.items():
    item = ET.SubElement(root, 'item', {'{%s}path' % ns: path})
    for name, value in values.items():
        prop = ET.SubElement(item, 'prop', {'{%s}name' % ns: name, '{%s}op' % ns: 'fuse'})
        ET.SubElement(prop, 'value').text = value
ET.ElementTree(root).write(profile / 'user/registrymodifications.xcu', encoding='utf-8', xml_declaration=True)
with open('/tmp/version', 'wb') as out:
    subprocess.run(['libreoffice', '--version'], stdin=subprocess.DEVNULL,
                   stdout=out, stderr=out, check=True, timeout=5)
with open('/tmp/version', 'rb') as stream:
    version = stream.read(4097)
if not version or len(version) > 4096:
    raise ValueError('Invalid version')
with open('/tmp/stdout', 'wb') as out, open('/tmp/errors', 'wb') as err:
    subprocess.run(['libreoffice', '-env:UserInstallation=' + profile.as_uri(),
                    '--headless', '--nologo', '--nodefault', '--norestore', '--nolockcheck',
                    '--infilter=MS Word 97', '--convert-to', 'docx:Office Open XML Text',
                    '--outdir', '/tmp', '/tmp/document.doc'], stdin=subprocess.DEVNULL,
                   stdout=out, stderr=err, check=True, timeout=90)
output = Path('/tmp/document.docx')
if output.is_symlink() or not output.is_file() or not 0 < output.stat().st_size <= 2 * 1024 * 1024:
    raise ValueError('Invalid converted output size')
with output.open('rb') as stream:
    content = stream.read(2 * 1024 * 1024 + 1)
if len(content) > 2 * 1024 * 1024:
    raise ValueError('Converted output limit')
with ZipFile(BytesIO(content)) as archive:
    members = archive.infolist()
    if len(members) > 2048 or sum(m.file_size for m in members) > 16 * 1024 * 1024:
        raise ValueError('Converted package limit')
    names = set()
    for member in members:
        name = member.orig_filename
        if (not name or name.startswith('/') or '\\' in name or ':' in name or '\x00' in name
                or any(p in ('', '.', '..') for p in name.rstrip('/').split('/'))
                or name in names or member.flag_bits & 1 or member.compress_type not in (0, 8)
                or (member.external_attr >> 16) & 0o170000 == 0o120000
                or member.file_size > 16 * 1024 * 1024
                or member.file_size > max(member.compress_size, 1) * 200):
            raise ValueError('Invalid converted package member')
        names.add(name)
        with archive.open(member) as stream:
            if len(stream.read(16 * 1024 * 1024 + 1)) != member.file_size:
                raise ValueError('Invalid converted package data')
    if not {'[Content_Types].xml', 'word/document.xml'} <= names:
        raise ValueError('Missing DOCX parts')
print(json.dumps(dict(job='legacy_doc_to_docx', page=1, source_sha256=digest,
                      converted_sha256=hashlib.sha256(content).hexdigest(),
                      docx_base64=base64.b64encode(content).decode('ascii'),
                      tools={'libreoffice': version.decode('utf-8').splitlines()[0]})))
'''
_WORKER = r'''
import hashlib, json, os, resource, subprocess, sys
from pathlib import Path
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
job, page = sys.argv[1], int(sys.argv[2])
if job not in ('ocr_page', 'pdf_text_page') or not 1 <= page <= 10000:
    raise ValueError('Unsupported job or page')
source = Path('/input/source')
if not source.is_file() or not 0 < source.stat().st_size <= 64 * 1024 * 1024:
    raise ValueError('Invalid source')
with source.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
def command(args, target):
    with open(target, 'wb') as out, open('/tmp/errors', 'wb') as err:
        subprocess.run(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                       check=True, timeout=90)
versions = {}
for tool in (('pdftotext',) if job == 'pdf_text_page' else ('pdftoppm', 'tesseract')):
    flag = '--version' if tool == 'tesseract' else '-v'
    with open('/tmp/version', 'wb') as out:
        subprocess.run([tool, flag], stdin=subprocess.DEVNULL, stdout=out,
                       stderr=out, check=True, timeout=5)
    with open('/tmp/version', 'rb') as stream:
        value = stream.read(4097)
    if not value or len(value) > 4096:
        raise ValueError('Invalid version')
    versions[tool] = value.decode('utf-8').splitlines()[0]
if job == 'pdf_text_page':
    command(['pdftotext', '-f', str(page), '-l', str(page), '-enc', 'UTF-8',
             '/input/source', '/tmp/text'], '/tmp/stdout')
else:
    command(['pdftoppm', '-f', str(page), '-l', str(page), '-singlefile',
             '-scale-to', '4000', '-png', '/input/source', '/tmp/page'], '/tmp/stdout')
    command(['tesseract', '/tmp/page.png', '/tmp/ocr', '-l', 'vie+eng'], '/tmp/stdout')
    os.rename('/tmp/ocr.txt', '/tmp/text')
with open('/tmp/text', 'rb') as stream:
    text = stream.read(512 * 1024 + 1)
if len(text) > 512 * 1024:
    raise ValueError('Text output limit')
print(json.dumps(dict(text=text.decode('utf-8'), tools=versions, job=job,
                      page=page, source_sha256=digest), ensure_ascii=True))
'''


def _drain(stream, buffer, overflow, errors, limit):
    try:
        read = getattr(stream, 'read1', stream.read)
        while True:
            chunk = read(8192)
            if not chunk:
                return
            if len(buffer) + len(chunk) > limit:
                overflow.set()
                return
            buffer.extend(chunk)
    except Exception as exc:
        errors.append(exc)
        overflow.set()


def _stop(process, name):
    try:
        process.kill()
    finally:
        try:
            if name is not None:
                _capture(['docker', 'rm', '--force', name], timeout=10)
        finally:
            process.wait(timeout=10)


def _capture(args, *, timeout, name=None, limit=None):
    process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, shell=False)
    buffers, errors, overflow = [bytearray(), bytearray()], [], threading.Event()
    limit = MAX_OUTPUT_BYTES if limit is None else min(limit, MAX_OUTPUT_BYTES)
    threads = [threading.Thread(target=_drain, args=(stream, buffer, overflow, errors, limit), daemon=True)
               for stream, buffer in zip((process.stdout, process.stderr), buffers)]
    deadline = time.monotonic() + timeout
    for thread in threads:
        thread.start()
    try:
        while True:
            if overflow.is_set():
                raise ValueError('Output byte limit exceeded or stream read failed')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('Document worker timed out')
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
            for thread in threads:
                thread.join(timeout=min(0.05, remaining))
            if not any(thread.is_alive() for thread in threads):
                if overflow.is_set() or errors:
                    raise ValueError('Output byte limit exceeded or stream read failed')
                break
        if process.returncode != 0:
            raise ValueError('Document worker command failed')
        return bytes(buffers[0])
    except BaseException:
        _stop(process, name)
        raise
    finally:
        for thread in threads:
            thread.join(timeout=1)
        for stream in (process.stdout, process.stderr):
            stream.close()


def _source_hash(source, *, legacy=False, rar=False):
    try:
        path = Path(source)
        if path.is_symlink():
            raise ValueError('Source symlinks are unsupported')
        path = path.resolve(strict=True)
        if any(char in str(path) for char in (',', '\n', '\r', '"')):
            raise ValueError('Source path cannot be represented safely as a Docker mount')
        if not path.is_file():
            raise ValueError('Source must be a regular file')
        flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
        with os.fdopen(os.open(path, flags), 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            limit = min(MAX_INPUT_BYTES, MAX_DOC_BYTES) if legacy else MAX_INPUT_BYTES
            if legacy:
                if path.suffix.lower() != '.doc' or stream.read(8) != bytes.fromhex('d0cf11e0a1b11ae1'):
                    raise ValueError('Source must be an OLE .doc file')
                stream.seek(0)
            if rar:
                document_rar.source_format(stream.read(8))
                stream.seek(0)
            if not stat.S_ISREG(metadata.st_mode) or not 0 < metadata.st_size <= limit:
                raise ValueError('Source must be a nonempty regular file within input size limit')
            digest, total = hashlib.sha256(), 0
            while chunk := stream.read(65536):
                total += len(chunk)
                if total > limit:
                    raise ValueError('Source input size limit exceeded')
                digest.update(chunk)
        return path, digest.hexdigest()
    except (OSError, TypeError) as exc:
        raise ValueError('Source must be an existing regular file') from exc


def _legacy_result(output, digest, image):
    from .document_sources import _OutputBudget, _read_package

    try:
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError('Output byte limit')
        data = json.loads(output.decode('utf-8'))
        fields = {'job', 'page', 'source_sha256', 'converted_sha256', 'docx_base64', 'tools'}
        if not isinstance(data, dict) or set(data) != fields:
            raise ValueError('Invalid conversion schema')
        encoded, tools = data['docx_base64'], data['tools']
        if (data['job'] != 'legacy_doc_to_docx' or type(data['page']) is not int or data['page'] != 1
                or data['source_sha256'] != digest or not isinstance(encoded, str)
                or len(encoded) > 4 * ((MAX_DOCX_BYTES + 2) // 3)
                or not isinstance(tools, dict) or set(tools) != {'libreoffice'}
                or not isinstance(tools['libreoffice'], str) or not tools['libreoffice'].strip()
                or len(tools['libreoffice']) > 4096):
            raise ValueError('Invalid conversion provenance')
        content = base64.b64decode(encoded, validate=True)
        if (not 0 < len(content) <= MAX_DOCX_BYTES
                or hashlib.sha256(content).hexdigest() != data['converted_sha256']):
            raise ValueError('Invalid conversion bytes')
        _read_package(content, _OutputBudget())
    except (ValueError, UnicodeError, RecursionError, OSError) as exc:
        raise ValueError('Invalid document worker output') from exc
    return dict(data, docx_bytes=content, image_id=image, review_required=True, completeness='incomplete')


def _result(output, job, page, digest, image):
    try:
        data = json.loads(output.decode('utf-8'))
        if not isinstance(data, dict) or set(data) != {'text', 'tools', 'job', 'page', 'source_sha256'}:
            raise ValueError('Invalid worker output schema')
        tools = data['tools']
        valid = (isinstance(data['text'], str) and len(data['text'].encode('utf-8')) <= 512 * 1024
                 and data['job'] == job and type(data['page']) is int and data['page'] == page
                 and data['source_sha256'] == digest and isinstance(tools, dict)
                 and set(tools) == _JOBS[job])
        if not valid or any(not isinstance(v, str) or not v.strip() or len(v) > 4096 for v in tools.values()):
            raise ValueError('Invalid worker output provenance')
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError('Invalid document worker output') from exc
    return dict(data, image_id=image, review_required=True, completeness='incomplete')


def read_rar_member(source, member_index) -> dict:
    first, content, chunks = None, bytearray(), []
    identity = ('source_sha256', 'format', 'encrypted', 'entries', 'member_index',
                'member_sha256', 'image_id', 'tools')
    while True:
        result = run_document_job(source, job='rar_member_chunk', member_index=member_index,
                                  offset=len(content))
        if first is None:
            first = result
        elif any(result[key] != first[key] for key in identity):
            raise ValueError('RAR member changed between chunks')
        size = first['entries'][member_index]['size']
        chunk = result['member_bytes']
        if (not 0 <= size <= document_rar.MAX_LARGE_MEMBER_BYTES
                or len(chunk) != min(document_rar.CHUNK_BYTES, size - len(content))
                or (not chunk and size != 0)):
            raise ValueError('RAR invalid assembled size')
        chunks.append(dict(offset=len(content), size=len(chunk), chunk_sha256=result['chunk_sha256'],
                           **{key: result[key] for key in identity if key != 'entries'}))
        content.extend(chunk)
        if len(content) == size:
            break
    if hashlib.sha256(content).hexdigest() != first['member_sha256']:
        raise ValueError('RAR assembled member hash mismatch')
    return dict({k: v for k, v in first.items()
                 if k not in ('member_base64', 'member_bytes', 'offset', 'chunk_sha256')},
                member_bytes=bytes(content), chunks=chunks)


def run_document_job(source, *, job, page=1, member_index=None, offset=None) -> dict:
    if not isinstance(job, str) or job not in _JOBS:
        raise ValueError('Unsupported document worker job')
    chunk_job = job == 'rar_member_chunk'
    member = job in ('rar_member', 'rar_member_chunk')
    if chunk_job:
        if type(offset) is not int or not 0 <= offset < document_rar.MAX_LARGE_MEMBER_BYTES:
            raise ValueError('Invalid RAR chunk offset')
    elif offset is not None:
        raise ValueError('Offset only supported for rar_member_chunk')
    if member:
        if type(member_index) is not int or not 0 <= member_index < document_rar.MAX_ENTRIES:
            raise ValueError('Invalid RAR member index')
    elif member_index is not None:
        raise ValueError('Member index only supported for rar_member')
    legacy, rar = job == 'legacy_doc_to_docx', job in ('rar_list', 'rar_member', 'rar_member_chunk')
    if type(page) is not int or not 1 <= page <= MAX_PAGE or ((legacy or rar) and page != 1):
        raise ValueError('Invalid document page')
    path, digest = _source_hash(source, legacy=legacy, rar=rar)
    format_name = None
    if rar:
        with path.open('rb') as stream:
            format_name = document_rar.source_format(stream.read(8))
    image = _capture(['docker', 'image', 'inspect', '--format', '{{.Id}}',
                      'vat-document-worker:local'], timeout=10).decode('ascii').strip()
    if re.fullmatch(r'sha256:[0-9a-f]{64}', image) is None:
        raise ValueError('Invalid immutable local image ID')
    name = 'vat-document-' + uuid.uuid4().hex
    args = ['docker', 'run', '--rm', '--pull=never', '--name', name, '--network=none',
            '--read-only', '--user=10001:10001', '--cap-drop=ALL',
            '--security-opt=no-new-privileges', '--cpus=2', '--memory=2g',
            '--memory-swap=2g', '--pids-limit=128', '--log-driver=none',
            '--tmpfs', '/tmp:rw,noexec,nosuid,nodev,size=512m,mode=1777',
            '--mount', f'type=bind,source={path},target=/input/source,readonly',
            image, 'python3', '-c', _RAR_WORKER if rar else (_LEGACY_WORKER if legacy else _WORKER),
            job, str(member_index if member else page)]
    if chunk_job:
        args.append(str(offset))
    output = _capture(args, timeout=PROCESS_TIMEOUT, name=name,
                      limit=document_rar.MAX_CHUNK_OUTPUT_BYTES if chunk_job else
                      MAX_RAR_OUTPUT_BYTES if rar and not member else None)
    if _source_hash(path)[1] != digest:
        raise ValueError('Source changed during document job')
    if chunk_job:
        return document_rar.validate_chunk_result(output, digest, format_name, image, member_index, offset)
    if member:
        return document_rar.validate_member_result(output, digest, format_name, image, member_index)
    if rar:
        return document_rar.validate_result(output, digest, format_name, image)
    return _legacy_result(output, digest, image) if legacy else _result(output, job, page, digest, image)

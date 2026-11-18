from contextlib import nullcontext
from io import BytesIO
from os import PathLike
import hashlib
import json
from zipfile import BadZipFile, ZipFile
import zlib

from . import document_sources


MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_SEEKABLE_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_CENTRAL_DIRECTORY_BYTES = 4 * 1024 * 1024
MAX_MEMBERS = 512
MAX_PART_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_PROCESSING_BYTES = 64 * 1024 * 1024
MAX_PROCESSING_MEMBERS = 4096
MAX_OUTPUT_BYTES = 16 * 1024 * 1024


class _BudgetLimit(ValueError):
    pass


class _ReportBudget:
    def __init__(self):
        self.remaining = MAX_OUTPUT_BYTES - 512
        self.record()

    def record(self):
        self.remaining -= 512
        if self.remaining < 0:
            raise _BudgetLimit('Output byte limit exceeded')

    def consume(self, text):
        self.remaining -= len(json.dumps(text).encode('utf-8')) - 2
        if self.remaining < 0:
            raise _BudgetLimit('Output byte limit exceeded')
        return text


class _ProcessingBudget:
    def __init__(self):
        self.bytes_remaining = MAX_PROCESSING_BYTES
        self.members_remaining = MAX_PROCESSING_MEMBERS

    def charge(self, members):
        self.bytes_remaining -= sum(info.file_size for info in members)
        self.members_remaining -= len(members)
        if self.bytes_remaining < 0 or self.members_remaining < 0:
            raise _BudgetLimit('Archive processing budget limit exceeded')


def _member_report(archive, info, output, processing):
    with archive.open(info) as stream:
        data = stream.read(MAX_PART_BYTES + 1)
    if len(data) > MAX_PART_BYTES or len(data) != info.file_size:
        raise ValueError('Archive part size limit or size mismatch')
    report = _member_metadata(info, output, extracted=True)
    if report['status'] == 'extracted':
        nested_stream = BytesIO(data)
        document_sources._preflight_zip(nested_stream, vars(document_sources))
        with ZipFile(nested_stream) as nested:
            processing.charge(document_sources._validate_members(nested))
        report['evidence'] = document_sources.extract_docx_blocks(data, _budget=output)
    report['sha256'] = output.consume(hashlib.sha256(data).hexdigest())
    return report


def _member_metadata(info, output, *, extracted=False):
    output.record()
    name = info.filename
    basename = name.rsplit('/', 1)[-1]
    extension = basename.rsplit('.', 1)[-1].lower() if '.' in basename else ''
    source_format = extension if extension in {'docx', 'doc', 'pdf', 'rar', 'zip'} else 'unknown'
    status = 'unsupported'
    if info.is_dir():
        source_format, status = 'directory', 'directory'
    elif basename.startswith('~$'):
        status = 'ignored_office_lock'
    elif source_format == 'docx':
        status = 'extracted' if extracted else 'pending'
    report = {
        'path': output.consume(name),
        'format': output.consume(source_format),
        'status': output.consume(status),
    }
    return report


def _page_members(archive, members, start, page_size, extract):
    output = _ReportBudget()
    processing = _ProcessingBudget()
    reports = []
    next_start = start
    status = 'complete'
    for info in members[start:start + page_size]:
        try:
            if extract:
                processing.charge([info])
                report = _member_report(archive, info, output, processing)
            else:
                report = _member_metadata(info, output)
        except _BudgetLimit as exc:
            status = ('output_limit' if 'Output' in str(exc) else 'processing_limit') if reports else 'member_limit'
            break
        reports.append(report)
        next_start += 1
    if next_start == len(members):
        next_start = None
    elif status == 'complete':
        status = 'more'
    return {
        'source_format': 'zip', 'members': reports, 'status': status,
        'next_start': next_start, 'total_members': len(members),
        'crc_verified': extract and bool(reports), 'crc_scope': 'returned_members_only',
        'completeness': 'incomplete',
        'warnings': ['Evidence only; enumeration does not verify payload CRC. Unsupported members '
                     'are not parsed for document evidence; nested ZIPs are not traversed; coverage is not certified.'],
    }


def extract_zip_page(source, *, start=0, page_size=1, extract=False) -> dict:
    if type(start) is not int or start < 0 or type(page_size) is not int or not 1 <= page_size <= MAX_MEMBERS:
        raise ValueError('Invalid ZIP page start or size')
    if type(extract) is not bool:
        raise TypeError('extract must be bool')
    context = open(source, 'rb') if isinstance(source, (str, PathLike)) else nullcontext(source)
    with context as stream:
        if not all(callable(getattr(stream, name, None)) for name in ('read', 'seek', 'tell', 'seekable')):
            raise TypeError('ZIP source must be a path or seekable binary stream')
        if not stream.seekable():
            raise ValueError('ZIP source must be seekable')
        position = stream.tell()
        try:
            document_sources._preflight_zip(stream, globals(), MAX_SEEKABLE_ARCHIVE_BYTES)
            with ZipFile(stream) as archive:
                members = document_sources._validate_members(archive, require_docx=False, limits=globals())
                if start > len(members):
                    raise ValueError('Invalid ZIP page start')
                return _page_members(archive, members, start, page_size, extract)
        except (BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
            raise ValueError('Invalid or unsupported ZIP archive') from exc
        finally:
            stream.seek(position)


def extract_zip_sources(content: bytes) -> dict:
    if not isinstance(content, bytes):
        raise TypeError('ZIP content must be bytes')
    if len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError('Archive byte size limit exceeded')
    output = _ReportBudget()
    processing = _ProcessingBudget()
    reports = []
    try:
        stream = BytesIO(content)
        document_sources._preflight_zip(stream, globals())
        with ZipFile(stream) as archive:
            members = document_sources._validate_members(archive, require_docx=False, limits=globals())
            processing.charge(members)
            for info in members:
                reports.append(_member_report(archive, info, output, processing))
    except (BadZipFile, RuntimeError, NotImplementedError, zlib.error, EOFError) as exc:
        raise ValueError('Invalid or unsupported ZIP archive') from exc
    return {
        'source_format': output.consume('zip'),
        'members': reports,
        'completeness': output.consume('incomplete'),
        'warnings': [output.consume(
            'Evidence only; unsupported members and Office lock files are not extracted; '
            'nested ZIP archives are not traversed. Completeness and coverage are not certified.'
        )],
    }

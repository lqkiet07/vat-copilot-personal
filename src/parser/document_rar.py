import base64
import ctypes
import ctypes.util
import hashlib
import json
from pathlib import Path
import stat
import sys


MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_METADATA_BYTES = 1024 * 1024
MAX_ENTRIES = 512
MAX_PATH_BYTES = 4096
MAX_MEMBER_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_BYTES = 4 * 1024 * 1024
MAX_TRAVERSAL_BYTES = 64 * 1024 * 1024
MAX_LARGE_MEMBER_BYTES = 16 * 1024 * 1024
CHUNK_BYTES = 512 * 1024
MAX_CHUNK_OUTPUT_BYTES = 2 * 1024 * 1024


def source_format(magic):
    if magic.startswith(b'Rar!\x1a\x07\x00'):
        return 'rar4'
    if magic.startswith(b'Rar!\x1a\x07\x01\x00'):
        return 'rar5'
    raise ValueError('RAR format mismatch')


def safe_path(path, kind):
    name = path[:-1] if kind == 'directory' and path.endswith('/') else path
    return bool(name and not name.startswith('/') and not any(c in path for c in '\\:*?"<>|')
                and not any(ord(c) < 32 or ord(c) == 127 for c in path)
                and all(p not in ('', '.', '..') and not p.endswith((' ', '.'))
                        and p.split('.')[0].upper() not in
                        {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                         *(f'LPT{i}' for i in range(1, 10))} for p in name.split('/')))


def _library(member=False):
    name = ctypes.util.find_library('archive')
    if not name:
        raise ValueError('RAR library unavailable')
    lib = ctypes.CDLL(name)
    pointer = ctypes.c_void_p
    signatures = {
        'archive_read_new': (pointer, []),
        'archive_version_string': (ctypes.c_char_p, []),
        'archive_read_open_filename': (ctypes.c_int, [pointer, ctypes.c_char_p, ctypes.c_size_t]),
        'archive_read_next_header': (ctypes.c_int, [pointer, ctypes.POINTER(pointer)]),
        'archive_entry_pathname_utf8': (ctypes.c_char_p, [pointer]),
        'archive_entry_filetype': (ctypes.c_uint, [pointer]),
        'archive_entry_size': (ctypes.c_int64, [pointer]),
        'archive_entry_hardlink': (ctypes.c_char_p, [pointer]),
        'archive_entry_symlink': (ctypes.c_char_p, [pointer]),
        'archive_error_string': (ctypes.c_char_p, [pointer]),
    }
    for function in ('archive_read_support_format_rar', 'archive_read_support_format_rar5',
                     'archive_format', 'archive_entry_size_is_set', 'archive_entry_is_encrypted',
                     'archive_read_has_encrypted_entries', 'archive_read_data_skip',
                     'archive_read_close', 'archive_read_free'):
        signatures[function] = (ctypes.c_int, [pointer])
    if member:
        signatures['archive_read_data'] = (ctypes.c_ssize_t, [pointer, pointer, ctypes.c_size_t])
    for function, (result, arguments) in signatures.items():
        call = getattr(lib, function)
        call.restype, call.argtypes = result, arguments
    return lib


def _check(lib, archive, code):
    if code != 0:
        error = (lib.archive_error_string(archive) or b'').lower()
        if lib.archive_read_has_encrypted_entries(archive) == 1 or b'encrypt' in error or b'passphrase' in error:
            raise ValueError('RAR encrypted metadata unavailable')
        raise ValueError('RAR invalid archive')


def _entry(lib, entry, index):
    raw = lib.archive_entry_pathname_utf8(entry)
    if raw is None or len(raw) > MAX_PATH_BYTES:
        raise ValueError('RAR path limit or encoding')
    try:
        path = raw.decode('utf-8', errors='strict')
    except UnicodeError as exc:
        raise ValueError('RAR path encoding') from exc
    kind = {stat.S_IFREG: 'file', stat.S_IFDIR: 'directory'}.get(lib.archive_entry_filetype(entry))
    if kind is None or lib.archive_entry_hardlink(entry) is not None or lib.archive_entry_symlink(entry) is not None:
        raise ValueError('RAR unsupported member type')
    size = lib.archive_entry_size(entry)
    if not lib.archive_entry_size_is_set(entry) or not 0 <= size < 2 ** 63:
        raise ValueError('RAR invalid member size')
    encrypted = lib.archive_entry_is_encrypted(entry)
    if encrypted not in (0, 1):
        raise ValueError('RAR invalid encryption status')
    return dict(index=index, path=path, type=kind, size=size,
                encrypted='yes' if encrypted else 'no', safe=safe_path(path, kind))


def _encode(data):
    output = json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    limit = (MAX_CHUNK_OUTPUT_BYTES if data.get('job') == 'rar_member_chunk' else
             MAX_OUTPUT_BYTES if data.get('job') == 'rar_member' else MAX_METADATA_BYTES)
    if len(output) + 1 > limit:
        raise ValueError('RAR metadata output limit')
    return output


def _read_payload(lib, archive, metadata):
    if metadata['type'] != 'file' or metadata['encrypted'] != 'no' or metadata['size'] > MAX_MEMBER_BYTES:
        raise ValueError('RAR unsupported or oversized member')
    content = bytearray()
    buffer = ctypes.create_string_buffer(65536)
    while True:
        count = lib.archive_read_data(archive, buffer, min(65536, metadata['size'] - len(content) + 1))
        if count < 0:
            _check(lib, archive, count)
        if count == 0:
            break
        if count > 65536 or len(content) + count > metadata['size']:
            raise ValueError('RAR member size mismatch')
        content.extend(buffer.raw[:count])
    if len(content) != metadata['size']:
        raise ValueError('RAR truncated member')
    return dict(member_sha256=hashlib.sha256(content).hexdigest(),
                member_base64=base64.b64encode(content).decode('ascii'))


def _read_chunk(lib, archive, metadata, offset):
    size = metadata['size']
    if (metadata['type'] != 'file' or metadata['encrypted'] != 'no'
            or not 0 <= size <= MAX_LARGE_MEMBER_BYTES or offset > max(0, size - 1)):
        raise ValueError('RAR unsupported member or chunk offset')
    content, total, digest = bytearray(), 0, hashlib.sha256()
    buffer = ctypes.create_string_buffer(65536)
    while True:
        count = lib.archive_read_data(archive, buffer, min(65536, size - total + 1))
        if count < 0:
            _check(lib, archive, count)
        if count == 0:
            break
        if count > 65536 or total + count > size:
            raise ValueError('RAR member size mismatch')
        raw = buffer.raw[:count]
        digest.update(raw)
        start, end = max(offset, total), min(offset + CHUNK_BYTES, total + count)
        if start < end:
            content.extend(raw[start - total:end - total])
        total += count
    if total != size:
        raise ValueError('RAR truncated member')
    return dict(member_sha256=digest.hexdigest(), offset=offset,
                chunk_sha256=hashlib.sha256(content).hexdigest(),
                member_base64=base64.b64encode(content).decode('ascii'))


def _headers(lib, archive, data, member_index=None, offset=None):
    expected = {'rar4': 0xD0000, 'rar5': 0x100000}[data['format']]
    entry = ctypes.c_void_p()
    traversed = 0
    while True:
        code = lib.archive_read_next_header(archive, ctypes.byref(entry))
        if code != 1:
            _check(lib, archive, code)
        if lib.archive_format(archive) != expected:
            raise ValueError('RAR format mismatch')
        if code == 1:
            break
        if not entry.value or len(data['entries']) >= MAX_ENTRIES:
            raise ValueError('RAR entry limit or invalid header')
        metadata = _entry(lib, entry, len(data['entries']))
        data['entries'].append(metadata)
        _encode(data)
        if member_index is not None:
            traversed += metadata['size']
            if metadata['encrypted'] != 'no' or traversed > MAX_TRAVERSAL_BYTES:
                raise ValueError('RAR encrypted member or traversal limit')
            if metadata['index'] == member_index:
                data.update(_read_payload(lib, archive, metadata) if offset is None
                            else _read_chunk(lib, archive, metadata, offset))
                break
        _check(lib, archive, lib.archive_read_data_skip(archive))
    if member_index is not None and 'member_base64' not in data:
        raise ValueError('RAR member index unavailable')
    encryption = lib.archive_read_has_encrypted_entries(archive)
    if encryption not in (-2, -1, 0, 1):
        raise ValueError('RAR invalid encryption status')
    data['encrypted'] = {0: 'no', 1: 'yes', -1: 'unknown', -2: 'unsupported'}[encryption]
    if any(e['encrypted'] == 'yes' for e in data['entries']):
        data['encrypted'] = 'yes'
    if member_index is not None and data['encrypted'] == 'yes':
        raise ValueError('RAR encryption status not verified')


def list_metadata(source):
    return _read_archive(source)


def read_member(source, member_index):
    if type(member_index) is not int or not 0 <= member_index < MAX_ENTRIES:
        raise ValueError('RAR invalid member index')
    return _read_archive(source, member_index)


def read_member_chunk(source, member_index, offset):
    if type(member_index) is not int or not 0 <= member_index < MAX_ENTRIES:
        raise ValueError('RAR invalid member index')
    if type(offset) is not int or not 0 <= offset < MAX_LARGE_MEMBER_BYTES:
        raise ValueError('RAR invalid chunk offset')
    return _read_archive(source, member_index, offset)


def _read_archive(source, member_index=None, offset=None):
    source = Path(source)
    if source.is_symlink() or not source.is_file() or not 0 < source.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError('RAR invalid source size or type')
    with source.open('rb') as stream:
        format_name = source_format(stream.read(8))
        stream.seek(0)
        digest, total = hashlib.sha256(), 0
        while chunk := stream.read(65536):
            total += len(chunk)
            if total > MAX_INPUT_BYTES:
                raise ValueError('RAR input size limit')
            digest.update(chunk)
    lib = _library(member_index is not None)
    version = lib.archive_version_string()
    if not version or len(version) > 4096:
        raise ValueError('RAR invalid library version')
    data = dict(job='rar_list', source_sha256=digest.hexdigest(), format=format_name,
                encrypted='unknown', tools={'libarchive': version.decode('utf-8')}, entries=[])
    if member_index is not None:
        data.update(job='rar_member' if offset is None else 'rar_member_chunk', member_index=member_index)
    archive = lib.archive_read_new()
    if not archive:
        raise ValueError('RAR reader allocation failed')
    try:
        _check(lib, archive, lib.archive_read_support_format_rar(archive))
        _check(lib, archive, lib.archive_read_support_format_rar5(archive))
        _check(lib, archive, lib.archive_read_open_filename(archive, bytes(source), 65536))
        _headers(lib, archive, data, member_index, offset)
    finally:
        try:
            closed = lib.archive_read_close(archive)
        finally:
            freed = lib.archive_read_free(archive)
        if closed != 0 or freed != 0:
            raise ValueError('RAR reader close/free failed')
    _encode(data)
    return data


def _unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError('Duplicate JSON field')
        data[key] = value
    return data


def validate_result(output, digest, format_name, image):
    try:
        if len(output) > MAX_METADATA_BYTES:
            raise ValueError('RAR metadata output limit')
        data = json.loads(output.decode('utf-8'), object_pairs_hook=_unique_object)
        if (type(data) is dict and set(data) == {'job', 'error'} and data['job'] == 'rar_list'
                and type(data['error']) is str
                and data['error'] in ('encrypted_metadata', 'format_mismatch', 'invalid_archive')):
            raise ValueError('RAR worker output error: ' + data['error'])
        fields = {'job', 'source_sha256', 'format', 'encrypted', 'tools', 'entries'}
        if type(data) is not dict or set(data) != fields:
            raise ValueError('RAR invalid schema')
        tools, entries = data['tools'], data['entries']
        if (data['job'] != 'rar_list' or data['source_sha256'] != digest or data['format'] != format_name
                or type(data['encrypted']) is not str or data['encrypted'] not in ('yes', 'no', 'unknown', 'unsupported')
                or type(tools) is not dict or set(tools) != {'libarchive'}
                or type(tools['libarchive']) is not str or not tools['libarchive'].strip()
                or len(tools['libarchive']) > 4096 or type(entries) is not list or len(entries) > MAX_ENTRIES):
            raise ValueError('RAR invalid provenance')
        for index, entry in enumerate(entries):
            _validate_entry(entry, index)
        if data['encrypted'] != 'yes' and any(e['encrypted'] == 'yes' for e in entries):
            raise ValueError('RAR inconsistent encryption status')
    except (ValueError, UnicodeError, RecursionError, TypeError) as exc:
        if str(exc).startswith('RAR worker output error: '):
            raise
        raise ValueError('Invalid RAR worker output') from exc
    return dict(data, image_id=image, review_required=True, completeness='incomplete')


def _validate_entry(entry, index):
    if type(entry) is not dict or set(entry) != {'index', 'path', 'type', 'size', 'encrypted', 'safe'}:
        raise ValueError('RAR invalid entry schema')
    if (type(entry['index']) is not int or entry['index'] != index
            or type(entry['path']) is not str or len(entry['path'].encode('utf-8')) > MAX_PATH_BYTES
            or type(entry['type']) is not str or entry['type'] not in ('file', 'directory')
            or type(entry['size']) is not int or not 0 <= entry['size'] < 2 ** 63
            or type(entry['encrypted']) is not str or entry['encrypted'] not in ('yes', 'no')
            or type(entry['safe']) is not bool or entry['safe'] != safe_path(entry['path'], entry['type'])):
        raise ValueError('RAR invalid entry metadata')


def validate_member_result(output, digest, format_name, image, member_index):
    try:
        if len(output) > MAX_OUTPUT_BYTES:
            raise ValueError('RAR output limit')
        data = json.loads(output.decode('utf-8'), object_pairs_hook=_unique_object)
        fields = {'job', 'source_sha256', 'format', 'encrypted', 'tools', 'entries',
                  'member_index', 'member_sha256', 'member_base64'}
        if type(data) is not dict or set(data) != fields or data['job'] != 'rar_member':
            raise ValueError('RAR invalid member schema')
        if type(data['member_index']) is not int or data['member_index'] != member_index:
            raise ValueError('RAR invalid member index')
        encoded = data['member_base64']
        if type(encoded) is not str or len(encoded) > 4 * ((MAX_MEMBER_BYTES + 2) // 3):
            raise ValueError('RAR encoded member limit')
        metadata = {key: value for key, value in data.items() if not key.startswith('member_')}
        metadata['job'] = 'rar_list'
        validate_result(_encode(metadata), digest, format_name, image)
        entries = data['entries']
        if len(entries) != member_index + 1 or data['encrypted'] == 'yes':
            raise ValueError('RAR invalid member metadata')
        entry = entries[member_index]
        if (entry['type'] != 'file' or any(e['encrypted'] != 'no' for e in entries)
                or sum(e['size'] for e in entries) > MAX_TRAVERSAL_BYTES):
            raise ValueError('RAR unsupported member')
        content = base64.b64decode(encoded, validate=True)
        if (len(content) != entry['size'] or len(content) > MAX_MEMBER_BYTES
                or base64.b64encode(content).decode('ascii') != encoded
                or hashlib.sha256(content).hexdigest() != data['member_sha256']):
            raise ValueError('RAR invalid member bytes')
    except (ValueError, UnicodeError, RecursionError, TypeError, IndexError) as exc:
        raise ValueError('Invalid RAR member worker output') from exc
    return dict(data, member_bytes=content, image_id=image, review_required=True, completeness='incomplete')


def validate_chunk_result(output, digest, format_name, image, member_index, offset):
    try:
        if len(output) > MAX_CHUNK_OUTPUT_BYTES:
            raise ValueError('RAR chunk output limit')
        data = json.loads(output.decode('utf-8'), object_pairs_hook=_unique_object)
        fields = {'job', 'source_sha256', 'format', 'encrypted', 'tools', 'entries',
                  'member_index', 'member_sha256', 'member_base64', 'offset', 'chunk_sha256'}
        if type(data) is not dict or set(data) != fields or data['job'] != 'rar_member_chunk':
            raise ValueError('RAR invalid chunk schema')
        if (type(data['member_index']) is not int or data['member_index'] != member_index
                or type(data['offset']) is not int or data['offset'] != offset
                or type(offset) is not int or not 0 <= offset < MAX_LARGE_MEMBER_BYTES):
            raise ValueError('RAR invalid chunk identity')
        metadata = {key: data[key] for key in
                    ('source_sha256', 'format', 'encrypted', 'tools', 'entries')}
        validate_result(_encode(dict(metadata, job='rar_list')), digest, format_name, image)
        entries = data['entries']
        if (len(entries) != member_index + 1 or data['encrypted'] == 'yes'
                or any(e['encrypted'] != 'no' for e in entries)
                or sum(e['size'] for e in entries) > MAX_TRAVERSAL_BYTES):
            raise ValueError('RAR invalid chunk metadata')
        entry, encoded = entries[member_index], data['member_base64']
        if (entry['type'] != 'file' or entry['size'] > MAX_LARGE_MEMBER_BYTES
                or offset > max(0, entry['size'] - 1) or type(encoded) is not str
                or len(encoded) > 4 * ((CHUNK_BYTES + 2) // 3)):
            raise ValueError('RAR chunk size limit')
        sha = data['member_sha256']
        if type(sha) is not str or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
            raise ValueError('RAR invalid member hash')
        content = base64.b64decode(encoded, validate=True)
        if (len(content) != min(CHUNK_BYTES, entry['size'] - offset)
                or base64.b64encode(content).decode('ascii') != encoded
                or hashlib.sha256(content).hexdigest() != data['chunk_sha256']):
            raise ValueError('RAR invalid chunk bytes')
    except (ValueError, UnicodeError, RecursionError, TypeError, IndexError) as exc:
        raise ValueError('Invalid RAR chunk worker output') from exc
    return dict(data, member_bytes=content, image_id=image, review_required=True, completeness='incomplete')


if __name__ == '__main__':
    args = sys.argv[1:]
    chunk_job = (len(args) == 3 and args[0] == 'rar_member_chunk'
                 and all(a.isascii() and a.isdecimal() and len(a) <= 8 for a in args[1:]))
    if not chunk_job and (len(args) != 2 or not (args == ['rar_list', '1'] or
                          (args[0] == 'rar_member' and args[1].isascii() and args[1].isdecimal()))):
        raise ValueError('Unsupported RAR job or index')
    try:
        result = (read_member_chunk('/input/source', int(args[1]), int(args[2])) if chunk_job else
                  read_member('/input/source', int(args[1])) if args[0] == 'rar_member'
                  else list_metadata('/input/source'))
    except ValueError as exc:
        error = 'invalid_archive'
        if str(exc) == 'RAR encrypted metadata unavailable':
            error = 'encrypted_metadata'
        elif str(exc) == 'RAR format mismatch':
            error = 'format_mismatch'
        result = dict(job=args[0], error=error)
    print(_encode(result).decode('ascii'))

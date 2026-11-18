import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.parser.document_worker import _JOBS, _capture, _source_hash, run_document_job


def _hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _sources(inventory, root):
    records = json.loads(Path(inventory).read_text(encoding='utf-8'))['evidence_records']
    selected = []
    for record in records:
        if record['kind'] not in ('signed_pdf', 'gazette_pdf', 'official_pdf'):
            continue
        relative = Path(record['path'])
        path = root / relative
        if relative.is_absolute() or not path.resolve().is_relative_to(root):
            raise ValueError('Source path escapes project root')
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ValueError('Source symlinks are unsupported')
        count = record['page_count']
        if type(count) is not int or not 1 <= count <= 10000:
            raise ValueError('Invalid inventory page count')
        if _source_hash(path)[1] != record['sha256']:
            raise ValueError('Inventory source hash mismatch')
        selected.append({key: record.get(key) for key in
                         ('path', 'code', 'kind', 'sha256', 'page_count', 'source_url')})
    if not selected or len({r['path'] for r in selected}) != len(selected):
        raise ValueError('Empty or duplicate PDF inventory')
    return selected


def _status(text, job):
    if job == 'pdf_text_page' and len(text.strip()) < 50:
        return 'ocr_required'
    return 'extracted_unreviewed' if text.strip() else 'empty_unreviewed'


def _validate_page(item, sources, image):
    source = sources.get(item['path'])
    if (source is None or type(item['page']) is not int
            or not 1 <= item['page'] <= source['page_count']
            or item['job'] not in ('pdf_text_page', 'ocr_page')
            or item['source_sha256'] != source['sha256'] or item['image_id'] != image):
        raise ValueError('Invalid page provenance')
    if item['status'] == 'failed':
        if not isinstance(item.get('error'), str):
            raise ValueError('Invalid failure record')
        return
    text, tools = item['text'], item['tools']
    if (not isinstance(text, str) or len(text.encode('utf-8')) > 512 * 1024
            or item['text_sha256'] != _hash(text) or not isinstance(tools, dict)
            or set(tools) != _JOBS[item['job']]
            or any(not isinstance(v, str) or not v.strip() or len(v) > 4096 for v in tools.values())
            or item['status'] != _status(text, item['job'])):
        raise ValueError('Invalid page evidence')


def _load(output, sources, image):
    report = dict(schema_version=1, image_id=image, sources=sources, pages=[],
                  review_required=True, completeness='incomplete')
    if not output.exists():
        return report
    try:
        saved = json.loads(output.read_text(encoding='utf-8'))
        if any(saved[key] != report[key] for key in report if key != 'pages'):
            raise ValueError('Checkpoint identity mismatch')
        index = {r['path']: r for r in sources}
        seen = set()
        for item in saved['pages']:
            _validate_page(item, index, image)
            key = (item['path'], item['job'], item['page'])
            if key in seen:
                raise ValueError('Duplicate checkpoint page')
            seen.add(key)
        return saved
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError('Invalid checkpoint') from exc


def _save(output, report):
    if output.is_symlink() or not output.parent.is_dir():
        raise ValueError('Unsafe or missing checkpoint directory')
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix=output.name + '.', suffix='.tmp', delete=False) as stream:
            name = stream.name
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, output)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _extract(source, root, job, page, image, runner):
    base = dict(path=source['path'], job=job, page=page,
                source_sha256=source['sha256'], image_id=image)
    try:
        result = runner(root / source['path'], job=job, page=page)
    except (RuntimeError, OSError, ValueError) as exc:
        return dict(base, status='failed', error=type(exc).__name__)
    if any(result.get(key) != base[key] for key in ('job', 'page', 'source_sha256', 'image_id')):
        raise ValueError('Worker identity changed')
    text = result['text']
    item = dict(base, text=text, text_sha256=_hash(text), tools=result['tools'],
                status=_status(text, job))
    _validate_page(item, {source['path']: source}, image)
    return item


def run_batch(inventory, output, *, root, image_id, runner=run_document_job,
              job='pdf_text_page', paths=None, pages=None, max_pages=10, retry_failed=False):
    if (re.fullmatch(r'sha256:[0-9a-f]{64}', image_id) is None
            or job not in ('pdf_text_page', 'ocr_page')
            or type(max_pages) is not int or max_pages < 1):
        raise ValueError('Invalid batch parameters')
    root, output = Path(root).resolve(), Path(output)
    if (not output.resolve().is_relative_to(root)
            or any(p.is_symlink() for p in (output, *output.parents))):
        raise ValueError('Unsafe checkpoint path')
    sources = _sources(inventory, root)
    if paths and not set(paths) <= {s['path'] for s in sources}:
        raise ValueError('Unknown PDF path selection')
    if pages is not None and (not pages or any(type(p) is not int or p < 1 for p in pages)):
        raise ValueError('Invalid page selection')
    if output.resolve() == Path(inventory).resolve() or output.resolve() in {
            (root / s['path']).resolve() for s in sources}:
        raise ValueError('Checkpoint would overwrite source')
    report = _load(output, sources, image_id)
    index = {(p['path'], p['job'], p['page']): i for i, p in enumerate(report['pages'])}
    attempted = 0
    for source in sources:
        if paths and source['path'] not in paths:
            continue
        if pages and max(pages) > source['page_count']:
            raise ValueError('Page selection exceeds declared page count')
        for page in sorted(set(pages)) if pages else range(1, source['page_count'] + 1):
            key = (source['path'], job, page)
            old = index.get(key)
            if old is not None and (not retry_failed or report['pages'][old]['status'] != 'failed'):
                continue
            item = _extract(source, root, job, page, image_id, runner)
            if old is None:
                index[key] = len(report['pages'])
                report['pages'].append(item)
            else:
                report['pages'][old] = item
            _save(output, report)
            attempted += 1
            if attempted >= max_pages:
                return report
    _save(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description='Checkpoint PDF evidence; extraction never grants review approval.')
    parser.add_argument('--job', choices=['pdf_text_page', 'ocr_page'], default='pdf_text_page')
    parser.add_argument('--path', action='append')
    parser.add_argument('--page', action='append', type=int)
    parser.add_argument('--max-pages', type=int, default=10)
    parser.add_argument('--retry-failed', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    image = _capture(['docker', 'image', 'inspect', '--format', '{{.Id}}',
                      'vat-document-worker:local'], timeout=10).decode('ascii').strip()
    report = run_batch(root / 'data/source_inventory.json',
                       root / 'data/processed/document_pdf_coverage.json', root=root,
                       image_id=image, job=args.job, paths=args.path, pages=args.page,
                       max_pages=args.max_pages, retry_failed=args.retry_failed)
    counts = {}
    for item in report['pages']:
        counts[item['status']] = counts.get(item['status'], 0) + 1
    print(json.dumps(dict(records=len(report['pages']), statuses=counts,
                         review_required=True), ensure_ascii=False))


if __name__ == '__main__':
    main()

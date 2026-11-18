import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def batch():
    path = Path(__file__).resolve().parents[1] / 'scripts' / 'batch_pdf_coverage.py'
    assert path.exists(), 'Resumable PDF coverage runner is missing'
    spec = importlib.util.spec_from_file_location('pdf_batch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def inputs(tmp_path):
    source = tmp_path / 'scan.pdf'
    source.write_bytes(b'%PDF-1.4 test')
    record = dict(path='scan.pdf', kind='signed_pdf', code='test', page_count=3,
                  sha256=hashlib.sha256(source.read_bytes()).hexdigest())
    inventory = tmp_path / 'inventory.json'
    inventory.write_text(json.dumps({'evidence_records': [record]}), encoding='utf-8')
    return tmp_path, inventory, tmp_path / 'coverage.json', record


IMAGE = 'sha256:' + 'a' * 64


def worker(record, calls, *, text='Tiếng Việt 1.000 đồng'):
    def run(source, *, job, page):
        calls.append((job, page))
        return dict(text=text, tools={key: '1.0' for key in ({'pdftotext'} if job == 'pdf_text_page' else {'pdftoppm', 'tesseract'})}, job=job, page=page,
                    source_sha256=record['sha256'], image_id=IMAGE)
    return run


def test_checkpoints_each_page_and_resumes_without_reprocessing(batch, inputs):
    root, inventory, output, record = inputs
    calls = []
    run = worker(record, calls)
    report = batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=run, max_pages=1)
    assert calls == [('pdf_text_page', 1)]
    assert json.loads(output.read_text(encoding='utf-8')) == report
    report = batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=run, max_pages=9)
    assert calls == [('pdf_text_page', 1), ('pdf_text_page', 2), ('pdf_text_page', 3)]
    assert report['review_required'] is True
    assert report['completeness'] == 'incomplete'
    assert len(report['pages']) == 3
    assert report['pages'][0]['text_sha256'] == hashlib.sha256('Tiếng Việt 1.000 đồng'.encode()).hexdigest()


def test_empty_text_requires_ocr_and_explicit_ocr_preserves_text_evidence(batch, inputs):
    root, inventory, output, record = inputs
    batch.run_batch(inventory, output, root=root, image_id=IMAGE,
                    runner=worker(record, [], text=''), max_pages=1)
    report = batch.run_batch(inventory, output, root=root, image_id=IMAGE,
                             runner=worker(record, []), job='ocr_page', pages=[1], max_pages=1)
    assert [p['status'] for p in report['pages']] == ['ocr_required', 'extracted_unreviewed']
    assert report['pages'][0]['text'] == ''


def test_interruption_leaves_last_page_durable(batch, inputs):
    root, inventory, output, record = inputs
    good = worker(record, [])
    def interrupted(source, *, job, page):
        if page == 2:
            raise KeyboardInterrupt()
        return good(source, job=job, page=page)
    with pytest.raises(KeyboardInterrupt):
        batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=interrupted)
    assert len(json.loads(output.read_text(encoding='utf-8'))['pages']) == 1


@pytest.mark.parametrize('tamper', ['text', 'image', 'source', 'duplicate', 'tools', 'wrong_tools'])
def test_resume_rejects_changed_evidence(batch, inputs, tamper):
    root, inventory, output, record = inputs
    batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=worker(record, []), max_pages=1)
    report = json.loads(output.read_text(encoding='utf-8'))
    if tamper == 'text':
        report['pages'][0]['text'] = 'changed'
    elif tamper == 'image':
        report['image_id'] = 'sha256:' + 'b' * 64
    elif tamper == 'source':
        (root / record['path']).write_bytes(b'changed')
    elif tamper == 'duplicate':
        report['pages'].append(report['pages'][0])
    elif tamper == 'wrong_tools':
        report['pages'][0]['tools'] = {'unrelated': '1.0'}
    else:
        report['pages'][0]['tools'] = {}
    output.write_text(json.dumps(report), encoding='utf-8')
    with pytest.raises(ValueError):
        batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=worker(record, []))


def test_worker_failure_is_recorded_and_retried(batch, inputs):
    root, inventory, output, record = inputs
    def failure(*args, **kwargs):
        raise RuntimeError('worker failed')
    report = batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=failure, max_pages=1)
    assert report['pages'][0]['status'] == 'failed'
    report = batch.run_batch(inventory, output, root=root, image_id=IMAGE,
                             runner=worker(record, []), max_pages=1, retry_failed=True)
    assert report['pages'][0]['status'] == 'ocr_required'


def test_wrong_worker_identity_is_not_saved_as_success(batch, inputs):
    root, inventory, output, record = inputs
    def bad(*args, **kwargs):
        return dict(worker(record, [])(*args, **kwargs), image_id='sha256:' + 'b' * 64)
    with pytest.raises(ValueError):
        batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=bad)
    assert not output.exists() or not json.loads(output.read_text(encoding='utf-8'))['pages']


def test_persistent_failure_does_not_starve_unseen_pages(batch, inputs):
    root, inventory, output, record = inputs
    calls = []
    def failure(source, *, job, page):
        calls.append(page)
        raise RuntimeError('failed')
    for _ in range(3):
        batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=failure, max_pages=1)
    assert calls == [1, 2, 3]


def test_output_cannot_escape_root_before_worker_runs(batch, inputs):
    root, inventory, output, record = inputs
    calls = []
    with pytest.raises(ValueError, match='checkpoint path'):
        batch.run_batch(inventory, root.parent / 'escaped.json', root=root,
                        image_id=IMAGE, runner=worker(record, calls))
    assert calls == []


def test_resume_preserves_sample_review_notes(batch, inputs):
    root, inventory, output, record = inputs
    batch.run_batch(inventory, output, root=root, image_id=IMAGE,
                    runner=worker(record, []), max_pages=1)
    report = json.loads(output.read_text(encoding='utf-8'))
    report['sample_review'] = {'status': 'not_approved', 'observations': ['table scrambled']}
    output.write_text(json.dumps(report), encoding='utf-8')
    resumed = batch.run_batch(inventory, output, root=root, image_id=IMAGE,
                              runner=worker(record, []), max_pages=1)
    assert resumed['sample_review'] == report['sample_review']


def test_empty_ocr_remains_explicitly_unresolved(batch, inputs):
    root, inventory, output, record = inputs
    report = batch.run_batch(inventory, output, root=root, image_id=IMAGE,
                             runner=worker(record, [], text=''), job='ocr_page', max_pages=1)
    assert report['pages'][0]['status'] == 'empty_unreviewed'


@pytest.mark.parametrize('path', ['../outside.pdf', '/outside.pdf'])
def test_inventory_cannot_escape_root(batch, inputs, path):
    root, inventory, output, record = inputs
    record['path'] = path
    inventory.write_text(json.dumps({'evidence_records': [record]}), encoding='utf-8')
    with pytest.raises(ValueError):
        batch.run_batch(inventory, output, root=root, image_id=IMAGE, runner=worker(record, []))

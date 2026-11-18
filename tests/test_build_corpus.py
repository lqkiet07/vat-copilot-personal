import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def builder():
    path = Path(__file__).resolve().parents[1] / 'scripts/build_corpus.py'
    assert 'def build_corpus(' in path.read_text(encoding='utf-8'), 'Missing guarded corpus builder'
    spec = importlib.util.spec_from_file_location('corpus_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, 'build_corpus'), 'Missing guarded corpus builder'
    return module


@pytest.fixture
def inputs(tmp_path):
    html = '<html><body><p>Điều 1. Phạm vi</p><p>1. Nội dung áp dụng.</p></body></html>'
    source = tmp_path / 'source.html'
    source.write_text(html, encoding='utf-8')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    inventory = dict(evidence_records=[dict(path='source.html', code='144/2026/NĐ-CP',
                     kind='html', sha256=digest, source_url=None)], documents=[dict(
                     id='ND_144_2026', code='144/2026/NĐ-CP', title='Nghị định',
                     issued_on='2026-05-05', effective_on='2026-06-20',
                     source_paths=['source.html'], corpus_review_status='approved',
                      provision_effective_dates=[], temporal_review_status='approved')])
    path = tmp_path / 'inventory.json'
    path.write_text(json.dumps(inventory), encoding='utf-8')
    output = tmp_path / 'corpus.json'
    output.write_text('OLD CORPUS', encoding='utf-8')
    return tmp_path, path, output, inventory


def test_uses_inventory_dates_and_preserves_source_provenance(builder, inputs):
    root, inventory, output, _ = inputs
    result = builder.build_corpus(inventory, root=root)
    assert result[0]['valid_from'] == '2026-06-20'
    assert result[0]['issue_date'] == '2026-05-05'
    assert result[0]['provenance']['sources'][0]['path'] == 'source.html'
    assert result[0]['provenance']['sources'][0]['source_url'] is None
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'


@pytest.mark.parametrize('issue', ['hash', 'review', 'metadata', 'scan', 'path', 'unresolved', 'date'])
def test_invalid_inputs_never_replace_published_corpus(builder, inputs, issue):
    root, inventory, output, data = inputs
    if issue == 'hash':
        (root / 'source.html').write_text('changed', encoding='utf-8')
    elif issue == 'review':
        data['documents'][0].pop('corpus_review_status')
    elif issue == 'metadata':
        data['documents'] = []
    elif issue == 'scan':
        data['evidence_records'][0]['kind'] = 'signed_pdf'
    elif issue == 'path':
        data['evidence_records'][0]['path'] = '../outside.html'
        data['documents'][0]['source_paths'] = ['../outside.html']
    elif issue == 'unresolved':
        source = root / 'source.html'
        source.write_text('<html><body><p>Không có điều khoản</p></body></html>', encoding='utf-8')
        data['evidence_records'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    else:
        data['documents'][0]['effective_on'] = '2026-02-30'
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        builder.build_corpus(inventory, root=root, output=output)
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'


@pytest.mark.parametrize('exceptions', [[{'provisions': ['Article 1'], 'effective_on': '2026-01-01'}], None, {}])
def test_blocks_unmapped_provision_dates(builder, inputs, exceptions):
    root, inventory, output, data = inputs
    data['documents'][0]['provision_effective_dates'] = exceptions
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='provision'):
        builder.build_corpus(inventory, root=root, output=output)
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'


def test_maps_effective_date_only_to_exact_clause_and_descendants(builder, inputs):
    root, inventory, output, data = inputs
    source = root / 'source.html'
    source.write_text('<html><body><p>Điều 1. Phạm vi</p><p>1. Quy định một.</p>'
                      '<p>a) Chi tiết.</p><p>2. Quy định hai.</p></body></html>', encoding='utf-8')
    data['evidence_records'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    data['documents'][0]['provision_effective_dates'] = [dict(
        target_ids=['ND_144_2026_D1_K1'], effective_on='2026-01-01',
        mapping_status='approved', evidence='Article 2(2)')]
    inventory.write_text(json.dumps(data), encoding='utf-8')
    result = builder.build_corpus(inventory, root=root)[0]
    article = result['articles'][0]
    assert result['valid_from'] == article['valid_from'] == '2026-06-20'
    assert article['clauses'][0]['valid_from'] == '2026-01-01'
    assert article['clauses'][0]['points'][0]['valid_from'] == '2026-01-01'
    assert article['clauses'][1]['valid_from'] == '2026-06-20'


@pytest.mark.parametrize('rule', [
    dict(target_ids=['missing'], effective_on='2026-01-01', mapping_status='approved', evidence='Article2'),
    dict(target_ids=['ND_144_2026_D1'], effective_on='2026-02-30', mapping_status='approved', evidence='Article2'),
    dict(target_ids=['ND_144_2026_D1'], effective_on='2026-01-01', mapping_status='approved'),
    dict(target_ids=['ND_144_2026_D1'], effective_until_inclusive='2026-01-01', mapping_status='approved', evidence='Article2'),
])
def test_rejects_invalid_or_unresolved_effective_date_rules(builder, inputs, rule):
    root, inventory, output, data = inputs
    data['documents'][0]['provision_effective_dates'] = [rule]
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError):
        builder.build_corpus(inventory, root=root, output=output)
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'


@pytest.mark.parametrize('field,value,reason', [
    ('conditional_transitions', [{'satisfied_on': None}], 'conditional'),
    ('effective_from_local', '2026-03-27T00:00:00', 'time-aware'),
    ('document_role', 'consolidation_crosscheck_not_independent_amendment', 'consolidation'),
    ('unresolved_references', [{'status': 'unresolved'}], 'references'),
    ('temporal_review_status', 'pending_transitions', 'temporal review'),
    ('original_application_end_on', '2026-01-01', 'application end'),
    ('valid_to', '2026-12-31', 'expiry'),
    ('effective_until_inclusive', '2026-12-31', 'expiry'),
    ('applies_through', '2026-12-31', 'expiry'),
])
def test_temporal_metadata_cannot_be_silently_dropped(builder, inputs, field, value, reason):
    root, inventory, output, data = inputs
    data['documents'][0][field] = value
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match=reason):
        builder.build_corpus(inventory, root=root, output=output)
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'


def test_missing_temporal_review_is_not_approval(builder, inputs):
    root, inventory, output, data = inputs
    data['documents'][0].pop('temporal_review_status')
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='temporal review'):
        builder.build_corpus(inventory, root=root, output=output)
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'


def test_overlapping_rules_are_rejected_without_publication(builder, inputs):
    root, inventory, output, data = inputs
    data['documents'][0]['provision_effective_dates'] = [
        dict(target_ids=['ND_144_2026_D1'], effective_on='2026-01-01',
             mapping_status='approved', evidence='Article2'),
        dict(target_ids=['ND_144_2026_D1_K1'], effective_on='2026-02-01',
             mapping_status='approved', evidence='Article3')]
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='overlap'):
        builder.build_corpus(inventory, root=root, output=output)
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'


def test_real_law09_retroactive_article_does_not_change_other_articles(builder):
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / 'data/source_inventory.json').read_text(encoding='utf-8'))
    metadata = next(d for d in inventory['documents'] if d['id'] == 'LUAT_09_2026')
    snapshot = json.loads((root / metadata['snapshot_path']).read_text(encoding='utf-8'))
    parsed = builder.parse_vbpl_document(snapshot['html'], dict(
        doc_id=metadata['id'], code=metadata['code'], title=metadata['title'],
        valid_from=metadata['effective_on'], valid_to='9999-12-31', status='UNVERIFIED'))
    original = {a['id']: a['valid_from'] for a in parsed['articles']}
    builder._apply_provision_dates(parsed, [dict(target_ids=['LUAT_09_2026_D2'],
        effective_on='2026-01-01', mapping_status='approved', evidence='Article 5(2)')])
    article = next(a for a in parsed['articles'] if a['id'] == 'LUAT_09_2026_D2')
    assert article['valid_from'] == '2026-01-01'
    assert all(c['valid_from'] == '2026-01-01' for c in article['clauses'])
    assert all(a['valid_from'] == original[a['id']] for a in parsed['articles']
               if a['id'] != 'LUAT_09_2026_D2')
    assert len(original) > 1


def test_supports_primary_body_with_hash_verified_attachments(builder, inputs):
    root, inventory, output, data = inputs
    annex = root / 'annex.pdf'
    annex.write_bytes(b'%PDF-1.4 annex')
    annex_hash = hashlib.sha256(annex.read_bytes()).hexdigest()
    data['evidence_records'].append(dict(path='annex.pdf', code='144/2026/NĐ-CP',
                                         kind='gazette_pdf', sha256=annex_hash, source_url=None))
    data['documents'][0]['source_paths'] = ['source.html', 'annex.pdf']
    data['documents'][0]['primary_body_source'] = 'source.html'
    data['documents'][0]['attachment_sources'] = ['annex.pdf']
    inventory.write_text(json.dumps(data), encoding='utf-8')
    result = builder.build_corpus(inventory, root=root)[0]
    assert len(result['provenance']['sources']) == 2
    assert result['provenance']['primary_body_source'] == 'source.html'


def test_supports_document_and_provision_expiry_plus_clause_span_dates(builder, inputs):
    root, inventory, output, data = inputs
    source = root / 'source.html'
    source.write_text(
        '<html><body><p>Điều 5. Đối tượng</p>'
        '<p>25. Hàng hóa, dịch vụ của hộ, cá nhân sản xuất, kinh doanh có mức doanh thu hằng năm từ 200 triệu đồng trở xuống; tài sản của tổ chức, cá nhân không kinh doanh.</p>'
        '<p>Điều 15. Giao dịch</p><p>1. Quy định tạm thời.</p></body></html>',
        encoding='utf-8',
    )
    data['evidence_records'][0]['sha256'] = hashlib.sha256(source.read_bytes()).hexdigest()
    data['documents'][0]['original_application_end_on'] = '2026-12-31'
    data['documents'][0]['provision_effective_dates'] = [
        dict(
            scope_kind='text_span_within_clause',
            container_id='ND_144_2026_D5_K25',
            subject='Hàng hóa, dịch vụ của hộ, cá nhân sản xuất, kinh doanh có mức doanh thu hằng năm từ 200 triệu đồng trở xuống',
            effective_on='2026-01-01',
            mapping_status='approved',
            evidence='Article 18(2)',
        ),
        dict(
            target_ids=['ND_144_2026_D15_K1'],
            effective_until_inclusive='2026-09-30',
            mapping_status='approved',
            evidence='Article 74(2)',
        ),
    ]
    inventory.write_text(json.dumps(data), encoding='utf-8')
    result = builder.build_corpus(inventory, root=root)[0]
    assert result['valid_to'] == '2026-12-31'
    clause_25 = result['articles'][0]['clauses'][0]
    assert clause_25['valid_from'] == '2026-06-20'
    assert clause_25['provision_spans'][0]['valid_from'] == '2026-01-01'
    assert result['articles'][1]['clauses'][0]['valid_to'] == '2026-09-30'


def test_staging_corpus_assembles_all_33_documents_with_pdf_and_consolidation_adapters(builder):
    root = Path(__file__).resolve().parents[1]
    corpus = builder.build_staging_corpus(
        root / 'data/source_inventory.json',
        root=root,
        pdf_coverage_path=root / 'data/processed/document_pdf_coverage.json',
    )
    assert len(corpus) == 33
    by_id = {d['id']: d for d in corpus}
    assert by_id['ND_144_2026']['valid_from'] == '2026-06-20'
    assert by_id['ND_144_2026']['issue_date'] == '2026-05-05'
    assert by_id['ND_174_2025']['valid_from'] == '2025-07-01'
    assert by_id['ND_174_2025']['valid_to'] == '2026-12-31'
    assert len(by_id['ND_174_2025']['articles']) >= 2
    assert len(by_id['QD_482_2026']['articles']) == 2
    assert by_id['VBHN_25_2026']['status'] == 'CONSOLIDATION_REFERENCE'
    assert len(by_id['VBHN_25_2026']['articles']) >= 15


def test_validated_build_can_publish_atomically(builder, inputs):
    root, inventory, output, _ = inputs
    result = builder.build_corpus(inventory, root=root, output=output)
    assert json.loads(output.read_text(encoding='utf-8')) == result


def test_rejects_declared_but_unselected_attachments(builder, inputs):
    root, inventory, output, data = inputs
    data['evidence_records'].append(dict(path='annex.pdf', code='144/2026/NĐ-CP',
                                       kind='signed_pdf', sha256='a' * 64))
    inventory.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='coverage'):
        builder.build_corpus(inventory, root=root, output=output)
    assert output.read_text(encoding='utf-8') == 'OLD CORPUS'

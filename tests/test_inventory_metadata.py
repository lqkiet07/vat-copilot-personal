import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
CODES = ['48/2024/QH15', '181/2025/NĐ-CP', '69/2025/TT-BTC', '359/2025/NĐ-CP',
         '108/2025/QH15', '05/2025/NQ-CP', '72/2014/TT-BTC', '92/2019/TT-BTC',
         '41/2026/TT-BTC', '18/2026/TT-BTC', '19/2026/QH16', '204/2025/QH15',
         '245/2026/NĐ-CP', '252/2026/NĐ-CP', '2551/QĐ-BTC', '32/2026/TT-BTC',
         '50/2026/TT-BTC', '84/2026/TT-BTC', '89/2026/TT-BTC', '97/2026/TT-BTC']


class Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def normalize(text):
    import unicodedata

    return re.sub(r'\s+', '', unicodedata.normalize('NFC', text)).casefold()


@pytest.mark.parametrize('code', CODES)
def test_normalized_metadata_has_hash_pinned_sources_and_verbatim_date_evidence(code):
    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    doc = next(d for d in inventory['documents'] if d['code'] == code)
    records = {r['path']: r for r in inventory['evidence_records'] if r.get('code') == code}
    assert set(doc['source_paths']) == set(records)
    for path, record in records.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == record['sha256']
    assert doc['corpus_review_status'] == 'pending'
    assert doc['import_status'] == 'not_imported'
    evidence = doc['metadata_evidence']
    raw = (ROOT / evidence['path']).read_text(encoding='utf-8')
    html = json.loads(raw)['html'] if evidence['path'].endswith('.json') else raw
    parser = Text()
    parser.feed(html)
    text = normalize(' '.join(parser.parts))
    assert normalize(evidence['effective_excerpt']) in text
    if not evidence.get('issued_source_path'):
        assert normalize(evidence['issued_excerpt']) in text
    else:
        coverage = json.loads((ROOT / 'data/processed/document_pdf_coverage.json').read_text(encoding='utf-8'))
        page = next(p for p in coverage['pages'] if p['path'] == evidence['issued_source_path']
                    and p['job'] == 'pdf_text_page' and p['page'] == evidence['issued_source_page'])
        assert page['source_sha256'] == records[evidence['issued_source_path']]['sha256']
        assert normalize(evidence['issued_excerpt']) in normalize(page['text'])


@pytest.mark.parametrize('code', ['174/2025/NĐ-CP', '77/2025/TT-BTC', '101/2025/TT-BNNMT',
                                  '34/2026/NQ-CP', '43/2026/NQ-CP'])
def test_pdf_metadata_matches_exact_page_text_and_source_hash(code):
    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    coverage = json.loads((ROOT / 'data/processed/document_pdf_coverage.json').read_text(encoding='utf-8'))
    doc = next(d for d in inventory['documents'] if d['code'] == code)
    evidence = doc['metadata_evidence']
    record = next(r for r in inventory['evidence_records'] if r['path'] == evidence['path'])
    assert hashlib.sha256((ROOT / evidence['path']).read_bytes()).hexdigest() == record['sha256']
    for field in ('issued', 'effective'):
        page = next(p for p in coverage['pages'] if p['path'] == evidence['path']
                    and p['job'] == 'pdf_text_page' and p['page'] == evidence[field + '_page'])
        assert page['source_sha256'] == record['sha256']
        assert normalize(evidence[field + '_excerpt']) in normalize(page['text'])


def test_all_declared_documents_have_complete_source_mapping_without_publication_approval():
    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    codes = {r['code'] for r in inventory['evidence_records'] if r.get('code')}
    docs = inventory['documents']
    assert len(docs) == len(codes) == 33
    assert {d['code'] for d in docs} == codes
    assert len({d['id'] for d in docs}) == len(docs)
    for doc in docs:
        records = [r for r in inventory['evidence_records'] if r.get('code') == doc['code']]
        assert set(doc['source_paths']) == {r['path'] for r in records}
        assert doc['corpus_review_status'] == 'pending'
        for record in records:
            assert hashlib.sha256((ROOT / record['path']).read_bytes()).hexdigest() == record['sha256']


def test_inventory_law09_retroactivity_maps_all_three_articles_without_changing_others():
    from scripts.build_corpus import _apply_provision_dates, parse_vbpl_document

    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    doc = next(d for d in inventory['documents'] if d['id'] == 'LUAT_09_2026')
    snapshot = json.loads((ROOT / doc['snapshot_path']).read_text(encoding='utf-8'))
    rule, = doc['provision_effective_dates']
    assert rule['target_ids'] == ['LUAT_09_2026_D1', 'LUAT_09_2026_D2', 'LUAT_09_2026_D3']
    assert rule['mapping_status'] == 'approved'
    assert rule['evidence'] == 'Article 5(2)'
    text = Text()
    text.feed(snapshot['html'])
    assert normalize(doc['temporal_mapping_evidence']['excerpt']) in normalize(' '.join(text.parts))
    assert hashlib.sha256((ROOT / doc['snapshot_path']).read_bytes()).hexdigest() == doc['snapshot_sha256']
    parsed = parse_vbpl_document(snapshot['html'], dict(doc_id=doc['id'], code=doc['code'],
        title=doc['title'], valid_from=doc['effective_on'], valid_to='9999-12-31', status='UNVERIFIED'))
    _apply_provision_dates(parsed, doc['provision_effective_dates'])
    assert parsed['valid_from'] == '2026-04-24'
    assert len(parsed['articles']) == 5
    for article in parsed['articles']:
        expected = '2026-01-01' if article['id'] in rule['target_ids'] else '2026-04-24'
        assert article['valid_from'] == expected
        for clause in article['clauses']:
            assert clause['valid_from'] == expected
            assert all(point['valid_from'] == expected for point in clause['points'])
    assert doc['corpus_review_status'] == 'pending'


def test_law48_threshold_exception_never_becomes_whole_clause_mapping():
    from scripts.build_corpus import _apply_provision_dates, parse_vbpl_document

    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    doc = next(d for d in inventory['documents'] if d['id'] == 'LUAT_48_2024')
    rules = doc['provision_effective_dates']
    article = next(r for r in rules if r.get('target_ids') == ['LUAT_48_2024_D17'])
    assert article['effective_on'] == '2026-01-01'
    span_rule = next(r for r in rules if r.get('scope_kind') == 'text_span_within_clause')
    assert span_rule['container_id'] == 'LUAT_48_2024_D5_K25'
    assert span_rule['mapping_status'] == 'approved'
    assert not span_rule.get('target_ids')
    assert all('LUAT_48_2024_D5_K25' not in r.get('target_ids', []) for r in rules)
    html = (ROOT / doc['metadata_evidence']['path']).read_text(encoding='utf-8')
    parser = Text()
    parser.feed(html)
    assert normalize(doc['temporal_mapping_evidence']['excerpt']) in normalize(' '.join(parser.parts))
    parsed = parse_vbpl_document(html, dict(doc_id=doc['id'], code=doc['code'], title=doc['title'],
                                            valid_from=doc['effective_on'], valid_to='9999-12-31', status='UNVERIFIED'))
    _apply_provision_dates(parsed, rules)
    k25 = next(c for a in parsed['articles'] if a['id'] == 'LUAT_48_2024_D5' for c in a['clauses'] if c['id'] == 'LUAT_48_2024_D5_K25')
    assert k25['valid_from'] == '2025-07-01'
    assert k25['provision_spans'][0]['valid_from'] == '2026-01-01'


def test_law108_and_nd252_provision_mappings_apply_cleanly():
    from scripts.build_corpus import _apply_provision_dates, parse_vbpl_document

    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    for doc_id in ('LUAT_108_2025', 'ND_252_2026'):
        doc = next(d for d in inventory['documents'] if d['id'] == doc_id)
        raw = json.loads((ROOT / doc['primary_body_source']).read_text(encoding='utf-8'))['html']
        parsed = parse_vbpl_document(raw, dict(doc_id=doc['id'], code=doc['code'], title=doc['title'],
                                               valid_from=doc['effective_on'], valid_to='9999-12-31', status='UNVERIFIED'))
        _apply_provision_dates(parsed, doc['provision_effective_dates'])
        if doc_id == 'LUAT_108_2025':
            d13 = next(a for a in parsed['articles'] if a['id'] == 'LUAT_108_2025_D13')
            assert d13['valid_from'] == '2026-01-01'
            k3b = next(p for a in parsed['articles'] if a['id'] == 'LUAT_108_2025_D26'
                       for c in a['clauses'] if c['id'] == 'LUAT_108_2025_D26_K3'
                       for p in c['points'] if p['id'] == 'LUAT_108_2025_D26_K3_b')
            assert k3b['valid_from'] == '2026-07-01'
            assert k3b['provision_spans'][0]['valid_from'] == '2026-01-01'
        else:
            pa = next(p for a in parsed['articles'] if a['id'] == 'ND_252_2026_D15'
                      for c in a['clauses'] if c['id'] == 'ND_252_2026_D15_K2'
                      for p in c['points'] if p['id'] == 'ND_252_2026_D15_K2_a')
            assert pa['valid_to'] == '9999-12-31'
            assert pa['provision_spans'][0]['valid_to'] == '2026-12-31'


def test_consolidation_has_no_fabricated_commencement_and_fuel_decision_keeps_time_boundary():
    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    docs = {d['id']: d for d in inventory['documents']}
    consolidated = docs['VBHN_25_2026']
    assert consolidated['effective_on'] is None
    text = json.loads((ROOT / consolidated['metadata_evidence']['path']).read_text(encoding='utf-8'))['text']
    assert normalize(consolidated['metadata_evidence']['issued_excerpt']) in normalize(text)
    fuel = docs['QD_482_2026']
    assert fuel['effective_on'] is None
    assert fuel['effective_from_local'] == '2026-03-27T00:00:00'
    assert fuel['effective_until_local_exclusive'] == '2026-04-16T00:00:00'
    evidence = fuel['metadata_evidence']
    assert hashlib.sha256((ROOT / evidence['image_path']).read_bytes()).hexdigest() == evidence['image_sha256']
    coverage = json.loads((ROOT / 'data/processed/document_pdf_coverage.json').read_text(encoding='utf-8'))
    reviewed = coverage['sample_review']['qd482_selected_review']['verified_fields']
    assert reviewed['issued_on'] == fuel['issued_on']
    assert evidence['effective_excerpt'] in reviewed['article_2_clause_1']

import hashlib
from html.parser import HTMLParser
from functools import lru_cache
import re
import json
from pathlib import Path
import sys
from copy import deepcopy

import pytest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/amendment_review.staging.json'


class SourceText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@lru_cache(maxsize=None)
def source_text(path):
    if path.suffix == '.pdf':
        from pypdf import PdfReader

        return ' '.join(' '.join(page.extract_text() for page in PdfReader(path).pages).split())
    raw = path.read_text(encoding='utf-8')
    if path.suffix == '.json':
        snapshot = json.loads(raw)
        if 'html' not in snapshot:
            return ' '.join(snapshot.get('text', snapshot.get('innerText', '')).split())
        raw = snapshot['html']
    parser = SourceText()
    parser.feed(raw)
    return ' '.join(' '.join(parser.parts).split())


def load_ledger():
    assert FIXTURE.is_file(), 'R1 independently reviewed staging ledger is missing'
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def test_review_ledger_exists_without_production_oracle():
    ledger = load_ledger()
    assert ledger['schema_version'] == 1
    assert ledger['oracle'] == 'independent_raw_source_review'
    assert ledger['publication_approved'] is False


def test_inventory_and_corpus_are_hash_pinned():
    ledger = load_ledger()
    actual_paths = {p.relative_to(ROOT).as_posix() for p in (ROOT / 'data').rglob('*') if p.is_file()}
    assert ledger['pins'].keys() == actual_paths
    for path, expected in ledger['pins'].items():
        assert digest(ROOT / path) == expected, path
    assert {'data/source_inventory.json', 'data/processed/vat_corpus.staging.json'} <= ledger['pins'].keys()


def test_all_33_documents_have_explicit_review_coverage():
    ledger = load_ledger()
    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    docs = {doc['id']: doc for doc in inventory['documents']}
    coverage = ledger['documents']
    assert len(coverage) == len(docs) == 33
    assert {doc['doc_id'] for doc in coverage} == docs.keys()
    for doc in coverage:
        assert doc['review_status'] in {'reviewed', 'partial', 'not_reviewed'}
        assert doc['source_paths'] == docs[doc['doc_id']]['source_paths']
        assert doc['date_evidence'] == docs[doc['doc_id']].get('metadata_evidence')
        assert doc['review_status'] == 'reviewed' or doc['gaps']
        assert set(doc['candidate_ids']) == {c['id'] for c in ledger['candidates'] if c['source_doc'] == doc['doc_id']}


def test_candidate_evidence_is_verbatim_and_pinned():
    ledger = load_ledger()
    ids = set()
    for item in ledger['candidates']:
        assert item['id'] not in ids
        ids.add(item['id'])
        assert item['classification'] in {'resolved', 'unresolved', 'nonoperative', 'outscope'}
        assert item['source_unit'] and item['source_field'] and item['reason']
        evidence = item['evidence']
        assert digest(ROOT / evidence['path']) == evidence['sha256']
        text = source_text(ROOT / evidence['path'])
        assert evidence['excerpt'] and evidence['excerpt'] in text
        assert evidence['parent_context'] and evidence['parent_context'] in text
        start = evidence['locator']['normalized_text_start']
        assert text[start:start + len(evidence['excerpt'])] == evidence['excerpt']
        assert item['source_effective_intervals']
        if item['classification'] != 'resolved':
            assert item['expected']['emit_resolved'] is False


def test_resolved_targets_exist_at_exact_requested_granularity():
    ledger = load_ledger()
    corpus = json.loads((ROOT / 'data/processed/vat_corpus.staging.json').read_text(encoding='utf-8'))
    docs = {doc['id']: doc for doc in corpus}
    for item in ledger['candidates']:
        if item['classification'] != 'resolved':
            continue
        expected = item['expected']
        assert expected['emit_resolved'] is True
        assert expected['action'] and expected['scope'] and expected['targets']
        for target in expected['targets']:
            doc = docs[target['doc_id']]
            article = next(a for a in doc['articles'] if a['id'] == target['article_id'])
            if target.get('clause_id'):
                clause = next(c for c in article['clauses'] if c['id'] == target['clause_id'])
                if target.get('point_id'):
                    assert any(p['id'] == target['point_id'] for p in clause['points'])


def test_dates_and_target_evidence_are_independent_raw_excerpts():
    ledger = load_ledger()
    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    docs = {d['id']: d for d in inventory['documents']}
    corpus = json.loads((ROOT / 'data/processed/vat_corpus.staging.json').read_text(encoding='utf-8'))
    units = {}
    for doc in corpus:
        for article in doc['articles']:
            units[article['id']] = article
            for clause in article.get('clauses', []):
                units[clause['id']] = clause
                units.update({p['id']: p for p in clause.get('points', [])})
    for item in ledger['candidates']:
        if not item['source_unit'].endswith('_DOCUMENT_SCAN'):
            assert item['source_unit'] in units
            assert item['source_field'] in units[item['source_unit']]
        expected_date = docs[item['source_doc']]['effective_on']
        for rule in docs[item['source_doc']].get('provision_effective_dates', []):
            if item['source_unit'] in rule.get('target_ids', []):
                expected_date = rule['effective_on']
        for interval in item['source_effective_intervals']:
            assert interval['from'] == expected_date
            assert interval['issued_on'] == docs[item['source_doc']]['issued_on']
            for evidence in interval['date_evidence_refs']:
                assert digest(ROOT / evidence['path']) == evidence['sha256']
                assert evidence['excerpt'] in source_text(ROOT / evidence['path'])
        if item['classification'] == 'resolved':
            evidence = item['target_evidence']
            assert digest(ROOT / evidence['path']) == evidence['sha256']
            text = source_text(ROOT / evidence['path'])
            start = evidence['locator']['normalized_text_start']
            assert text[start:start + len(evidence['excerpt'])] == evidence['excerpt']


def test_bounded_regressions_never_broaden_or_approve_quoted_history():
    candidates = load_ledger()['candidates']
    for unit in ('ND_144_2026_D3', 'ND_144_2026_D3_K1', 'ND_144_2026_D3_K2'):
        item, = [c for c in candidates if c['source_unit'] == unit and c['classification'] != 'nonoperative']
        assert item['classification'] == 'unresolved'
    phrase, = [c for c in candidates if c['source_unit'] == 'ND_141_2026_D1_K1']
    assert phrase['classification'] == 'unresolved'
    assert len(phrase['expected']['targets']) == 10
    assert all(t['doc_id'] == 'ND_68_2026' for t in phrase['expected']['targets'])
    quoted, = [c for c in candidates if c['classification'] == 'nonoperative' and c['expected']['scope'] == 'quoted_history']
    assert quoted['expected']['emit_resolved'] is False
    assert quoted['expected']['targets'] == []


def test_review_gaps_cannot_be_misrepresented_as_complete():
    ledger = load_ledger()
    gaps = [doc for doc in ledger['documents'] if doc['gaps']]
    assert ledger['review_complete'] == (not gaps and not ledger['gaps'])
    assert ledger['historical_resolved_review']['reviewed_count'] == len(ledger['historical_resolved_review']['entries'])
    assert ledger['review_complete'] is True, 'R1 requires full source review, not partial signoff'
    assert ledger['historical_resolved_review']['reviewed_count'] == 26
    assert len(ledger['historical_resolved_review']['entries']) == 26
    assert all(doc['review_status'] == 'reviewed' for doc in ledger['documents'])
    for entry in ledger['historical_resolved_review']['entries']:
        assert entry['decision'] in {'accepted', 'rejected'}
        assert entry['candidate_ids'] and entry['reason']
        assert set(entry['candidate_ids']) <= {c['id'] for c in ledger['candidates']}


class SourceBlocks(SourceText):
    def __init__(self):
        super().__init__()
        self.blocks = []

    def flush(self):
        text = ' '.join(' '.join(self.parts).split())
        if text:
            self.blocks.append(text)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'}:
            self.flush()

    def handle_endtag(self, tag):
        if tag in {'p', 'div', 'tr', 'li', 'h1', 'h2', 'h3'}:
            self.flush()


@lru_cache(maxsize=None)
def source_blocks(path):
    if path.suffix == '.pdf':
        from pypdf import PdfReader

        return [line for page in PdfReader(path).pages for line in page.extract_text().splitlines()]
    raw = path.read_text(encoding='utf-8')
    if path.suffix == '.json':
        snapshot = json.loads(raw)
        if 'html' not in snapshot:
            return snapshot.get('text', snapshot.get('innerText', '')).splitlines()
        raw = snapshot['html']
    parser = SourceBlocks()
    parser.feed(raw)
    parser.flush()
    return parser.blocks


def test_every_scanned_keyword_block_has_an_explicit_disposition():
    ledger = load_ledger()
    for doc in ledger['documents']:
        scan = doc['body_scan']
        assert digest(ROOT / scan['path']) == scan['sha256']
        blocks = source_blocks(ROOT / scan['path'])
        assert len(blocks) == scan['block_count']
        keywords = re.compile(r'sửa đổi|bổ sung|bãi bỏ|thay thế|đính chính|kéo dài|hết hiệu lực', re.I)
        assert scan['keyword_block_indices'] == [i for i, text in enumerate(blocks) if keywords.search(text)]
        assert scan['headings'] == [{'block_index': i, 'text': text} for i, text in enumerate(blocks)
                                    if re.match(r'Điều\s+\d+', text)] or doc['doc_id'] == 'QD_482_2026'
        candidates = [c for c in ledger['candidates'] if c['source_doc'] == doc['doc_id']]
        represented = {c['evidence']['locator']['block_index'] for c in candidates}
        for candidate in candidates:
            for evidence in candidate.get('source_block_members', []):
                assert evidence['classification'] == candidate['classification']
                assert evidence['reason'] and evidence['emit_resolved'] is False
                assert digest(ROOT / evidence['path']) == evidence['sha256']
                text = source_text(ROOT / evidence['path'])
                start = evidence['locator']['normalized_text_start']
                assert text[start:start + len(evidence['excerpt'])] == evidence['excerpt']
                represented.add(evidence['locator']['block_index'])
        assert set(scan['keyword_block_indices']) <= represented
        if not scan['block_count']:
            assert doc['doc_id'] == 'QD_482_2026'
            assert len(scan['visual_pages']) == 2
            for page in scan['visual_pages']:
                assert digest(ROOT / page['path']) == page['sha256']


def test_historical26_has_unique_exact_identities_and_source_grounded_decisions():
    ledger = load_ledger()
    entries = ledger['historical_resolved_review']['entries']
    assert [entry['historical_index'] for entry in entries] == list(range(1, 27))
    assert {entry['historical_index'] for entry in entries if entry['decision'] == 'rejected'} == {6, 23, 25}
    candidates = {c['id']: c for c in ledger['candidates']}
    for entry in entries:
        for candidate_id in entry['candidate_ids']:
            candidate = candidates[candidate_id]
            assert candidate['source_unit'] == entry['source_unit']
            assert candidate['classification'] == ('resolved' if entry['decision'] == 'accepted' else 'unresolved')
            if entry['decision'] == 'accepted':
                assert candidate['expected']['targets'] == [entry['target']]
                assert candidate['expected']['action'] == entry['action']
                assert candidate['expected']['scope'] == entry['scope']


def test_omitted_operative_directives_and_precise_nd144_targets_are_preserved():
    candidates = load_ledger()['candidates']
    by_unit = {}
    for candidate in candidates:
        by_unit.setdefault(candidate['source_unit'], []).append(candidate)
    required = {
        'LUAT_90_2025_D4': 'unresolved',
        'QD_2551_2026_D1': 'unresolved',
        'TT_89_2026_D99_K4_d_1': 'unresolved',
        'TT_84_2026_D16_K1': 'unresolved',
        'TT_97_2026_D1': 'unresolved',
        'LUAT_108_2025_D51': 'unresolved',
        'TT_50_2026_D3': 'outscope',
        'NQ_34_2026_D2': 'outscope',
        'NQ_43_2026_D2': 'outscope',
    }
    for unit, classification in required.items():
        assert any(c['classification'] == classification for c in by_unit[unit])
    for unit, article, clause, point in [('ND_144_2026_D2', 9, 1, 'b'), ('ND_144_2026_D4', 26, 2, 'g')]:
        candidate, = by_unit[unit]
        assert candidate['classification'] == 'resolved'
        assert candidate['expected']['targets'] == [{
            'doc_id': 'ND_181_2025', 'article_id': f'ND_181_2025_D{article}',
            'clause_id': f'ND_181_2025_D{article}_K{clause}',
            'point_id': f'ND_181_2025_D{article}_K{clause}_{point}',
        }]
    inserted, = by_unit['TT_92_2019_D1_K2']
    assert inserted['classification'] == 'unresolved'
    assert inserted['expected']['targets'][0]['article_id'] == 'TT_72_2014_D4a'
    retroactive, = by_unit['LUAT_09_2026_D2']
    assert retroactive['source_effective_intervals'][0]['from'] == '2026-01-01'
    assert retroactive['source_effective_intervals'][0]['issued_on'] == '2026-04-24'


RAW_DOCUMENTS = {
    'TT_50_2026': ('source_TT_50_2026.snapshot.json', 0, '50/2026/TT-BTC'),
    'TT_18_2026': ('source_TT_18_2026.snapshot.json', 3, '18/2026/TT-BTC'),
    'ND_359_2025': ('04_nd_359_2025.html', 1, '359/2025/NĐ-CP'),
    'ND_181_2025': ('02_nd_181_2025.html', 2, '181/2025/NĐ-CP'),
    'TT_92_2019': ('source_TT_92_2019.snapshot.json', 3, '9 2/2019/TT-BTC'),
    'TT_72_2014': ('source_TT_72_2014.snapshot.json', 3, '72/2014/TT-BTC'),
    'LUAT_149_2025': ('source_LUAT_149_2025.snapshot.json', 3, '149/2025/QH15'),
    'LUAT_48_2024': ('01_luat_48_2024.html', 1, '48/2024/QH15'),
    'LUAT_09_2026': ('source_LUAT_09_2026.snapshot.json', 0, '09/2026/QH16'),
    'ND_141_2026': ('source_ND_141_2026.snapshot.json', 1, '141/2026/NĐ-CP'),
    'ND_68_2026': ('source_ND_68_2026.snapshot.json', 2, '68/2026/NĐ-CP'),
    'ND_144_2026': ('source_ND_144_2026.snapshot.json', 0, '144/2026/NĐ-CP'),
}

REVIEWED_23 = (
    (1, 'TT_50_2026_D1', 12, 'Điều 1. Sửa đổi, bổ sung Điều 1', 'TT_18_2026', 1, None, None, 15, None, 15, 'sua_doi', 'article'),
    (2, 'TT_50_2026_D2', 15, 'Điều 2. Sửa đổi, bổ sung khoản 2 Điều 5', 'TT_18_2026', 5, 2, None, 43, 45, 45, 'sua_doi', 'clause'),
    (3, 'ND_359_2025_D1_K2', 16, '2. Bãi bỏ khoản 3 Điều 37 và khoản 3 Điều 39.', 'ND_181_2025', 37, 3, None, 424, 429, 429, 'bai_bo', 'clause'),
    (4, 'ND_359_2025_D1_K2', 16, '2. Bãi bỏ khoản 3 Điều 37 và khoản 3 Điều 39.', 'ND_181_2025', 39, 3, None, 448, 452, 452, 'bai_bo', 'clause'),
    (5, 'TT_92_2019_D1_K1', 20, '1. Điều 3 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 3, None, None, 27, None, 27, 'sua_doi', 'article'),
    (7, 'TT_92_2019_D1_K4', 44, '4. Khoản 9 Điều 7 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 7, 9, None, 57, 67, 67, 'sua_doi', 'clause'),
    (8, 'TT_92_2019_D1_K7', 59, '7. Khoản 2 Điều 10 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 10, 2, None, 95, 98, 98, 'sua_doi', 'clause'),
    (9, 'TT_92_2019_D1_K9', 64, '9. Điều 13 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 13, None, None, 122, None, 122, 'sua_doi', 'article'),
    (10, 'TT_92_2019_D1_K10', 82, '10. Khoản 3 Điều 14 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 14, 3, None, 139, 149, 149, 'sua_doi', 'clause'),
    (11, 'TT_92_2019_D1_K12', 93, '12. Khoản 1 Điều 18 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 18, 1, None, 177, 179, 179, 'sua_doi', 'clause'),
    (12, 'TT_92_2019_D1_K13', 96, '13. Khoản 2 Điều 19 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 19, 2, None, 186, 192, 192, 'sua_doi', 'clause'),
    (13, 'TT_92_2019_D1_K14', 104, '14. Khoản 2 Điều 20 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 20, 2, None, 197, 202, 202, 'sua_doi', 'clause'),
    (14, 'TT_92_2019_D1_K15', 115, '15. Điều 21 được sửa đổi, bổ sung như sau:', 'TT_72_2014', 21, None, None, 209, None, 209, 'sua_doi', 'article'),
    (15, 'TT_92_2019_D2_K1', 131, '1. Bãi bỏ khoản 5 Điều 10, khoản 4 Điều 15, khoản 5 Điều 18 Thông tư số 72/2014/TT-BTC.', 'TT_72_2014', 10, 5, None, 95, 102, 102, 'bai_bo', 'clause'),
    (16, 'TT_92_2019_D2_K1', 131, '1. Bãi bỏ khoản 5 Điều 10, khoản 4 Điều 15, khoản 5 Điều 18 Thông tư số 72/2014/TT-BTC.', 'TT_72_2014', 15, 4, None, 152, 164, 164, 'bai_bo', 'clause'),
    (17, 'TT_92_2019_D2_K1', 131, '1. Bãi bỏ khoản 5 Điều 10, khoản 4 Điều 15, khoản 5 Điều 18 Thông tư số 72/2014/TT-BTC.', 'TT_72_2014', 18, 5, None, 177, 185, 185, 'bai_bo', 'clause'),
    (18, 'LUAT_149_2025_D1_K2', 15, '2. Sửa đổi, bổ sung khoản 5 Điều 9 như sau:', 'LUAT_48_2024', 9, 5, None, 103, 126, 126, 'sua_doi', 'clause'),
    (19, 'LUAT_149_2025_D1_K3', 17, '3. Bãi bỏ khoản 3 Điều 12 và điểm c khoản 9 Điều 15.', 'LUAT_48_2024', 12, 3, None, 143, 159, 159, 'bai_bo', 'clause'),
    (20, 'LUAT_149_2025_D1_K3', 17, '3. Bãi bỏ khoản 3 Điều 12 và điểm c khoản 9 Điều 15.', 'LUAT_48_2024', 15, 9, 'c', 189, 211, 214, 'bai_bo', 'point'),
    (21, 'LUAT_09_2026_D2', 7, 'Điều 2. Sửa đổi, bổ sung khoản 25 Điều 5 của Luật Thuế giá trị gia tăng', 'LUAT_48_2024', 5, 25, None, 22, 61, 61, 'sua_doi', 'clause'),
    (22, 'ND_141_2026_D1_K2', 17, '2. Sửa đổi, bổ sung khoản 5 Điều 8 Nghị định số 68/2026/NĐ-CP như sau:', 'ND_68_2026', 8, 5, None, 71, 100, 100, 'sua_doi', 'clause'),
    (24, 'ND_144_2026_D2', 17, 'Điều 2. Sửa đổi, bổ sung điểm b khoản 1 Điều 9', 'ND_181_2025', 9, 1, 'b', 124, 125, 128, 'sua_doi', 'point'),
    (26, 'ND_144_2026_D4', 25, 'Điều 4. Sửa đổi, bổ sung điểm g khoản 2 Điều 26', 'ND_181_2025', 26, 2, 'g', 338, 342, 349, 'sua_doi', 'point'),
)

REVIEWED_DATES = {
    'TT_50_2026': (1, 'ngày 13 tháng 5 năm 2026', '2026-05-13', 20, '1. Thông tư này có hiệu lực thi hành kể từ ngày ký ban hành.', '2026-05-13', 'signing'),
    'ND_359_2025': (3, 'ngày 31 tháng 12 năm 2025', '2025-12-31', 18, '1. Nghị định này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2026.', '2026-01-01', 'explicit'),
    'TT_92_2019': (7, 'ngày 31 tháng 12 năm 2019', '2019-12-31', 138, '1. Thông tư này có hiệu lực kể từ ngày 01 tháng 7 năm 2020.', '2020-07-01', 'explicit'),
    'LUAT_149_2025': (20, 'thông qua ngày 11 tháng 12 năm 2025', '2025-12-11', 19, 'Luật này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2026.', '2026-01-01', 'explicit'),
    'LUAT_09_2026': (34, 'thông qua ngày 24 tháng 4 năm 2026', '2026-04-24', 32, '2. Quy định tại các điều 1, 2 và 3 của Luật này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2026.', '2026-01-01', 'retroactive_article_1_2_3'),
    'ND_141_2026': (3, 'ngày 29 tháng 4 năm 2026', '2026-04-29', 30, 'Nghị định này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2026.', '2026-01-01', 'retroactive'),
    'ND_144_2026': (2, 'ngày 05 tháng 5 năm 2026', '2026-05-05', 30, '1. Nghị định này có hiệu lực thi hành kể từ ngày 20 tháng 6 năm 2026.', '2026-06-20', 'explicit'),
}


def raw_document(doc_id):
    filename, index, code = RAW_DOCUMENTS[doc_id]
    path = ROOT / 'data/raw' / filename
    blocks = source_blocks(path)
    assert code in blocks[index]
    return path, blocks


def assert_raw_locator(evidence, path, block_index):
    assert evidence['path'] == path.relative_to(ROOT).as_posix()
    assert evidence['sha256'] == digest(path)
    assert evidence['locator']['block_index'] == block_index
    block = ' '.join(source_blocks(path)[block_index].split())
    assert evidence['excerpt'] == block
    offset = evidence['locator']['normalized_text_start']
    assert source_text(path)[offset:offset + len(block)] == block


def validate_target_binding(candidate, entry, row):
    _, unit, si, directive, td, article, clause, point, ai, ci, ti, action, scope = row
    source_doc = unit.split('_D')[0]
    sp, sb = raw_document(source_doc)
    tp, tb = raw_document(td)
    assert sb[si] == directive
    assert_raw_locator(candidate['evidence'], sp, si)
    assert_raw_locator(candidate['target_evidence'], tp, ti)
    assert tb[ai].startswith(f'Điều {article}.')
    assert next(i for i in range(ti, -1, -1) if re.match(r'^Điều \d+\.', tb[i])) == ai
    if clause is not None:
        assert tb[ci].startswith(f'{clause}.')
        assert next(i for i in range(ti, ai, -1) if re.match(r'^\d+\.', tb[i])) == ci
    if point:
        assert tb[ti].startswith(f'{point})')
    target = {'doc_id': td, 'article_id': f'{td}_D{article}'}
    if clause is not None:
        target['clause_id'] = f'{td}_D{article}_K{clause}'
    if point:
        target['point_id'] = f'{td}_D{article}_K{clause}_{point}'
    assert entry['source_unit'] == candidate['source_unit'] == unit
    assert entry['target'] == target
    assert candidate['expected']['targets'] == [target]
    assert entry['action'] == candidate['expected']['action'] == action
    assert entry['scope'] == candidate['expected']['scope'] == scope
    binding = candidate['target_evidence']['designation_locator']
    assert binding == {'document_block': RAW_DOCUMENTS[td][1], 'article_block': ai,
                       'clause_block': ci, 'point_block': ti if point else None}


def validate_date_binding(candidate, document):
    doc_id = candidate['source_doc']
    ii, issued_text, issued, ei, effective_text, effective, basis = REVIEWED_DATES[doc_id]
    path, blocks = raw_document(doc_id)
    assert issued_text in blocks[ii]
    assert blocks[ei] == effective_text
    interval, = candidate['source_effective_intervals']
    assert document['issued_on'] == interval['issued_on'] == issued
    assert interval['from'] == effective
    assert interval['commencement_basis'] == basis
    assert_raw_locator(interval['issuance_evidence'], path, ii)
    assert_raw_locator(interval['commencement_evidence'], path, ei)


def validate_reviewed_oracle(ledger):
    candidates = {c['id']: c for c in ledger['candidates']}
    entries = {e['historical_index']: e for e in ledger['historical_resolved_review']['entries']}
    docs = {d['doc_id']: d for d in ledger['documents']}
    assert {e['historical_index'] for e in entries.values() if e['decision'] == 'accepted'} == {r[0] for r in REVIEWED_23}
    assert len([c for c in candidates.values() if c['classification'] == 'resolved']) == 23
    for row in REVIEWED_23:
        entry = entries[row[0]]
        candidate, = [candidates[cid] for cid in entry['candidate_ids']]
        assert candidate['classification'] == 'resolved'
        validate_target_binding(candidate, entry, row)
        validate_date_binding(candidate, docs[candidate['source_doc']])


def test_accepted23_matches_manual_raw_identity_action_scope_and_dates():
    validate_reviewed_oracle(load_ledger())


@pytest.mark.parametrize('mutation', ['target', 'issued_on', 'commencement', 'action', 'scope'])
def test_coordinated_accepted_mutations_are_rejected(mutation):
    ledger = deepcopy(load_ledger())
    validate_reviewed_oracle(ledger)
    entry = ledger['historical_resolved_review']['entries'][0]
    candidate = next(c for c in ledger['candidates'] if c['id'] == entry['candidate_ids'][0])
    if mutation == 'target':
        entry['target']['article_id'] = 'TT_18_2026_D2'
        candidate['expected']['targets'] = [deepcopy(entry['target'])]
    elif mutation == 'issued_on':
        candidate['source_effective_intervals'][0]['issued_on'] = '2026-05-14'
        next(d for d in ledger['documents'] if d['doc_id'] == 'TT_50_2026')['issued_on'] = '2026-05-14'
    elif mutation == 'commencement':
        candidate['source_effective_intervals'][0]['from'] = '2026-05-14'
    else:
        entry[mutation] = candidate['expected'][mutation] = 'bai_bo' if mutation == 'action' else 'clause'
    with pytest.raises(AssertionError):
        validate_reviewed_oracle(ledger)


@pytest.mark.parametrize('mutation', ['target_locator', 'target_heading', 'source_locator', 'issuance_locator', 'commencement_locator', 'retroactive_issuance', 'retroactive_commencement'])
def test_raw_evidence_and_retroactive_date_mutations_are_rejected(mutation):
    ledger = deepcopy(load_ledger())
    validate_reviewed_oracle(ledger)
    index = 21 if mutation.startswith('retroactive') else 1
    entry = next(e for e in ledger['historical_resolved_review']['entries'] if e['historical_index'] == index)
    candidate = next(c for c in ledger['candidates'] if c['id'] == entry['candidate_ids'][0])
    interval = candidate['source_effective_intervals'][0]
    if mutation == 'target_locator':
        candidate['target_evidence']['locator']['block_index'] = 17
    elif mutation == 'target_heading':
        candidate['target_evidence']['designation_locator']['article_block'] = 17
    elif mutation == 'source_locator':
        candidate['evidence']['locator']['block_index'] = 15
    elif mutation == 'issuance_locator':
        interval['issuance_evidence'] = deepcopy(interval['commencement_evidence'])
    elif mutation == 'commencement_locator':
        interval['commencement_evidence'] = deepcopy(interval['issuance_evidence'])
    elif mutation == 'retroactive_issuance':
        interval['issued_on'] = '2026-01-01'
        next(d for d in ledger['documents'] if d['doc_id'] == 'LUAT_09_2026')['issued_on'] = '2026-01-01'
    else:
        interval['from'] = '2026-04-24'
    with pytest.raises(AssertionError):
        validate_reviewed_oracle(ledger)


def inspect_sources(doc_ids):
    inventory = json.loads((ROOT / 'data/source_inventory.json').read_text(encoding='utf-8'))
    for doc in inventory['documents']:
        if doc['id'] not in doc_ids:
            continue
        print(json.dumps(doc, ensure_ascii=False, indent=2))
        paths = [p for p in doc['source_paths'] if p.endswith('.snapshot.json') or p.endswith('.html')]
        if not paths:
            print('NO HTML SNAPSHOT: direct PDF review required')
            continue
        path = next((p for p in paths if p.endswith('.snapshot.json')), paths[0])
        text = source_text(ROOT / path)
        print('SOURCE', path, 'SHA256', digest(ROOT / path), 'CHARS', len(text))
        print(text)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    inspect_sources(sys.argv[1:])

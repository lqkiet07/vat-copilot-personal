import hashlib
import json
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.engine.provision_evidence import resolve_provision_evidence
from tests.test_amendment_review import source_blocks, source_text


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / 'tests/fixtures/amendment_review.staging.json'
CORPUS = ROOT / 'data/processed/vat_corpus.staging.json'
REPLACEMENTS = [('TT_92_2019:44:6', 45), ('TT_92_2019:59:7', 60)]
REPEALS = ['ND_359_2025:16:2', 'ND_359_2025:16:3', 'TT_92_2019:131:14',
           'TT_92_2019:131:15', 'TT_92_2019:131:16']


def ledger():
    return json.loads(LEDGER.read_text(encoding='utf-8'))


def candidate(identity, review=None):
    return next(c for c in (review or ledger())['candidates'] if c['id'] == identity)


def resolve(ids, day, path=LEDGER):
    return resolve_provision_evidence(ids, day, corpus_path=str(CORPUS), reviewed_amendment_path=str(path))


def provisions(result):
    return {p.id: p for item in result.items for p in item.provisions}


@pytest.mark.parametrize('target,fragment', [
    ('LUAT_48_2024_D5_K1', 'không phải kê khai'),
    ('LUAT_48_2024_D5_K25', 'Căn cứ các chỉ số kinh tế vĩ mô'),
    ('LUAT_48_2024_D9_K1_a', 'hàng hóa xuất khẩu tại chỗ')])
def test_priority_source_bound_operations(target, fragment):
    result = resolve([target], '2026-07-01')
    assert target not in result.blocked, result.blocked
    assert fragment in provisions(result)[target].text


@pytest.mark.parametrize('target', ['ND_68_2026_D4_K3', 'ND_68_2026_D10',
                                    'ND_68_2026_D11_K3', 'ND_68_2026_D12_K1',
                                    'ND_68_2026_D12_K2', 'ND_68_2026_D18_K3'])
def test_bounded_phrase_operations_preserve_other_scopes(target):
    result = resolve([target, 'ND_68_2026_D11_K1', 'ND_68_2026_D8_K1'], '2026-07-01')
    assert target not in result.blocked, result.blocked
    assert '500 triệu đồng' not in provisions(result)[target].text
    assert '01 tỷ đồng' in provisions(result)[target].text
    assert provisions(result)['ND_68_2026_D11_K1'].status == 'UNCHANGED'
    assert 'ND_68_2026_D8_K1' in result.blocked


def test_inventory_preserves_23_relations_and_disposes_every_resolved_candidate():
    review = ledger()
    resolved = [c for c in review['candidates'] if c['classification'] == 'resolved']
    assert len(resolved) == 23
    assert len(review['operative_review']['resolved_inventory']) == 23
    assert {r['candidate_id'] for r in review['operative_review']['resolved_inventory']} == {c['id'] for c in resolved}
    assert all(c.get('operations') for c in resolved)
    assert review['operative_review']['supported_candidate_count'] == 43
    assert sum(len(c.get('operations', [])) for c in review['candidates']) == 76


@pytest.mark.parametrize('identity,block', REPLACEMENTS)
def test_replacement_is_exact_raw_source_with_independent_dates(identity, block):
    c = candidate(identity)
    op, = c['operations']
    expected = source_blocks(ROOT / c['evidence']['path'])[block][1:-1]
    binding = op['replacement']
    raw = (ROOT / binding['path']).read_bytes()
    assert binding['text'] == expected
    assert raw[binding['byte_start']:binding['byte_end']] == expected.encode('utf-8')
    assert hashlib.sha256(raw).hexdigest() == binding['sha256']
    assert (op['issued_on'], op['effective_on']) == ('2019-12-31', '2020-07-01')
    for key, excerpt in [('issuance_evidence', 'ngày 31 tháng 12 năm 2019'),
                         ('commencement_evidence', 'ngày 01 tháng 7 năm 2020')]:
        ref = op[key]
        text = source_text(ROOT / ref['path'])
        start = ref['locator']['normalized_text_start']
        assert text[start:start + len(ref['excerpt'])] == ref['excerpt']
        assert excerpt in ref['excerpt']


@pytest.mark.parametrize('identity,block', REPLACEMENTS)
@pytest.mark.parametrize('day', ['2020-06-30', '2020-07-01', '2020-07-02'])
def test_generic_resolver_before_on_after_with_unaffected_sibling(identity, block, day):
    c = candidate(identity)
    target = list(c['expected']['targets'][0].values())[-1]
    sibling = 'TT_72_2014_D7_K1'
    result = resolve([target, sibling], day)
    actual = provisions(result)
    assert target not in result.blocked, result.blocked
    assert sibling not in result.blocked
    assert actual[sibling].status == 'UNCHANGED'
    original = source_text(ROOT / c['target_evidence']['path'])
    assert c['target_evidence']['excerpt'] in original
    if day < '2020-07-01':
        assert actual[target].status == 'UNCHANGED'
        assert actual[target].valid_to == '2020-06-30'
    else:
        assert actual[target].text == source_blocks(ROOT / c['evidence']['path'])[block][1:-1]
        assert (actual[target].valid_from, actual[target].issued_on) == ('2020-07-01', '2019-12-31')
        assert actual[target].provenance[-1]['candidate_id'] == identity


@pytest.mark.parametrize('identity', REPEALS)
def test_source_reviewed_repeals_are_inclusive_and_persistent(identity):
    c = candidate(identity)
    op, = c['operations']
    target = op['target_id']
    effective = date.fromisoformat(op['effective_on'])
    assert target in provisions(resolve([target], (effective - timedelta(days=1)).isoformat()))
    for day in [effective.isoformat(), (effective + timedelta(days=1)).isoformat(), '2027-01-01']:
        result = resolve([target], day)
        assert target not in provisions(result)
        assert ('Reviewed repeal suppresses provision' in result.blocked[target]
                or result.blocked[target].startswith('Container is not citable;'))


@pytest.mark.parametrize('mutation', ['text', 'offset', 'issued', 'effective', 'locator', 'source', 'missing'])
def test_mutated_operation_cannot_authorize_text(tmp_path, mutation):
    review = deepcopy(ledger())
    c = candidate(REPLACEMENTS[0][0], review)
    op = c['operations'][0]
    if mutation == 'text':
        op['replacement']['text'] += ' invented'
    elif mutation == 'offset':
        op['replacement']['byte_start'] += 1
    elif mutation == 'issued':
        op['issued_on'] = '2020-01-01'
    elif mutation == 'effective':
        op['effective_on'] = '2020-07-02'
    elif mutation == 'locator':
        op['commencement_evidence']['locator']['normalized_text_start'] += 1
    elif mutation == 'source':
        op['source_unit'] = 'TT_72_2014_D7_K9'
    else:
        del op['replacement']
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    target = 'TT_72_2014_D7_K9'
    result = resolve([target, 'TT_72_2014_D7_K1'], '2020-07-01', path)
    assert target in result.blocked
    assert provisions(result)['TT_72_2014_D7_K1'].status == 'UNCHANGED'


@pytest.mark.parametrize('identity', ['LUAT_149_2025:10:37', 'LUAT_149_2025:13:38',
                                     'ND_141_2026:16:34'])
def test_confirmed_unresolved_scopes_are_not_promoted_to_runtime_proof(identity):
    c = candidate(identity)
    assert c['classification'] == 'unresolved'
    assert c.get('operations')
    assert c['expected']['emit_resolved'] is False
    for op in c['operations']:
        unit = op['target_id']
        assert unit not in resolve([unit], '2026-07-01').blocked


@pytest.mark.parametrize('mutation', ['text', 'date', 'scope', 'supersession', 'grouping', 'exception'])
def test_real_priority_mutations_never_expose_stale_text(tmp_path, mutation):
    review = deepcopy(ledger())
    target = 'LUAT_48_2024_D5_K25'
    successor = candidate('LUAT_09_2026:7:20', review)
    if mutation == 'text':
        successor['operations'][0]['replacement']['text'] += ' invented'
    elif mutation == 'date':
        successor['operations'][0]['effective_on'] = '2026-01-02'
    elif mutation == 'scope':
        candidate('LUAT_149_2025:13:38', review)['operative_scope']['targets'] = []
    elif mutation == 'supersession':
        successor['operations'][0]['supersedes']['candidate_id'] = 'unknown'
    elif mutation == 'grouping':
        candidate('LUAT_149_2025:9:39', review)['coverage']['candidate_ids'].pop()
    else:
        doc = next(d for d in review['documents'] if d['doc_id'] == 'LUAT_09_2026')
        doc['provision_effective_dates'][0]['mapping_status'] = 'pending'
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    result = resolve([target, 'LUAT_48_2024_D5_K2'], '2026-07-01', path)
    assert target in result.blocked
    if mutation != 'grouping':
        assert provisions(result)['LUAT_48_2024_D5_K2'].status == 'UNCHANGED'


@pytest.mark.parametrize('identity', ['LUAT_149_2025:10:37', 'LUAT_149_2025:13:38',
                                     'LUAT_09_2026:7:20', 'LUAT_90_2025:552:40'])
def test_new_replacements_bind_exact_decoded_raw_text(identity):
    c = candidate(identity)
    op, = c['operations']
    binding = op['replacement']
    text = source_text(ROOT / binding['path'])
    locator = binding['locator']
    assert text[locator['normalized_text_start']:locator['normalized_text_end']] == binding['text']
    assert hashlib.sha256((ROOT / binding['path']).read_bytes()).hexdigest() == binding['sha256']


def test_priority_blockers_preserve_raw_scope_and_retroactive_evidence():
    review = ledger()['operative_review']
    assert {'unresolved_operation_gate', 'same_date_chain', 'independent_date_schema',
            'normalized_replacement_binding', 'overlapping_parent_scope'} <= set(review['contract_blockers'])
    for entry in review['confirmed_unresolved']:
        for ref in entry['evidence_refs']:
            text = source_text(ROOT / ref['path'])
            start = ref['locator']['normalized_text_start']
            assert text[start:start + len(ref['excerpt'])] == ref['excerpt']
    law = source_text(ROOT / 'data/raw/source_LUAT_09_2026.snapshot.json')
    assert 'các điều 1, 2 và 3 của Luật này có hiệu lực thi hành từ ngày 01 tháng 01 năm 2026' in law
    phrase = candidate('ND_141_2026:16:34')
    assert len(phrase['expected']['targets']) == 10
    assert '“500 triệu đồng” thành “01 tỷ đồng”' in phrase['evidence']['excerpt']


@pytest.mark.parametrize('target,before_day,after_day,status,fragment', [
    ('LUAT_48_2024_D9_K5', '2025-12-31', '2026-01-01', 'AMENDED', 'phế liệu đó'),
    ('ND_68_2026_D8_K5_a', '2026-03-05', '2026-03-05', 'AMENDED', '01 tỷ đồng'),
    ('ND_181_2025_D4_K1b', '2025-12-31', '2026-01-01', 'INSERTED', 'khâu kinh doanh thương mại'),
    ('ND_181_2025_D4_K3a', '2026-06-19', '2026-06-20', 'INSERTED', 'Bảo hiểm nhân thọ'),
    ('ND_181_2025_D9_K1_b', '2026-06-19', '2026-06-20', 'AMENDED', 'môi giới dịch vụ bảo hiểm'),
    ('TT_18_2026_D1', '2026-05-12', '2026-05-13', 'AMENDED', 'Luật Quản lý thuế số 108/2025/QH15'),
    ('TT_89_2026_D99_K5', '2026-09-14', '2026-09-15', 'AMENDED', '01/XSBHDCMG-CNKD'),
    ('TT_72_2014_D4a', '2020-06-30', '2020-07-01', 'INSERTED', 'Tổng cục Hải quan chịu trách nhiệm'),
    ('TT_72_2014_D13_K1_a', '2020-06-30', '2020-07-01', 'AMENDED', 'hoạt động theo quy định'),
    ('TT_72_2014_D14_K3', '2020-06-30', '2020-07-01', 'AMENDED', 'đến lần thứ hai'),
    ('TT_72_2014_D21_K2', '2020-06-30', '2020-07-01', 'AMENDED', 'Tài chính hướng dẫn chế độ kế toán'),
])
def test_inner33_operations_resolve_accurately_across_dates(target, before_day, after_day, status, fragment):
    res_after = resolve([target], after_day)
    assert target not in res_after.blocked, res_after.blocked
    p = provisions(res_after)[target]
    assert p.status == status and fragment in p.text
    if before_day != after_day:
        res_before = resolve([target], before_day)
        if status == 'INSERTED':
            assert target in res_before.blocked
        else:
            assert provisions(res_before)[target].valid_to < after_day


def test_tt84_conditional_replacement_and_retirements():
    assert 'LUAT_48_2024_D12_K3' in resolve(['LUAT_48_2024_D12_K3'], '2026-01-01').blocked
    assert 'TT_18_2026_D4_K3' in resolve(['TT_18_2026_D4_K3'], '2026-07-01').blocked
    assert 'TT_72_2014_D13_K2_c' in resolve(['TT_72_2014_D13_K2_c'], '2020-07-01').blocked
    assert 'TT_72_2014_D14_K3_a' in resolve(['TT_72_2014_D14_K3_a'], '2020-07-01').blocked
    assert 'TT_72_2014_D13_K1_c' in resolve(['TT_72_2014_D13_K1_c'], '2020-06-30').blocked
    assert 'Cam kết tham gia vào Hệ thống' in provisions(resolve(['TT_72_2014_D13_K1_c'], '2020-07-01'))['TT_72_2014_D13_K1_c'].text
    res = resolve(['TT_72_2014_D3', 'TT_72_2014_D6_K2'], '2026-07-01')
    assert 'repeal' in res.blocked['TT_72_2014_D3'].lower()
    assert 'readiness' in res.blocked['TT_72_2014_D6_K2'].lower()


def test_mutated_except_evidence_on_tt84_fails_closed(tmp_path):
    review = deepcopy(ledger())
    c = candidate('TT_84_2026:168:54', review)
    c['operations'][0]['except_evidence']['excerpt'] = 'invented exception'
    path = tmp_path / 'review.json'
    path.write_text(json.dumps(review, ensure_ascii=False), encoding='utf-8')
    res = resolve(['TT_72_2014_D6_K2'], '2026-07-01', path)
    assert 'TT_72_2014_D6_K2' in res.blocked

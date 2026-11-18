import argparse
from datetime import date
import hashlib
import html as html_mod
import json
import os
from pathlib import Path
import re
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.validate_corpus import validate_corpus
from src.parser.vbpl_parser import parse_vbpl_document

_PDF_HEAD = re.compile(
    r'^(Chương\s+[IVXLCDM]+|Điều\s+\d+[\.:]|\d+[\.\)]\s+|[a-zđ]\)\s+|Phụ lục\b|Nơi nhận:)',
    re.IGNORECASE,
)


def _safe_path(root, relative):
    path = root / relative
    if (Path(relative).is_absolute() or not path.resolve().is_relative_to(root)
            or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError('Unsafe corpus path')
    return path


def _is_iso_date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _temporal_errors(doc):
    errors = []
    blocked = {
        'conditional_transitions': 'conditional transitions require validated mapping',
        'effective_from_local': 'time-aware commencement requires compatible consumers',
        'effective_until_local_exclusive': 'time-aware expiry requires compatible consumers',
        'document_role': 'consolidation requires separate evidence-only assembly',
        'unresolved_references': 'unresolved references require review',
        'valid_to': 'expiry requires validated temporal mapping',
        'effective_until_inclusive': 'expiry requires validated temporal mapping',
        'applies_through': 'expiry requires validated temporal mapping',
    }
    for field, message in blocked.items():
        if field in doc:
            errors.append(message)
    if 'original_application_end_on' in doc:
        end_on = doc['original_application_end_on']
        if not _is_iso_date(end_on) or (_is_iso_date(doc.get('effective_on')) and end_on < doc['effective_on']):
            errors.append('application end requires validated temporal mapping')
    if doc.get('temporal_review_status') != 'approved':
        errors.append('temporal review not approved')
    rules = doc.get('provision_effective_dates')
    if not isinstance(rules, list):
        return errors + ['provision effective dates require validated mapping']
    for rule in rules:
        if not isinstance(rule, dict) or rule.get('mapping_status') != 'approved':
            errors.append('provision effective dates require validated mapping')
            continue
        evidence = rule.get('evidence')
        if not isinstance(evidence, str) or not evidence.strip():
            errors.append('provision effective dates require validated mapping')
            continue
        if rule.get('scope_kind') == 'text_span_within_clause':
            date_keys = {'effective_on', 'effective_until_inclusive'} & set(rule)
            allowed = {'scope_kind', 'container_id', 'subject', 'mapping_status', 'evidence'} | date_keys
            if (not date_keys or set(rule) != allowed
                    or not isinstance(rule.get('container_id'), str) or not rule['container_id']
                    or not isinstance(rule.get('subject'), str) or not rule['subject'].strip()):
                errors.append('provision effective dates require validated mapping')
                continue
            if any(not _is_iso_date(rule[k]) for k in date_keys):
                errors.append('invalid provision effective date')
            continue
        date_keys = {'effective_on', 'effective_until_inclusive'} & set(rule)
        allowed = {'target_ids', 'mapping_status', 'evidence'} | date_keys
        if (not date_keys or set(rule) != allowed
                or not isinstance(rule.get('target_ids'), list) or not rule['target_ids']
                or any(not isinstance(t, str) or not t for t in rule['target_ids'])):
            errors.append('provision effective dates require validated mapping')
            continue
        if any(not _is_iso_date(rule[k]) for k in date_keys):
            errors.append('invalid provision effective date')
    return errors


def _apply_provision_dates(parsed, rules):
    def descendants(node):
        yield node
        for key in ('clauses', 'points'):
            for child in node.get(key, []):
                yield from descendants(child)

    nodes = [node for article in parsed['articles'] for node in descendants(article)]
    index = {}
    for node in nodes:
        if node['id'] in index:
            raise ValueError('Ambiguous provision ID')
        index[node['id']] = node
    start_updates, end_updates = {}, {}
    for rule in rules:
        if rule.get('scope_kind') == 'text_span_within_clause':
            cid = rule['container_id']
            if cid not in index:
                raise ValueError(f'Unresolved provision target: {cid}')
            container = index[cid]
            if rule['subject'].casefold() not in container.get('content', '').casefold():
                raise ValueError(f'Unresolved provision span in {cid}')
            spans = container.setdefault('provision_spans', [])
            spans.append(dict(
                subject=rule['subject'],
                valid_from=rule.get('effective_on', container['valid_from']),
                valid_to=rule.get('effective_until_inclusive', container['valid_to']),
                evidence=rule['evidence'],
            ))
            continue
        for target in rule['target_ids']:
            if target not in index:
                raise ValueError(f'Unresolved provision target: {target}')
            for node in descendants(index[target]):
                if 'effective_on' in rule:
                    if node['id'] in start_updates:
                        raise ValueError('overlapping provision effective date rules')
                    start_updates[node['id']] = rule['effective_on']
                if 'effective_until_inclusive' in rule:
                    if node['id'] in end_updates:
                        raise ValueError('overlapping provision effective date rules')
                    end_updates[node['id']] = rule['effective_until_inclusive']
    for node_id, effective in start_updates.items():
        index[node_id]['valid_from'] = effective
    for node_id, until in end_updates.items():
        index[node_id]['valid_to'] = until
    for node in nodes:
        if node['valid_from'] > node['valid_to']:
            raise ValueError('Invalid provision interval')


def _check_inventory(data):
    records = [r for r in data['evidence_records'] if r.get('code')]
    docs = data['documents']
    errors = []
    codes = [d['code'] for d in docs]
    ids = [d['id'] for d in docs]
    if not docs or len(set(codes)) != len(codes) or len(set(ids)) != len(ids):
        errors.append('Missing or duplicate document metadata')
    missing = {r['code'] for r in records} - set(codes)
    if missing:
        errors.append('Missing metadata for: ' + ', '.join(sorted(missing)))
    paths = [r['path'] for r in records]
    if len(set(paths)) != len(paths):
        errors.append('Duplicate evidence paths')
    for doc in docs:
        code = doc['code']
        if doc.get('corpus_review_status') != 'approved':
            errors.append(f'{code}: corpus review not approved')
        errors.extend(f'{code}: {error}' for error in _temporal_errors(doc))
        declared = {r['path'] for r in records if r['code'] == code}
        selected = doc.get('source_paths', [])
        if not selected or len(set(selected)) != len(selected) or set(selected) != declared:
            errors.append(f'{code}: source coverage unresolved')
        for key in ('issued_on', 'effective_on'):
            try:
                value = doc[key]
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError('Noncanonical date')
            except (KeyError, TypeError, ValueError):
                errors.append(f'{code}: invalid {key}')
    if errors:
        raise ValueError('\n'.join(errors))
    return {r['path']: r for r in records}


def _parse_document(doc, records, root, inventory_hash):
    paths = doc['source_paths']
    primary = doc.get('primary_body_source')
    if len(paths) != 1 and not primary:
        raise ValueError(f'{doc["code"]}: multi-source assembly is not validated')
    primary_path = primary or paths[0]
    if primary_path not in paths:
        raise ValueError(f'{doc["code"]}: primary_body_source not in source_paths')
    verified_records = []
    for rel_path in paths:
        rec = records[rel_path]
        fpath = _safe_path(root, rec['path'])
        raw_bytes = fpath.read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != rec['sha256']:
            raise ValueError(f'{doc["code"]}: source hash mismatch')
        verified_records.append((rec, raw_bytes))
    record, content = next((r, b) for r, b in verified_records if r['path'] == primary_path)
    if record['kind'] not in ('html', 'official_snapshot'):
        raise ValueError(f'{doc["code"]}: non-HTML/OCR source requires reviewed semantic adapter')
    html = content.decode('utf-8')
    if record['kind'] == 'official_snapshot':
        html = json.loads(html)['html']
    valid_to = doc.get('original_application_end_on', '9999-12-31')
    meta = dict(doc_id=doc['id'], code=doc['code'], title=doc['title'],
                valid_from=doc['effective_on'], valid_to=valid_to, status='UNVERIFIED')
    parsed = parse_vbpl_document(html, meta)
    if not parsed.get('articles') or parsed.get('unresolved_blocks'):
        raise ValueError(f'{doc["code"]}: empty or unresolved legal structure')
    _apply_provision_dates(parsed, doc['provision_effective_dates'])
    parsed.update(issue_date=doc['issued_on'],
                  provision_effective_dates=doc.get('provision_effective_dates', []),
                  provenance=dict(inventory_sha256=inventory_hash,
                                  corpus_review_status=doc['corpus_review_status'],
                                  primary_body_source=primary_path,
                                  sources=[r for r, _ in verified_records]))
    return parsed


def _publish(output, corpus):
    if not output.parent.is_dir():
        raise ValueError('Corpus output directory is missing')
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output.parent,
                                         prefix=output.name + '.', suffix='.tmp', delete=False) as stream:
            name = stream.name
            json.dump(corpus, stream, ensure_ascii=False, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, output)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _paragraphs_to_html(text_blocks):
    paras, appendix_paras = [], []
    in_appendix = False
    for block in text_blocks:
        for line in block.splitlines():
            s = line.strip()
            if not s or re.fullmatch(r'\d+', s) or s.startswith('CÔNG BÁO/Số'):
                continue
            if re.match(r'^Phụ lục\b', s, re.IGNORECASE):
                in_appendix = True
            target = appendix_paras if in_appendix else paras
            if not target or _PDF_HEAD.match(s):
                target.append(s)
            else:
                target[-1] += ' ' + s
    body_html = '<html><body>' + ''.join(f'<p>{html_mod.escape(p)}</p>' for p in paras) + '</body></html>'
    return body_html, appendix_paras


def _parse_staging_document(doc, records, root, inventory_hash, pdf_coverage):
    paths = doc['source_paths']
    primary_path = doc.get('primary_body_source') or paths[0]
    verified_records = []
    for rel_path in paths:
        rec = records[rel_path]
        fpath = _safe_path(root, rec['path'])
        raw_bytes = fpath.read_bytes()
        if hashlib.sha256(raw_bytes).hexdigest() != rec['sha256']:
            raise ValueError(f'{doc["code"]}: source hash mismatch')
        verified_records.append((rec, raw_bytes))
    record, content = next((r, b) for r, b in verified_records if r['path'] == primary_path)
    valid_from = doc.get('effective_on') or doc.get('issued_on')
    valid_to = doc.get('original_application_end_on', '9999-12-31')
    status = 'CONSOLIDATION_REFERENCE' if doc.get('document_role') else 'ACTIVE'
    extra_appendices = []

    if doc['id'] == 'QD_482_2026':
        valid_from, valid_to = '2026-03-27', '2026-04-15'
        fields = pdf_coverage['sample_review']['qd482_selected_review']['verified_fields']
        lines = [
            'Điều 1. Mức thuế bảo vệ môi trường, thuế giá trị gia tăng, thuế tiêu thụ đặc biệt đối với xăng, dầu và nhiên liệu bay',
            f'1. {fields["article_1_clause_1"]}',
            f'2. {fields["article_1_clause_2"]}',
            f'3. {fields["article_1_clause_3"]}',
            'Điều 2. Điều khoản thi hành',
            f'1. {fields["article_2_clause_1"]}',
            '2. Trong thời gian Quyết định này có hiệu lực thi hành:',
            f'b) {fields["article_2_clause_2_point_b"]}',
        ]
        html = '<html><body>' + ''.join(f'<p>{html_mod.escape(x)}</p>' for x in lines) + '</body></html>'
    elif doc['id'] == 'VBHN_25_2026':
        raw_text = json.loads(content.decode('utf-8'))['text']
        html, extra_appendices = _paragraphs_to_html([raw_text])
    elif record['kind'] in ('html', 'official_snapshot'):
        raw_str = content.decode('utf-8')
        html = json.loads(raw_str)['html'] if record['kind'] == 'official_snapshot' else raw_str
    elif record['kind'] in ('gazette_pdf', 'signed_pdf', 'official_pdf'):
        pages = [
            p['text']
            for p in sorted(pdf_coverage['pages'], key=lambda x: x['page'])
            if p['path'] == primary_path and p['job'] == 'pdf_text_page'
        ]
        if not pages:
            raise ValueError(f'{doc["code"]}: missing PDF coverage pages')
        html, extra_appendices = _paragraphs_to_html(pages)
    else:
        raise ValueError(f'{doc["code"]}: unsupported primary source kind {record["kind"]}')

    meta = dict(doc_id=doc['id'], code=doc['code'], title=doc['title'],
                valid_from=valid_from, valid_to=valid_to, status=status)
    parsed = parse_vbpl_document(html, meta)
    if not parsed.get('articles'):
        raise ValueError(f'{doc["code"]}: empty legal structure')
    if extra_appendices:
        parsed.setdefault('appendices', []).append(dict(
            id=f'{doc["id"]}_APPENDIX_PDF',
            title='Phụ lục',
            blocks=extra_appendices,
        ))
    approved_rules = [
        r for r in doc.get('provision_effective_dates', [])
        if isinstance(r, dict) and r.get('mapping_status') == 'approved'
    ]
    if approved_rules:
        _apply_provision_dates(parsed, approved_rules)
    parsed.update(
        issue_date=doc['issued_on'],
        provision_effective_dates=doc.get('provision_effective_dates', []),
        provenance=dict(
            inventory_sha256=inventory_hash,
            corpus_review_status=doc.get('corpus_review_status', 'pending'),
            primary_body_source=primary_path,
            sources=[r for r, _ in verified_records],
        ),
    )
    return parsed


def build_staging_corpus(inventory, *, root, pdf_coverage_path=None, output=None):
    root = Path(root).resolve()
    inventory = Path(inventory)
    content = inventory.read_bytes()
    data = json.loads(content.decode('utf-8'))
    records = {r['path']: r for r in data['evidence_records'] if r.get('code')}
    cov_path = Path(pdf_coverage_path) if pdf_coverage_path else root / 'data/processed/document_pdf_coverage.json'
    pdf_coverage = json.loads(cov_path.read_text(encoding='utf-8'))
    inventory_hash = hashlib.sha256(content).hexdigest()
    corpus = [
        _parse_staging_document(doc, records, root, inventory_hash, pdf_coverage)
        for doc in data['documents']
    ]
    errors = validate_corpus(corpus)
    if errors:
        raise ValueError('\n'.join(errors))
    if output is not None:
        output = _safe_path(root, str(Path(output).relative_to(root)))
        protected = {
            inventory.resolve(),
            (root / 'data/processed/vat_corpus.json').resolve(),
            *(_safe_path(root, p).resolve() for p in records),
        }
        if output.resolve() in protected:
            raise ValueError('Staging output cannot overwrite canonical corpus or source evidence')
        _publish(output, corpus)
    return corpus


def build_corpus(inventory, *, root, output=None):
    root = Path(root).resolve()
    inventory = Path(inventory)
    content = inventory.read_bytes()
    data = json.loads(content.decode('utf-8'))
    records = _check_inventory(data)
    inventory_hash = hashlib.sha256(content).hexdigest()
    corpus = [_parse_document(doc, records, root, inventory_hash) for doc in data['documents']]
    errors = validate_corpus(corpus)
    if errors:
        raise ValueError('\n'.join(errors))
    if output is not None:
        output = _safe_path(root, str(Path(output).relative_to(root)))
        protected = {inventory.resolve(), *(_safe_path(root, p).resolve() for p in records)}
        if output.resolve() in protected:
            raise ValueError('Corpus output would overwrite source evidence')
        _publish(output, corpus)
    return corpus


def main():
    parser = argparse.ArgumentParser(description='Validate inventory before atomic corpus publication.')
    parser.add_argument('--check', action='store_true', help='Validate only; never replace corpus.')
    parser.add_argument('--staging', action='store_true', help='Build 33-document staging corpus to data/processed/vat_corpus.staging.json.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.staging:
            out = None if args.check else root / 'data/processed/vat_corpus.staging.json'
            corpus = build_staging_corpus(
                root / 'data/source_inventory.json',
                root=root,
                pdf_coverage_path=root / 'data/processed/document_pdf_coverage.json',
                output=out,
            )
            print(f'Built staging corpus: {len(corpus)} documents' + (' (check only)' if args.check else ' -> data/processed/vat_corpus.staging.json'))
            return 0
        corpus = build_corpus(root / 'data/source_inventory.json', root=root,
                              output=None if args.check else root / 'data/processed/vat_corpus.json')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'Corpus build blocked: {exc}', file=sys.stderr)
        return 1
    print(f'Validated {len(corpus)} documents' + (' (no publication)' if args.check else ' and published corpus'))
    return 0


if __name__ == '__main__':
    sys.exit(main())

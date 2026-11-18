from collections import Counter
import re
import xml.etree.ElementTree as ET

from .document_sources import (
    CT, DOCUMENT, R, W, _BoundedSink, _element_warnings, _extract_blocks,
    _relative_target, _relationships,
)


def _note_id(value):
    if value is None or re.fullmatch(r'[+-]?[0-9]+', value) is None:
        raise ValueError('Missing or invalid note ID')
    negative = value.startswith('-')
    digits = value.lstrip('+-').lstrip('0') or '0'
    return ('-' if negative and digits != '0' else '') + digits


def _note_parts(parts):
    targets = {}
    for plural in ('footnotes', 'endnotes'):
        relations = [r for r in _relationships(parts).values() if r.get('Type') == f'{R}/{plural}']
        if len(relations) > 1:
            raise ValueError(f'Duplicate {plural} relationships')
        if not relations or relations[0].get('TargetMode', 'Internal') != 'Internal':
            continue
        part = _relative_target(relations[0].get('Target', ''))
        root = parts.get(part)
        if root is None or root.tag != f'{{{W}}}{plural}':
            continue
        types = parts['[Content_Types].xml']
        overrides = [n.get('ContentType') for n in types
                     if n.tag == f'{{{CT}}}Override' and n.get('PartName') == '/' + part]
        defaults = [n.get('ContentType') for n in types if n.tag == f'{{{CT}}}Default'
                    and n.get('Extension') == part.rsplit('.', 1)[-1]]
        expected = f'application/vnd.openxmlformats-officedocument.wordprocessingml.{plural}+xml'
        if (overrides or defaults) == [expected]:
            targets[part] = plural[:-1]
    return targets


def _raw_xml(node, budget):
    sink = _BoundedSink(budget)
    ET.ElementTree(node).write(sink, encoding='unicode')
    return sink.getvalue()


def _notes(parts, targets, budget, warnings):
    notes = []
    lookup = {}
    for part, root in parts.items():
        if part not in targets:
            continue
        kind = targets[part]
        warnings.extend(_element_warnings(root, budget))
        for index, node in enumerate(root, 1):
            if node.tag != f'{{{W}}}{kind}':
                raise ValueError(f'Invalid {kind} entry')
            note_id = node.get(f'{{{W}}}id')
            key = (kind, _note_id(note_id))
            if key in lookup:
                raise ValueError(f'Duplicate {kind} IDs')
            note_type = node.get(f'{{{W}}}type')
            path = f'/w:{kind}s/w:{kind}[{index}]'
            entry = {
                'part': budget.consume(part), 'kind': budget.consume(kind),
                'note_id': budget.consume(note_id),
                'note_type': budget.consume(note_type) if note_type is not None else None,
                'locator': budget.consume(f'{part}:{path}'),
                'raw_xml': _raw_xml(node, budget),
                'blocks': _extract_blocks(node, budget, part, path),
            }
            notes.append(entry)
            lookup[key] = entry
    return notes, lookup


def _references(node, path):
    counts = Counter()
    for child in node:
        counts[child.tag] += 1
        name = 'w:' + child.tag.rsplit('}', 1)[-1] if child.tag.startswith(f'{{{W}}}') else child.tag
        child_path = f'{path}/{name}[{counts[child.tag]}]'
        if child.tag in {f'{{{W}}}footnoteReference', f'{{{W}}}endnoteReference'}:
            yield child, child_path
        yield from _references(child, child_path)


def extract_notes(parts, stories, budget, warnings):
    targets = _note_parts(parts)
    notes, lookup = _notes(parts, targets, budget, warnings)
    contexts = []
    used = set()
    sources = {DOCUMENT, *targets, *(s['part'] for s in stories)}
    for part, root in parts.items():
        if part not in sources:
            continue
        path = '/w:' + root.tag.rsplit('}', 1)[-1]
        for ref, ref_path in _references(root, path):
            kind = ref.tag.rsplit('}', 1)[-1].removesuffix('Reference')
            note_id = ref.get(f'{{{W}}}id')
            try:
                key = (kind, _note_id(note_id))
            except ValueError:
                key = None
            entry = lookup.get(key)
            resolved = entry is not None and entry['note_type'] in {None, 'normal'}
            contexts.append({
                'source_part': budget.consume(part), 'kind': budget.consume(kind),
                'note_id': budget.consume(note_id) if note_id is not None else None,
                'note_part': budget.consume(entry['part']) if resolved else None,
                'locator': budget.consume(f'{part}:{ref_path}'), 'raw_xml': _raw_xml(ref, budget),
            })
            if resolved:
                used.add(key)
            else:
                warnings.append(budget.consume(f'Unresolved {kind} reference ({kind}Reference): {note_id}'))
    for key, entry in lookup.items():
        if key not in used and entry['note_type'] not in {'separator', 'continuationSeparator', 'continuationNotice'}:
            warnings.append(budget.consume(f'Orphan {entry["kind"]} note: {entry["part"]}#{entry["note_id"]}'))
    return notes, contexts, targets

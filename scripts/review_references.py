"""Check the agent's per-page reference review; return delivery notes for chat only.

This validates coverage, freshness and artifact links, not semantic correctness.
The internal review record is never inserted into the meeting report.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote


RECORD = 'reference-review.json'
STATES = ('annotated', 'unnecessary', 'insufficient_evidence')


def load(path):
    return json.loads(path.read_text('utf-8-sig'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local(root, relative):
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ValueError('Review artifact must stay inside the meeting directory')
    return path


def context(root):
    paths = {'meeting-structure.json', 'transcript.jsonl', 'captures/slides.json',
             'captures/reading-order.json', 'captures/image-selection.json',
             'sections/manifest.json'}
    pages = []
    for group in load(root / 'sections/manifest.json')['presentations']:
        relative = 'sections/' + group['id'] + '.json'
        paths.add(relative)
        for page in load(local(root, relative))['pages']:
            pages.append(dict(presentation_id=group['id'], page_id=page['page_id'],
                              export_image=page['export_image'],
                              source_image='captures/' + page['image'],
                              display_intervals=page['display_intervals']))
            paths.update((page['export_image'], 'captures/' + page['image']))
    if not pages:
        raise ValueError('Export selected images before reference review')
    hashes = {p: digest(local(root, p)) for p in sorted(paths)}
    for p in ('captures/cursor-candidates.jsonl', 'captures/cursor-status.json'):
        path = local(root, p)
        hashes[p] = digest(path) if path.is_file() else None
    return pages, hashes


def key(row):
    return row['presentation_id'], row['page_id']


def initialize(root, reset=False):
    pages, hashes = context(root)
    path = root / RECORD
    if path.exists() and not reset:
        if load(path).get('inputs') != hashes:
            raise ValueError('Review inputs changed; recheck pages and init --reset')
        return path
    data = {'schema_version': 1, 'inputs': hashes,
            'pages': [dict(presentation_id=p['presentation_id'], page_id=p['page_id'],
                           status='pending', reason='', evidence=[]) for p in pages]}
    path.write_bytes((json.dumps(data, ensure_ascii=False, indent=2) + '\n').encode('utf8'))
    return path


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def check(root):
    pages, hashes = context(root)
    record = load(root / RECORD)
    if record.get('schema_version') != 1 or record.get('inputs') != hashes:
        raise ValueError('Stale reference review; recheck changed materials')
    rows = record['pages']
    if Counter(map(key, rows)) != Counter(map(key, pages)):
        raise ValueError('Reference review must cover each selected presenter/page exactly once')
    decisions = {key(r): r for r in rows}
    speech = {u['id']: u for u in (json.loads(s) for s in
              (root / 'transcript.jsonl').read_text('utf8').splitlines() if s.strip())}
    step = load(root / 'captures/slides.json')['sample_step_s']
    annotation_path = root / 'captures/annotated/annotations.json'
    annotations = load(annotation_path)['pages'] if annotation_path.exists() else []
    if len({p['page_id'] for p in annotations}) != len(annotations):
        raise ValueError('Duplicate annotated page')
    annotations = {p['page_id']: p for p in annotations}
    report = (root / '组会报告.md').read_text('utf8')
    # The generated local report uses Markdown image paths, before vault conversion.
    images = list(re.finditer(r'!\[[^\]\n]*\]\(([^)\n]+)\)', report))
    used = [local(root, unquote(m.group(1).strip('<>'))) for m in images]
    expected = []
    for index, page in enumerate(pages):
        row = decisions[key(page)]
        status = row.get('status')
        if status not in STATES or not isinstance(row.get('reason'), str) or not row['reason'].strip():
            raise ValueError('Pending or unexplained reference review: ' + str(key(page)))
        evidence = row.get('evidence', [])
        if status != 'unnecessary' and not evidence:
            raise ValueError('Timed speech evidence required: ' + str(key(page)))
        for e in evidence:
            t = e.get('time_s')
            u = speech.get(e.get('utterance_id'))
            if not finite(t) or not u or not u['start_s'] - step <= t <= u['end_s'] + step:
                raise ValueError('Invalid timed speech evidence: ' + str(key(page)))
            if not any(i['start_s'] <= t <= i['end_s'] for i in page['display_intervals']):
                raise ValueError('Evidence outside this presenter/page window')
        if status != 'annotated':
            expected.append(local(root, page['export_image']))
            continue
        annotated = annotations.get(page['page_id'])
        if not annotated or not annotated.get('annotations'):
            raise ValueError('Claimed annotation is missing: ' + page['page_id'])
        image = local(root, 'captures/' + annotated['image'])
        source = local(root, 'captures/' + annotated['source_image'])
        if source != local(root, page['source_image']):
            raise ValueError('Annotation uses a different source page')
        if digest(source) != annotated['source_sha256'] or digest(image) != annotated.get('image_sha256'):
            raise ValueError('Annotated source or output image changed; render again')
        expected.append(image)
        numbers = set()
        body = report[images[index].end():images[index + 1].start() if index + 1 < len(images) else len(report)] if index < len(images) else ''
        for point in annotated['annotations']:
            n = point['number']
            if point.get('reviewed') is not True or type(n) is not int or not 1 <= n <= 99 or n in numbers:
                raise ValueError('Invalid or unreviewed annotation number')
            numbers.add(n)
            if not any(e['time_s'] == point['time_s'] and e['utterance_id'] == point['utterance_id'] for e in evidence):
                raise ValueError('Annotation absent from reviewed evidence')
            sample = local(root, 'captures/' + point['sample'])
            if digest(sample) != point['sample_sha256'] or point.get('utterance') != speech.get(point['utterance_id']):
                raise ValueError('Annotation evidence changed; render again')
            token = chr(0x2460 + n - 1) if n <= 20 else '编号' + str(n)
            if token not in body:
                raise ValueError('Explain annotation ' + token + ' beside its image')
    if used != expected:
        raise ValueError('Report images differ from reviewed page order or numbered images')
    counts = Counter(r['status'] for r in rows)
    detail = lambda r: {k: r[k] for k in ('presentation_id', 'page_id', 'status', 'reason', 'evidence')}
    return {'total_pages': len(pages), 'counts': {s: counts[s] for s in STATES},
            'attention': [detail(r) for r in rows if r['status'] != 'unnecessary'],
            'unnecessary_examples': [detail(r) for r in rows if r['status'] == 'unnecessary'][:3],
            'review_record': str(root / RECORD), 'report_sha256': digest(root / '组会报告.md'),
            'review_sha256': digest(root / RECORD),
            'delivery': 'Summarize these checks in chat only; do not append them to the report.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'check'))
    parser.add_argument('meeting', type=Path)
    parser.add_argument('--reset', action='store_true', help='Replace stale decisions with pending entries')
    args = parser.parse_args()
    root = args.meeting.resolve()
    result = {'review_record': str(initialize(root, args.reset))} if args.action == 'init' else check(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))

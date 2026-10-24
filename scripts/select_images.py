"""Choose slide images directly; no PDF or presentation runtime is needed."""
import argparse,hashlib,json
from pathlib import Path


def load(path):return json.loads(path.read_text('utf8'))


def selection_rows(root,selected):
    data=load(root/'slides.json');reading=load(root/'reading-order.json')['pages']
    by_id={p['page_id']:p for p in reading}
    for page_id in selected:
        if page_id not in by_id:
            occurrences=[r for r in data['intervals'] if r['page_id']==page_id]
            if not occurrences:raise ValueError('Unknown selected page '+page_id)
            row=max(occurrences,key=lambda r:r['end_s']-r['start_s'])
            by_id[page_id]=dict(row,occurrence_ids=[r['id'] for r in occurrences])
    return [dict(by_id[p]) for p in selected]


def select_rows(root):
    destination=root/'image-selection.json'
    signature=hashlib.sha256((root/'slides.json').read_bytes()).hexdigest()
    if destination.exists():
        old=load(destination)
        if old.get('reviewed'):
            if old.get('slides_sha256')!=signature:
                raise ValueError('Capture metadata changed; review image-selection.json before reusing it')
            return selection_rows(root,old['included'])
    included=[];excluded=[]
    for row in load(root/'reading-order.json')['pages']:
        text=''.join(row.get('ocr','').split()).lower()
        editor=(sum(word in text for word in ['幻灯片放映','审阅','动画','切换','设计','插入'])>=3
                or 'officeplus' in text or 'acrobat' in text)
        (excluded if editor else included).append(row['page_id'])
    destination.write_bytes((json.dumps({'included':included,'excluded_editor':excluded,
        'reviewed':False,'slides_sha256':signature,
        'note':'Review short chapter pages and dynamic families against transcript; all raw captures remain available.'},
        ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    return selection_rows(root,included)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('captures',type=Path);a=p.parse_args()
    print('Image selection:',len(select_rows(a.captures.resolve())),'pages')

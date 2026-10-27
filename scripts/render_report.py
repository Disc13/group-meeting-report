"""Resolve reviewed Markdown slide references to direct image exports."""
import argparse,json,re
from collections import Counter
from pathlib import Path


def load(path):return json.loads(path.read_text('utf8'))


def render(root):
    groups=load(root/'sections/manifest.json')['presentations'];pages=[]
    for group in groups:
        pages += [dict(p,presentation_id=group['id']) for p in load(root/'sections'/(group['id']+'.json'))['pages']]
    expected=Counter(p['page_id'] for p in pages)
    sources={}
    annotated=root/'captures/annotated/annotations.json'
    if annotated.exists():
        for p in load(annotated)['pages']:sources[p['page_id']]='captures/'+p['image']
    review=root/'reference-review.json'
    decisions={(p['presentation_id'],p['page_id']):p['status'] for p in load(review)['pages']} if review.exists() else {}
    text=(root/'组会讲稿源稿.md').read_text('utf8')
    seen=Counter(re.findall(r'!\[[^\]\n]*\]\(slide:(capture-\d+)\)',text))
    if seen!=expected:raise ValueError('Narration page coverage differs from reviewed image selection: '+str(seen-expected)+' / '+str(expected-seen))
    if re.findall(r'!\[[^\]\n]*\]\(slide:(capture-\d+)\)',text)!=[p['page_id'] for p in pages]:
        raise ValueError('Narration images must follow the reviewed presenter and slide order')
    def replace(match):
        page=next(ordered)
        status=decisions.get((page['presentation_id'],page['page_id']))
        if status=='annotated' and page['page_id'] not in sources:
            raise ValueError('Missing numbered image for '+page['page_id'])
        relative=page['export_image']
        if status not in ('unnecessary','insufficient_evidence'):
            relative=sources.get(page['page_id'],relative)
        if not (root/relative).is_file():raise FileNotFoundError(relative)
        return match.group(0).replace('(slide:'+page['page_id']+')','('+relative+')')
    ordered=iter(pages)
    text=re.sub(r'!\[[^\]\n]*\]\(slide:(capture-\d+)\)',replace,text)
    if 'slide:' in text:raise ValueError('Unresolved slide reference')
    (root/'组会报告.md').write_bytes((text.rstrip()+'\n').encode('utf8'))
    print('Rendered illustrated report:',sum(seen.values()),'images')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('meeting',type=Path);a=p.parse_args();render(a.meeting.resolve())

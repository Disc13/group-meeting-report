"""Resolve reviewed Markdown slide references to direct image exports."""
import argparse,json,re
from collections import Counter
from pathlib import Path


def load(path):return json.loads(path.read_text('utf8'))


def render(root):
    groups=load(root/'sections/manifest.json')['presentations'];pages=[]
    for group in groups:pages+=load(root/'sections'/(group['id']+'.json'))['pages']
    expected=Counter(p['page_id'] for p in pages)
    sources={p['page_id']:p['export_image'] for p in pages}
    annotated=root/'captures/annotated/annotations.json'
    if annotated.exists():
        for p in load(annotated)['pages']:sources[p['page_id']]='captures/'+p['image']
    text=(root/'组会讲稿源稿.md').read_text('utf8')
    seen=Counter(re.findall(r'!\[[^\]\n]*\]\(slide:(capture-\d+)\)',text))
    if seen!=expected:raise ValueError('Narration page coverage differs from reviewed image selection: '+str(seen-expected)+' / '+str(expected-seen))
    if re.findall(r'!\[[^\]\n]*\]\(slide:(capture-\d+)\)',text)!=[p['page_id'] for p in pages]:
        raise ValueError('Narration images must follow the reviewed presenter and slide order')
    for page_id,relative in sources.items():
        if not (root/relative).is_file():raise FileNotFoundError(relative)
        text=text.replace('(slide:'+page_id+')','('+relative+')')
    if 'slide:' in text:raise ValueError('Unresolved slide reference')
    (root/'组会报告.md').write_bytes((text.rstrip()+'\n').encode('utf8'))
    print('Rendered illustrated report:',sum(seen.values()),'images')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('meeting',type=Path);a=p.parse_args();render(a.meeting.resolve())

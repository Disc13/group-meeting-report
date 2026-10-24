"""Side-by-side native-render/video-frame gallery for this reference deck."""
import argparse,html,json,os
from pathlib import Path


def run(root,reference):
    data=json.loads((root/'captures/slides.json').read_text('utf8'));pairs={}
    for row in data['intervals']:
        match=row.get('reference') or {};page=match.get('page')
        if page and (page not in pairs or match['inliers']>pairs[page]['reference']['inliers']):pairs[page]=row
    refpath=os.path.relpath(reference,root).replace('\\','/')
    blocks=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>原件与录像截图对比</title>',
            '<style>body{font-family:system-ui;margin:32px;background:#f4f4f2;color:#172922}article{margin:28px 0}section{display:grid;grid-template-columns:1fr 1fr;gap:16px}img{width:100%;background:white}p{color:#53655a}h1{font-size:28px}</style>',
            '<h1>原件与录像截图对比</h1><p>左侧为原件渲染，右侧为从录像自动提取的代表画面。</p>']
    for page,row in sorted(pairs.items()):
        blocks.append('<article><h2>原件第 %d 页 · 录像 %02d:%02d · %s</h2><section><img loading="lazy" src="%s/page-%03d.png"><img loading="lazy" src="captures/%s"></section></article>'%(
            page,row['start_s']//60,row['start_s']%60,row['page_id'],html.escape(refpath),page,html.escape(row['image'])))
    blocks.append('</html>');(root/'原件与录像对比.html').write_text(''.join(blocks),encoding='utf8')
    print('Comparison gallery:',len(pairs),'pages')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path);p.add_argument('reference',type=Path)
    a=p.parse_args();run(a.root,a.reference)

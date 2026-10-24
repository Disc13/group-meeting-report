"""Combine source text, OCR and timeline; select readable capture representatives."""
import argparse,collections,json,re
from pathlib import Path


def normalize(text):
    return ''.join(re.findall(r'[\w\u4e00-\u9fff]+',text)).lower()


def tokens(text):
    value=normalize(text)
    return {value[i:i+3] for i in range(max(0,len(value)-2))}


def run(root,reference,output=None):
    output=output or root
    output.mkdir(parents=True,exist_ok=True)
    data=json.loads((root/'slides.json').read_text('utf8'))
    if not reference:
        for row in data['intervals']:row['reference']=None
    ocr={}
    for path in (root/'ocr').glob('capture-*.json'):
        record=json.loads(path.read_text('utf-8-sig'));ocr[record['page_id']]=record['text']
    native={}
    if reference:
        native={p['page']:p for p in json.loads((reference/'deck.json').read_text('utf8'))['pages']}
    groups=[];excluded=[]
    for row in data['intervals']:
        text=ocr.get(row['page_id'],'');ref=row.get('reference') or {}
        if len(normalize(text))<12:
            excluded.append({'interval_id':row['id'],'reason':'little_readable_text','page_id':row['page_id']});continue
        signature=tokens(text)
        target=None
        for group in groups:
            if ref.get('page') and group['reference_page']==ref['page']:
                target=group;break
            if not ref.get('page') and not group['reference_page']:
                intersection=len(signature & group['tokens']);union=len(signature | group['tokens'])
                if union and intersection/union>.76:
                    target=group;break
        if target is None:
            target={'tokens':signature,'reference_page':ref.get('page'),'rows':[]};groups.append(target)
        target['rows'].append(row)
    selected=[]
    # Collapse sustained moving content under an unchanged page heading.
    # Require >=5 separate visual groups to avoid collapsing ordinary pairs
    # of slides sharing a title. All occurrence IDs/raw captures are retained.
    from capture_slides import read_image,descriptor,difference
    families=[]
    for group in groups:
        if group['reference_page']:
            families.append([group]);continue
        representative=group['rows'][0]
        group['header']=descriptor(read_image(root/representative['image']))[:24]
        first=min(row['start_s'] for row in group['rows'])
        family=next((family for family in families
                     if not family[0]['reference_page'] and 'header' in family[0]
                     and difference(group['header'],family[0]['header'])<.002
                     and abs(first-max(r['start_s'] for g in family for r in g['rows']))<180),None)
        if family is None:families.append([group])
        else:family.append(group)
    groups=[]
    for family in families:
        if len(family)>=5:
            family[0]['rows']=[row for group in family for row in group['rows']]
            family[0]['moving_content_family']=True
            groups.append(family[0])
        else:groups.extend(family)
    for group in groups:
        def quality(row):
            text=normalize(ocr.get(row['page_id'],''))
            editor=sum(word in text for word in ['幻灯片放映','审阅','动画','切换','设计','插入'])>=3
            return (not editor,row['crop_method']=='bright_rectangle',
                    row['stable'],(row.get('reference') or {}).get('inliers',0),row['end_s']-row['start_s'])
        best=max(group['rows'],key=quality)
        selected.append(dict(best,first_shown_s=min(r['start_s'] for r in group['rows']),
                             moving_content_family=group.get('moving_content_family',False),
                             occurrence_ids=[r['id'] for r in group['rows']],ocr=ocr.get(best['page_id'],'')))
    selected.sort(key=lambda r:r['first_shown_s'])
    (output/'reading-order.json').write_text(json.dumps({'pages':selected,'excluded':excluded,
        'note':'Reading selection uses OCR trigram overlap; original captures and every interval remain available.'},ensure_ascii=False,indent=2),encoding='utf8')
    reference_pages=sorted({r['reference']['page'] for r in data['intervals'] if r.get('reference') and r['reference'].get('page')})
    comparison={'source_pages':len(native),'matched_source_pages':reference_pages,
                'missing_source_pages':sorted(set(native)-set(reference_pages)),
                'raw_capture_pages':data['unique_pages'],'reading_pages':len(selected),
                'sampling_uncertainty_s':data['timing_uncertainty_s'],
                'note':'Coverage is verified for this supplied deck; other decks have no ground truth file.'}
    (output/'comparison.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2),encoding='utf8')
    transcript=[json.loads(s) for s in (root.parent/'transcript.jsonl').read_text('utf8').splitlines()]
    lines=['# 组会材料包','', '原 PPT 页码只用于通过内容与上下文验证的匹配；其余使用截图编号。','']
    for row in selected:
        ref=row.get('reference') or {};source=native.get(ref.get('page'),{})
        title=next(iter(source.get('text','').splitlines()),'') or row['ocr'][:70]
        lines.extend(['## %s · %s'%(row['page_id'],title),'',
          '首次展示 %.1f 秒；原件页码 %s；再次展示区间 %s'%(row['first_shown_s'],ref.get('page') or '未提供/未匹配',', '.join(row['occurrence_ids'])),
          '', '![页面](%s)'%row['image'],'','### 页面文字（原件优先，OCR 需回查）','',source.get('text') or row['ocr'],''])
        if source.get('notes'):lines.extend(['### 演讲者备注','',source['notes'],''])
        lines.extend(['### 对应发言',''])
        for interval in data['intervals']:
            if interval['id'] not in row['occurrence_ids']:continue
            for utterance in transcript:
                if utterance['start_s']<interval['end_s'] and utterance['end_s']>interval['start_s']:
                    lines.append('[%.3f–%.3f] %s：%s'%(utterance['start_s'],utterance['end_s'],utterance['speaker'],utterance['text']))
        lines.append('')
    if output!=root:
        import os
        image_prefix=os.path.relpath(root,output).replace('\\','/')+'/'
        lines=[line.replace('](pages/',']('+image_prefix+'pages/') for line in lines]
    (output/'material-packet.md').write_text('\n'.join(lines),encoding='utf8')
    print(json.dumps(comparison,ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('captures',type=Path);p.add_argument('--reference',type=Path);p.add_argument('--out',type=Path)
    a=p.parse_args();run(a.captures,a.reference,a.out)

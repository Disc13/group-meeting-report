"""Group screenshot representatives and discussion material by presenter.

The reviewed structure supplies boundaries. Page assignment uses every recorded
occurrence (including reading-family members), never a global first-shown time.
"""
import argparse,hashlib,json,re,shutil,subprocess,sys
from pathlib import Path


def load(path):
    return json.loads(path.read_text('utf8'))


def stamp(seconds):
    seconds=int(seconds)
    return '%02d:%02d:%02d'%(seconds//3600,seconds//60%60,seconds%60)


def selected_groups(root,structure):
    captures=root/'captures';data=load(captures/'slides.json')
    reading=load(captures/'reading-order.json')['pages']
    selection=captures/'image-selection.json'
    selection_data=load(selection if selection.exists() else captures/'pdf-selection.json')
    if selection_data.get('reviewed') and selection_data.get('slides_sha256')!=hashlib.sha256((captures/'slides.json').read_bytes()).hexdigest():
        raise ValueError('Capture metadata changed; review the image selection before export')
    selected=selection_data['included']
    # Short chapter pages can be absent from the browsing representatives.
    known={p['page_id'] for p in reading}
    for page_id in selected:
        if page_id not in known:
            occurrences=[r for r in data['intervals'] if r['page_id']==page_id]
            if not occurrences:raise ValueError('Unknown selected page '+page_id)
            row=max(occurrences,key=lambda r:r['end_s']-r['start_s'])
            reading.append(dict(row,occurrence_ids=[r['id'] for r in occurrences]))
    for row in reading:
        family=selection_data.get('families',{}).get(row['page_id'],[])
        if family:
            row['occurrence_ids']=list(dict.fromkeys(row.get('occurrence_ids',[])+
                 [r['id'] for r in data['intervals'] if r['page_id'] in family]))
    return group_pages(structure,reading,data['intervals'],selected)


def export_images(root,group):
    folder=root/'output/images'/group['file_stem'];folder.mkdir(parents=True,exist_ok=True)
    lines=['# '+group['presenter']+'：'+group['title'],'','按首次讲解顺序排列；时间从录像起点计。','']
    for n,page in enumerate(group['pages'],1):
        source=root/'captures'/page['image']
        reference=page.get('reference') or {}
        if group.get('reference_dir') and reference.get('status')=='matched' and reference.get('page'):
            rendered=root/group['reference_dir']/('page-%03d.png'%reference['page'])
            if rendered.is_file():
                source=rendered
                page['rendered_image']=source.relative_to(root).as_posix()
        destination=folder/('%03d-%s%s'%(n,page['page_id'],source.suffix.lower()))
        if not destination.exists() or destination.read_bytes()!=source.read_bytes():shutil.copyfile(str(source),str(destination))
        page['export_image']=destination.relative_to(root).as_posix()
        page['image_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        lines+=['## %02d · %s · %s'%(n,page['page_id'],stamp(page['first_shown_in_section_s'])),
                '', '![%s](%s)'%(page['page_id'],destination.name),'']
    index=folder/'index.md';index.write_bytes(('\n'.join(lines)+'\n').encode('utf8'))
    group['images']=folder.relative_to(root).as_posix()
    group['image_index']=index.relative_to(root).as_posix()


def group_pages(structure,reading,intervals,selected):
    groups=[]
    sections=structure['presentations']
    if not sections:raise ValueError('No presentations in structure')
    identifiers=[s['id'] for s in sections]
    if len(identifiers)!=len(set(identifiers)):raise ValueError('Duplicate presentation id')
    for i,section in enumerate(sections):
        start,end=section['material_start_s'],section['material_end_s']
        if start>=end:raise ValueError('Invalid material interval')
        if i and sections[i-1]['material_end_s']>start:raise ValueError('Overlapping presentation windows')
        if not re.fullmatch(r'[a-z0-9-]+',section['id']):raise ValueError('Unsafe section id')
        if any(c in section['file_stem'] for c in '/\\:') or section['file_stem'] in ('.','..'):
            raise ValueError('Unsafe file stem')
        pages=[]
        for row in reading:
            if row['page_id'] not in selected:continue
            family=set(row.get('occurrence_ids',[]))
            occurrences=[x for x in intervals if x['id'] in family or x['page_id']==row['page_id']]
            overlap=[{'interval_id':x['id'],'source_page_id':x['page_id'],
                      'start_s':max(start,x['start_s']),'end_s':min(end,x['end_s'])}
                     for x in occurrences if x['start_s']<end and x['end_s']>start]
            if not overlap:continue
            pages.append(dict(row,display_intervals=overlap,
                              first_shown_in_section_s=min(x['start_s'] for x in overlap)))
        pages.sort(key=lambda r:r['first_shown_in_section_s'])
        if not pages:raise ValueError('Empty presentation: '+section['id'])
        groups.append(dict(section,pages=pages))
    unassigned=set(selected)-{r['page_id'] for g in groups for r in g['pages']}
    if unassigned:raise ValueError('Selected pages outside all presentation windows: '+str(sorted(unassigned)))
    return groups


def run(a):
    root=a.root.resolve();captures=root/'captures'
    structure_path=(a.structure or root/'meeting-structure.json').resolve()
    structure=load(structure_path)
    groups=selected_groups(root,structure)
    private=root/'sections';private.mkdir(exist_ok=True)
    outputs=[]
    for group in groups:
        selection=private/(group['id']+'.json')
        old=load(selection) if selection.exists() else {}
        # Existing published PPTX is retained when only PDF/materials are refreshed.
        same_pages=[(r['page_id'],r.get('display_intervals')) for r in old.get('pages',[])]==[
            (r['page_id'],r.get('display_intervals')) for r in group['pages']]
        for kind in ('pdf','pptx'):
            if same_pages and old.get(kind) and (root/old[kind]).exists():group[kind]=old[kind]
        export_images(root,group)
        if a.pdf:
            from export_capture_pdf import write_pdf
            pdf=root/'output/pdf'/(''+group['file_stem']+'.pdf')
            write_pdf(captures,pdf,group['pages'],group['presenter']+' - '+group['title'])
            group['pdf']=pdf.relative_to(root).as_posix()
        selection.write_text(json.dumps(group,ensure_ascii=False,indent=2),encoding='utf8')
        if a.pptx:
            if not a.node_modules or not a.presentation_skill:
                raise ValueError('PPTX export needs --node-modules and --presentation-skill; direct images do not')
            subprocess.run([str(a.node),str(Path(__file__).with_name('export_capture_pptx.mjs')),
                            str(root),str(a.node_modules),str(a.pdf_python),str(a.presentation_skill),str(selection)],check=True)
            group=load(selection)
        outputs.append({k:v for k,v in group.items() if k!='pages'})
        outputs[-1]['page_count']=len(group['pages'])
        print(group['presenter'],len(group['pages']),'pages')
    manifest={'structure':str(structure_path),'presentations':outputs,
              'unique_selected_pages':len({p['page_id'] for g in groups for p in g['pages']}),
              'exported_pages':sum(len(g['pages']) for g in groups),
              'note':'A shared representative may appear in multiple presentations only if it has actual occurrences in both.'}
    (root/'sections/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    lines=['# 分人汇报材料','','[组会报告](组会报告.md) · [完整逐字稿](transcript.md)','','| 汇报人 | 内容 | 代表页 | 文件 |','|---|---|---:|---|']
    for group in outputs:
        links=['[逐页图片](%s)'%group['image_index']]+['[%s](%s)'%(kind.upper(),group[kind]) for kind in ('pdf','pptx') if kind in group]
        lines.append('| %s | %s | %d | %s |'%(group['presenter'],group['title'],group['page_count'],' · '.join(links)))
    lines+=['','默认直接输出逐页图片，保留原截图格式，不经过PDF/PPTX中转。PDF和截图PPTX仅按需导出。',
            '同页多次出现按本人的所有展示区间归属；问答中的回翻证据仍保存在原始截图与对齐材料包。',
            '仅对实际提供的原件核对覆盖；录像代表页数不等于未提供原件的总页数。导师共同事项另见报告末节。']
    (root/'分人汇报材料.md').write_text('\n'.join(lines)+'\n',encoding='utf8')
    state_path=root/'workflow.json'
    if state_path.exists():
        state=load(state_path);state['meeting_structure']=structure_path.name
        state['presenter_outputs']='sections/manifest.json'
        state['outputs']=list(dict.fromkeys(state.get('outputs',[])+['分人汇报材料.md']+
                             [g[k] for g in outputs for k in ('image_index','pdf','pptx') if k in g]))
        state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    p.add_argument('--structure',type=Path);p.add_argument('--pdf',action='store_true');p.add_argument('--pptx',action='store_true')
    p.add_argument('--node',default='node')
    p.add_argument('--node-modules',type=Path)
    p.add_argument('--pdf-python',default=sys.executable)
    p.add_argument('--presentation-skill',type=Path)
    run(p.parse_args())

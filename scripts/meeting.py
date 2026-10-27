"""Portable entry point: sources -> agent handoff -> report/cards/optional vault."""
import argparse,hashlib,importlib.util,json,os,re,shutil,subprocess,sys
from pathlib import Path
from urllib.parse import urlparse

WORKFLOW=Path(__file__).resolve().parent


def load(path):return json.loads(path.read_text('utf8'))


def share_key(source):
    parsed=urlparse(source)
    match=re.fullmatch(r'/(?:cw|crm)/([A-Za-z0-9_-]+)/?',parsed.path)
    if parsed.scheme!='https' or parsed.netloc.lower()!='meeting.tencent.com' or not match:
        raise ValueError('Expected https://meeting.tencent.com/cw/ID or /crm/ID')
    return match[1]


def choose_root(source,work_root,requested=None):
    key=share_key(source)
    if requested:
        root=requested.resolve()
        for filename in ('manifest.json','workflow.json'):
            if (root/filename).exists():
                saved=load(root/filename).get('source')
                if saved and share_key(saved)!=key:raise ValueError('Output belongs to a different meeting')
        return root
    candidates=[]
    for pattern in ('*/manifest.json','meetings/*/manifest.json'):
        for path in work_root.glob(pattern):
            saved=load(path).get('source','')
            try:same=share_key(saved)==key
            except ValueError:same=False
            if same:candidates.append(path)
    return max(candidates,key=lambda p:p.stat().st_mtime).parent if candidates else work_root/'meetings'/key


def runtime(config,work_root=None):
    return dict(config,workflow_root=WORKFLOW,work_root=Path(work_root or config.get('work_root') or Path.cwd()).resolve(),
        python=config.get('python') or sys.executable,ffmpeg=config.get('ffmpeg') or shutil.which('ffmpeg') or 'ffmpeg',
        cv_deps=config.get('cv_deps'),node=config.get('node') or shutil.which('node') or 'node',
        node_modules=config.get('node_modules'),pdf_python=config.get('pdf_python') or sys.executable,
        presentation_skill=config.get('presentation_skill'),soffice=config.get('soffice'))


def environment(config):
    env=os.environ.copy();env['PYTHONIOENCODING']='utf-8'
    if config.get('cv_deps'):env['PYTHONPATH']=str(config['cv_deps'])+os.pathsep+env.get('PYTHONPATH','')
    executable=Path(config['ffmpeg'])
    if executable.is_file():env['PATH']=str(executable.resolve().parent)+os.pathsep+env.get('PATH','')
    return env


def command(args,config):
    subprocess.run([str(a) for a in args],cwd=WORKFLOW,env=environment(config),check=True)


def module(name):
    spec=importlib.util.spec_from_file_location(name,WORKFLOW/(name+'.py'))
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


def material_ready(root):
    required=['manifest.json','transcript.md','transcript.jsonl','captures/slides.json','captures/reading-order.json',
              'captures/material-packet.md','captures/aligned.jsonl','captures/cursor-status.json','captures/image-selection.json']
    return all((root/p).is_file() for p in required) and load(root/'manifest.json').get('complete_as_returned') is True


def prepare(a,config):
    root=choose_root(a.source,config['work_root'],a.out)
    if a.deck and not a.deck.is_file():raise FileNotFoundError(a.deck)
    same_deck=False
    if a.deck and (root/'workflow.json').exists():
        reference=load(root/'workflow.json').get('reference')
        if reference and (Path(reference)/'deck.json').is_file():
            same_deck=load(Path(reference)/'deck.json').get('sha256')==hashlib.sha256(a.deck.read_bytes()).hexdigest()
            same_deck=same_deck and all((Path(reference)/('page-%03d.png'%p['page'])).is_file() for p in load(Path(reference)/'deck.json')['pages'])
    ready=material_ready(root) and not a.refresh and (not a.deck or same_deck)
    if ready and a.video:ready=Path(load(root/'captures/slides.json')['source_video']).resolve()==a.video.resolve()
    if ready:module('run_workflow').write_agent_instructions(WORKFLOW,root)
    else:
        args=[config['python'],WORKFLOW/'run_workflow.py',a.source,'--out',root,'--materials-only']
        for key,flag in [('cv_deps','--cv-deps'),('node','--node'),('node_modules','--node-modules'),
                         ('pdf_python','--pdf-python'),('presentation_skill','--presentation-skill'),('soffice','--soffice')]:
            if config.get(key):args += [flag,config[key]]
        if a.deck:args+=['--deck',a.deck.resolve()]
        video=a.video
        if not video and (root/'workflow.json').exists():
            old=load(root/'workflow.json').get('video')
            if old and Path(old).is_file():video=Path(old)
        if video:args+=['--video',video.resolve()]
        if a.refresh:args+=['--refresh-transcript']
        command(args,config)
    print(json.dumps({'meeting_dir':str(root),'reused_materials':ready,'agent_instructions':str(root/'RUN_AGENT.md'),
        'next':'Read evidence and RUN_AGENT.md; write structure, illustrated report and insights, then finish.'},ensure_ascii=False,indent=2))


def exports_ready(root):
    if not (root/'sections/manifest.json').is_file():return False
    groups=load(root/'sections/manifest.json')['presentations'];sections=load(root/'meeting-structure.json')['presentations']
    fields=('id','presenter','material_start_s','material_end_s')
    if [tuple(g[k] for k in fields) for g in groups]!=[tuple(s[k] for k in fields) for s in sections]:return False
    if not all(g.get('image_index') and (root/g['image_index']).is_file() for g in groups):return False
    expected=module('split_meeting').selected_groups(root,load(root/'meeting-structure.json'))
    def signature(rows):return [(r['page_id'],r['image'],r.get('display_intervals'),(r.get('reference') or {}).get('page')) for r in rows]
    for group,reviewed in zip(groups,expected):
        selection=root/'sections'/(group['id']+'.json')
        if not selection.is_file() or signature(load(selection)['pages'])!=signature(reviewed['pages']):return False
        for page in load(selection)['pages']:
            exported=root/page.get('export_image','__missing__')
            source=root/page['rendered_image'] if page.get('rendered_image') else root/'captures'/page['image']
            if not source.is_file() or not exported.is_file() or exported.read_bytes()!=source.read_bytes():return False
    return True


def finish(a,config):
    root=a.meeting.resolve()
    for filename in ('meeting-structure.json','组会报告.md','人物汇报提要.json'):
        if not (root/filename).is_file():raise FileNotFoundError('Agent must finish '+str(root/filename))
    if not exports_ready(root) or a.rebuild_slides:
        if a.check_only:raise ValueError('Image exports are missing or changed')
        command([config['python'],WORKFLOW/'split_meeting.py',root],config)
    reference_review=module('review_references').check(root)
    if not a.check_only:command([config['python'],WORKFLOW/'people_cards.py',root],config)
    vault=a.vault or config.get('vault')
    if vault:
        args=[config['python'],WORKFLOW/'sync_obsidian.py',root,'--vault',Path(vault).resolve()]
        command(args,config)
        if not a.check_only:command(args+['--apply'],config)
    if not a.check_only:
        path=root/'workflow.json';state=load(path) if path.exists() else {}
        state.update(report_generation='agent-reviewed illustrated speech and Q&A',report_exists=True,
                     status='completed_for_supplied_materials',research_cards='人物名片.md',
                     reference_review={k:reference_review[k] for k in ('counts','report_sha256','review_sha256')})
        path.write_bytes((json.dumps(state,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
    print(json.dumps({'report':str(root/'组会报告.md'),'cards':str(root/'人物名片.md'),
                      'annotation_review_for_chat':reference_review,
                      'obsidian_written':bool(vault) and not a.check_only},ensure_ascii=False,indent=2))


def doctor(config):
    checks={'python':bool(shutil.which(config['python'])),'ffmpeg':bool(shutil.which(config['ffmpeg']))}
    command([config['python'],'-c','import cv2,numpy,PIL; print("CV runtime available")'],config)
    print(json.dumps({'required':checks,'windows_ocr':os.name=='nt',
        'tesseract_available':bool(shutil.which('tesseract')),'libreoffice_available':bool(config.get('soffice') or shutil.which('soffice')),
        'artifact_renderer_configured':bool(config.get('node_modules'))},indent=2))
    if not all(checks.values()):raise SystemExit('Install required dependencies; see README')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path);p.add_argument('--work-root',type=Path)
    sub=p.add_subparsers(dest='action',required=True);sub.add_parser('doctor')
    prep=sub.add_parser('prepare');prep.add_argument('source');prep.add_argument('--out',type=Path)
    prep.add_argument('--deck',type=Path);prep.add_argument('--video',type=Path);prep.add_argument('--refresh',action='store_true')
    end=sub.add_parser('finish');end.add_argument('meeting',type=Path);end.add_argument('--vault',type=Path)
    end.add_argument('--check-only',action='store_true');end.add_argument('--rebuild-slides',action='store_true')
    a=p.parse_args();config=runtime(load(a.config) if a.config else {},a.work_root)
    {'prepare':prepare,'finish':finish,'doctor':lambda a,c:doctor(c)}[a.action](a,config)


if __name__=='__main__':main()

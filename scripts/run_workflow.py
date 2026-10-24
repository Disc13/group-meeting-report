"""Reproducible acquisition -> video pages/OCR -> aligned agent material pack.

The final report is an agent step using RUN_AGENT.md, not a template summary.
"""
import argparse,hashlib,json,os,subprocess,sys
from pathlib import Path


def command(arguments,env=None):
    subprocess.run([str(a) for a in arguments],check=True,env=env)


def write_agent_instructions(tools,root):
    prompt=(tools/'agent-report-prompt.md').read_text('utf8')
    (root/'RUN_AGENT.md').write_text(
        '# 生成本次组会图文讲稿\n\n先核实meeting-structure.json及实际汇报顺序，缺失时根据材料建立，再直接导出分人图片。'
        '读取 transcript.md、captures/material-packet.md、captures/comparison.json 和 captures/cursor-status.json；'
        '有原件时再读参考目录的 deck-text.md。输出组会报告.md。'
        '重要数字回查页面图片，注意时间、页码、现场建议和实际完成结果。\n\n'+prompt,encoding='utf8')


def run(a):
    tools=Path(__file__).resolve().parent
    a.out=a.out.resolve();a.out.mkdir(parents=True,exist_ok=True)
    environment=os.environ.copy()
    if a.cv_deps:
        environment['PYTHONPATH']=str(a.cv_deps.resolve())+os.pathsep+environment.get('PYTHONPATH','')
    python=Path(sys.executable)
    offline=(a.out/'minutes-response.json').exists() and not a.refresh_transcript
    command([python,tools/'tencent_transcript.py',a.source,'--out',a.out]+(['--offline'] if offline else []),environment)
    if a.video:
        video=a.video.resolve()
    else:
        import tmdown
        net=tmdown.Net();subject,tracks=tmdown.resolve_tencent(net,a.source,want={'screen'})
        if len(tracks)!=1:raise ValueError('Choose a single recording explicitly using --video')
        track=tracks[0];inputs=a.out/'inputs';inputs.mkdir(exist_ok=True)
        video=inputs/(tmdown.sanitize(subject)+'-共享屏幕.mp4')
        receipt=inputs/'download.json'
        if not (video.exists() and receipt.exists() and json.loads(receipt.read_text('utf8')).get('size')==video.stat().st_size):
            tmdown.download_direct(net,track['url'],str(video),size_hint=track['size'],
                                  refresh=lambda:tmdown.resign_tencent(net,track),
                                  source_id='tencent:%s:%s'%(track['record_id'],track['resource_type']))
            receipt.write_text(json.dumps({'size':video.stat().st_size,'record_id':track['record_id']}),encoding='utf8')
    reference=None
    if a.deck:
        source_hash=hashlib.sha256(a.deck.read_bytes()).hexdigest()
        reference=a.reference_dir or a.out/('reference-'+source_hash[:8])
        reference=reference.resolve()
        cache=reference/'deck.json'
        rendered=(cache.exists() and json.loads(cache.read_text('utf8')).get('sha256')==source_hash
                  and len(list(reference.glob('page-*.png')))==len(json.loads(cache.read_text('utf8'))['pages']))
        command([python,tools/'import_deck.py',a.deck.resolve(),'--out',reference],environment)
        if a.deck.suffix.lower()=='.pptx' and not rendered:
            rendering=[python,tools/'render_deck.py',a.deck.resolve(),'--out',reference]
            if a.node_modules:rendering+=['--node',a.node,'--node-modules',a.node_modules]
            if a.soffice:rendering+=['--soffice',a.soffice]
            command(rendering,environment)
    captures=a.out/'captures'
    extraction=[python,tools/'capture_slides.py',video,'--out',captures,'--transcript',a.out/'transcript.jsonl']
    if reference:extraction.extend(['--reference',reference])
    command(extraction,environment)
    command([python,tools/'ocr_pages.py',captures/'pages','--out',captures/'ocr'],environment)
    command([python,tools/'build_materials.py',captures,'--out',a.out/'video-only'],environment)
    if reference:command([python,tools/'build_materials.py',captures,'--reference',reference],environment)
    else:command([python,tools/'build_materials.py',captures],environment)
    command([python,tools/'track_cursor.py',captures],environment)
    command([python,tools/'select_images.py',captures],environment)
    if a.pdf and not a.materials_only:
        command([a.pdf_python,tools/'export_capture_pdf.py',captures,a.out/'录像幻灯片集.pdf'],environment)
    if reference:command([python,tools/'build_comparison.py',a.out,reference],environment)
    structure=(a.structure or a.out/'meeting-structure.json').resolve()
    if a.structure and not structure.exists():raise FileNotFoundError(structure)
    if structure.exists() and not a.materials_only:
        grouped=[python,tools/'split_meeting.py',a.out,'--structure',structure,'--node',a.node,'--pdf-python',a.pdf_python]
        if a.node_modules:grouped+=['--node-modules',a.node_modules]
        if a.presentation_skill:grouped+=['--presentation-skill',a.presentation_skill]
        command(grouped+(['--pdf'] if a.pdf else [])+(['--pptx'] if a.pptx else []),environment)
    elif a.pptx and not a.materials_only:
        command([a.node,tools/'export_capture_pptx.mjs',a.out,a.node_modules,a.pdf_python,a.presentation_skill],environment)
    write_agent_instructions(tools,a.out)
    state_path=a.out/'workflow.json'
    state=json.loads(state_path.read_text('utf8')) if state_path.exists() else {}
    state.update({'source':a.source,'video':str(video),'reference':str(reference) if reference else None,
           'material_generation':'completed','report_generation':'agent step; see RUN_AGENT.md',
           'report_exists':(a.out/'组会报告.md').exists(),
           'meeting_structure':str(structure) if structure.exists() else 'agent must identify actual presenter sections',
        'report_structure':'illustrated page-by-page polished speech and Q&A per presenter; advisor opening and closing separate'})
    state_path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(state,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source');parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--video',type=Path);parser.add_argument('--deck',type=Path);parser.add_argument('--reference-dir',type=Path)
    parser.add_argument('--refresh-transcript',action='store_true');parser.add_argument('--cv-deps',type=Path)
    parser.add_argument('--node',default='node')
    parser.add_argument('--node-modules',type=Path)
    parser.add_argument('--pdf-python',default=sys.executable)
    parser.add_argument('--soffice',help='LibreOffice executable for PPTX rendering')
    parser.add_argument('--pdf',action='store_true',help='Optionally export a PDF reading collection')
    parser.add_argument('--pptx',action='store_true',help='Optionally export an image-based PPTX after validation')
    parser.add_argument('--materials-only',action='store_true',help='Prepare sources and image candidates; defer presenter exports until the agent reviews the split')
    parser.add_argument('--structure',type=Path,help='Reviewed presenter boundaries; defaults to OUT/meeting-structure.json when present')
    parser.add_argument('--presentation-skill',type=Path,help='Optional installed presentations skill for validated PPTX export')
    run(parser.parse_args())

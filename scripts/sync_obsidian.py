"""Publish a reviewed meeting report and accumulate evidence-based person notes.

No model calls, plugin changes, network requests, Git commits or video copies.
The agent supplies reviewed presenter insights; this script handles paths,
attachments, math delimiters, reciprocal links and repeatable updates.
"""
import argparse,hashlib,json,os,re
from pathlib import Path
from urllib.parse import unquote
from people_cards import card_fields,card_lines,meeting_cards


def read_json(path):
    return json.loads(path.read_text('utf8'))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(text):
    return (text.rstrip()+'\n').encode('utf8')


def block_hash(text):
    pattern=r'<!-- meeting-workflow:[^:]+:start -->[\s\S]*?<!-- meeting-workflow:[^:]+:end -->'
    blocks='\n'.join(re.findall(pattern,text.replace('\r\n','\n')))
    return sha(blocks.encode('utf8')) if blocks else None


def safe_path(root,relative):
    relative=Path(relative)
    if relative.is_absolute() or any(p in ('..','.') for p in relative.parts):
        raise ValueError('Expected a safe relative path: '+str(relative))
    target=(root/relative).resolve()
    if root.resolve() not in target.parents:raise ValueError('Path outside root: '+str(target))
    return target


def wiki(target,label=None,heading=None):
    dest=target.as_posix() if isinstance(target,Path) else target
    if dest.endswith('.md'):dest=dest[:-3]
    if heading:dest+='#'+heading
    return '[['+dest+('|' +label if label else '')+']]'


def block(text,key,content,initial=None):
    start='<!-- meeting-workflow:%s:start -->'%key
    end='<!-- meeting-workflow:%s:end -->'%key
    fresh=start+'\n'+content.rstrip()+'\n'+end
    if start in text or end in text:
        if text.count(start)!=1 or text.count(end)!=1 or text.index(start)>=text.index(end):
            raise ValueError('Invalid managed block '+key)
        before,middle=text.split(start,1);_,after=middle.split(end,1)
        return before+fresh+after
    if text and initial is None:raise ValueError('Existing note has no managed block: '+key)
    return (initial or '')+fresh+'\n'


def plan_export(root,config):
    vault=Path(config['vault']).resolve()
    if not vault.is_dir():raise ValueError('Expected an existing, explicitly selected vault directory')
    event=read_json(root/'人物汇报提要.json')
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',event['meeting_id']):raise ValueError('Invalid meeting id')
    for field in ('meeting_dir','people_dir','assets_dir'):safe_path(vault,config[field])
    meeting_dir=Path(config['meeting_dir']);people_dir=Path(config['people_dir'])
    assets=Path(config['assets_dir'])/event['meeting_id']
    report_path=meeting_dir/(event['title']+'.md');materials_path=assets/'汇报材料.md'
    source_report=(root/'组会报告.md').read_text('utf8')
    structure=read_json(root/'meeting-structure.json')
    actual={g['id']:g['presenter'] for g in structure['presentations']}
    if {p['presentation_id']:p['name'] for p in event['presenters']}!=actual:
        raise ValueError('Presenter insights and reviewed meeting structure disagree')
    for person in event['presenters']:
        if any(c in person['name'] for c in '/\\:*?"<>|[]#'):
            raise ValueError('Unsafe person name')
        if '\n## '+person['report_heading']+'\n' not in source_report:
            raise ValueError('Missing report heading '+person['report_heading'])
    event=dict(event,report_path=report_path.as_posix(),people_dir=people_dir.as_posix(),assets_base=config['assets_dir'])
    source_url=read_json(root/'workflow.json').get('source')
    if source_url:event['source']=source_url
    # A date is not a recording identity. Do not replace another meeting that
    # happens to use the same date/title.
    old_event_path=vault/assets/'人物汇报提要.json'
    if old_event_path.exists() and source_url:
        previous=read_json(old_event_path);old_source=previous.get('source')
        material_note=vault/assets/'汇报材料.md'
        if not old_source and material_note.exists():
            match=re.search(r'https://meeting\.tencent\.com/(?:cw|crm)/[A-Za-z0-9_-]+',material_note.read_text('utf8'))
            old_source=match.group(0) if match else None
        def key(url):
            match=re.search(r'/(?:cw|crm)/([^/?]+)',url or '')
            return match.group(1) if match else url
        if old_source and key(old_source)!=key(source_url):
            raise ValueError('Meeting date/id already belongs to another recording; resolve the identity before publishing')
    files={};refs=[]
    def put(relative,data):
        path=safe_path(vault,relative)
        if path in files and files[path]!=data:raise ValueError('Conflicting outputs '+str(path))
        files[path]=data
    def copy(source,relative):
        if not source.is_file():raise FileNotFoundError(source)
        data=source.read_bytes()
        if source.suffix.lower() in ('.md','.json'):
            text=data.decode('utf-8-sig').replace('\r\n','\n')
            if source.suffix.lower()=='.md':text='\n'.join(line.rstrip(' \t') for line in text.split('\n'))
            data=text.encode('utf8')
        put(relative,data);return relative
    # Keep the research narrative; replace its project-only evidence appendix.
    body=source_report.split('\n## 材料与核查范围\n',1)[0]
    body=body.replace(source_report.splitlines()[0],'# '+event['title'],1)
    body=body.replace('[分人汇报材料](分人汇报材料.md)',wiki(materials_path,'分人汇报材料'))
    def image_link(match):
        embed,label,raw=match.groups();source=(root/unquote(raw.strip('<>'))).resolve()
        if root not in source.parents:raise ValueError('Source citation outside meeting: '+str(source))
        relative=copy(source,assets/'图'/source.name);refs.append(relative)
        # Obsidian resolves native embeds from the vault, independent of note depth.
        return '!'+wiki(relative) if embed else wiki(relative,label)
    body=re.sub(r'(!?)\[([^\]\n]+)\]\(([^)]+\.(?:png|jpg|jpeg))\)',image_link,body)
    # Obsidian uses dollar delimiters; leave formulas and their conditions intact.
    body=body.replace('\\[','$$').replace('\\]','$$').replace('\\(','$').replace('\\)','$')
    for person in event['presenters']:
        heading='\n## '+person['report_heading']+'\n'
        body=body.replace(heading,heading+'\n人物笔记：'+wiki(people_dir/(person['name']+'.md'),person['name'])+'。\n',1)
    transcript=copy(root/'transcript.md',assets/'逐字稿.md')
    if (root/'captures/comparison.json').exists():copy(root/'captures/comparison.json',assets/'页面匹配.json')
    copy(root/'captures/cursor-status.json',assets/'指针检测状态.json')
    copy(root/'meeting-structure.json',assets/'会议结构.json')
    if (root/'captures/annotated/annotations.json').exists():
        copy(root/'captures/annotated/annotations.json',assets/'编号标注证据.json')
        copy(root/'annotation-plan.json',assets/'编号标注计划.json')
    groups=read_json(root/'sections/manifest.json')['presentations']
    rows=['# '+event['meeting_date']+' 组会材料','',
          wiki(report_path,'返回本次报告')+' · '+wiki(transcript,'逐字稿'),'',
          '| 汇报人 | 内容 | 录像代表页 | 文件 |','|---|---|---:|---|']
    for group in groups:
        links=[]
        if group.get('image_index'):
            gallery=assets/'图片'/(group['id']+'.md')
            pages=read_json(root/'sections'/(group['id']+'.json'))['pages']
            gallery_text=['# '+group['presenter']+'：'+group['title'],'',wiki(report_path,'图文讲稿'),'']
            for number,page in enumerate(pages,1):
                source=safe_path(root,page['export_image']);relative=copy(source,assets/'图'/source.name)
                gallery_text+=['## %02d · %s'%(number,page['page_id']),'','!'+wiki(relative),'']
            put(gallery,encode('\n'.join(gallery_text)))
            links.append('[逐页图片](%s)'%Path(os.path.relpath(vault/gallery,(vault/materials_path).parent)).as_posix())
        for kind in ('pdf','pptx'):
            if not group.get(kind):continue
            source=safe_path(root,group[kind]);relative=copy(source,assets/'汇报文件'/source.name)
            links.append('[%s](%s)'%(kind.upper(),Path(os.path.relpath(vault/relative,(vault/materials_path).parent)).as_posix()))
        rows.append('| %s | %s | %d | %s |'%(wiki(people_dir/(group['presenter']+'.md'),group['presenter']),
                     group['title'],group['page_count'],' · '.join(links)))
    for group in structure['presentations']:
        if group.get('reference_dir'):
            deck=read_json(root/group['reference_dir']/'deck.json')
            original=copy(Path(deck['source']),assets/'原件'/(group['presenter']+'-原始汇报'+Path(deck['source']).suffix))
            rows+=['','[%s原始汇报文件](%s)。'%(group['presenter'],
                  Path(os.path.relpath(vault/original,(vault/materials_path).parent)).as_posix())]
    rows+=['','主材料为逐页图片，报告直接嵌入原图或编号副本。PDF/PPTX为可选的截图阅读集。只有提供原件的汇报可核对原稿覆盖，其他代表页数不等于原稿总页数。',
           '','正文引用的关键截图与逐字稿已随本次报告收入库内；视频、全量采样和OCR档案保留在原工作流目录：',
           '', '`'+root.as_posix()+'`','',
           ('[页面匹配](页面匹配.json) · ' if (root/'captures/comparison.json').exists() else '')+'[指针检测状态](指针检测状态.json) · [会议结构](会议结构.json)',
           '', '鼠标坐标仍为未经完整标注验证的稀疏候选，不能据此认定点击或讲解意图。']
    if source_url:rows.insert(4,'[腾讯会议回放]('+source_url+')\n')
    if (root/'captures/annotated/annotations.json').exists():
        rows+=['','[编号标注证据](编号标注证据.json) · [编号标注计划](编号标注计划.json)。编号由脚本按已核对的定时指针候选生成，原图保留。']
    put(materials_path,encode('\n'.join(rows)))
    body+='\n\n## 材料与阅读说明\n\n'+wiki(materials_path,'分人图片与原件')+' · '+wiki(transcript,'标准化逐字稿')+'\n\n'
    body+='正文中的时间以录像起点计。关键截图已收入库内；完整原始处理材料的位置见材料页。翻页定位受视频采样间隔影响，极短页及动画中间态可能遗漏。原稿覆盖率只对实际提供并核对的原件成立，录像代表页数不构成未提供原件的汇报的完整性证明。\n\n'
    body+='重要数值和公式已回查页面，平台发言人标签及字稿没有逐句核听。仿真、受限实机展示、本人计划与现场建议分别记录。\n'
    header='---\n日期: '+event['meeting_date']+'\n更新: '+event['updated']+'\n分类: 知识\n标签: [组会, 组会报告]\n汇报人:\n'
    for person in event['presenters']:header+='  - "'+wiki(people_dir/(person['name']+'.md'))+'"\n'
    header+='---\n\n'
    destination=vault/report_path
    existing=destination.read_text('utf8') if destination.exists() else ''
    put(report_path,encode(block(existing,'report',body,initial=header)))
    put(assets/'人物汇报提要.json',encode(json.dumps(event,ensure_ascii=False,indent=2)))
    # Rebuild longitudinal summaries from dated evidence already in the vault.
    events={}
    for path in (vault/config['assets_dir']).glob('*/人物汇报提要.json'):
        previous=read_json(path)
        events[previous['meeting_id']]=previous
    events[event['meeting_id']]=event
    people={}
    for meeting in sorted(events.values(),key=lambda m:m['meeting_date'],reverse=True):
        for person in meeting['presenters']:people.setdefault(person['name'],[]).append((meeting,person))
    for name,history in people.items():
        directions={}
        for meeting,person in history:
            for topic in person['directions']:directions.setdefault(topic,meeting)
        latest,latest_person=history[0]
        latest_link=wiki(latest['report_path'],'来源报告',latest_person['report_heading'])
        content=['## 研究名片','']+card_lines(latest_person,latest['meeting_date'],latest_link)+[
            '', '## 研究主题','', '以下归纳截至'+latest['meeting_date']+'已整理的组会内容，覆盖%d次汇报；主题来自汇报证据，长期研究方向仍需结合后续记录更新。'%len(history),'']
        for topic,source in directions.items():content.append('- '+topic+'。依据：'+wiki(source['report_path'],source['meeting_date'])+'。')
        content+=['','## 历次汇报','']
        for meeting,person in history:
            link=wiki(meeting['report_path'],meeting['meeting_date']+'报告',person['report_heading'])
            content+=['### '+meeting['meeting_date']+' '+person['title'],'',
                      '类型：'+person['kind']+'。来源：'+link+'；录像 '+person['evidence_window']+'。','',
                      person['summary'],'','**本次进展：** '+person['progress'],'','**关键讨论：**','']
            content+=['- '+d for d in person['discussions']]
            content+=['','**当时待跟进：**','']+['- '+item for item in person['follow_up']]
            content+=['','后续是否完成，以更新的组会记录或本人确认补充；该次报告中的计划不自动转为已完成。','']
        note=people_dir/(name+'.md');path=vault/note
        existing=path.read_text('utf8') if path.exists() else ''
        initial='---\n分类: 人物\n标签: [人物, 组会]\n---\n\n# '+name+'\n\n'
        put(note,encode(block(existing,'profile','\n'.join(content),initial=initial)))
    person_index=people_dir/'人物.md'
    pcontent=['# 人物','', '按人查研究主题和历次汇报；每项进展与讨论链接回对应组会报告。',
              '研究方向根据已整理材料归纳，不根据提问、鼠标位置或单次标题推定个人长期方向。','',
              '| 人物 | 在研究什么 | 已整理汇报 | 最近一次 | 入口 |','|---|---|---:|---|---|']
    for name,history in people.items():
        pcontent.append('| %s | %s | %d | %s | %s |'%(wiki(people_dir/(name+'.md'),name),
                        card_fields(history[0][1])['research'].replace('|','／'),len(history),history[0][0]['meeting_date'],wiki(history[0][0]['report_path'],'最近报告')))
    pcontent+=['','返回 '+wiki(meeting_dir/'组会.md','组会')+'。']
    ppath=vault/person_index
    put(person_index,encode(block(ppath.read_text('utf8') if ppath.exists() else '', 'people-index','\n'.join(pcontent),initial='')))
    cards_path=assets/'人物名片.md'
    put(cards_path,encode(meeting_cards(event,lambda p:wiki(people_dir/(p['name']+'.md'),'人物档案')+' · '+
                                      wiki(report_path,'本次报告',p['report_heading']))))
    index=meeting_dir/'组会.md';existing=(vault/index).read_text('utf8') if (vault/index).exists() else '# 组会\n'
    index_lines=['## 组会报告','', '按会议阅读内容与现场问答，按'+wiki(person_index,'人物')+'查看研究主题和历次进展。','']
    for meeting in sorted(events.values(),key=lambda m:m['meeting_date'],reverse=True):
        event_cards=Path(config['assets_dir'])/meeting['meeting_id']/'人物名片.md'
        cards_link=' · '+wiki(event_cards,'人物名片') if meeting['meeting_id']==event['meeting_id'] or (vault/event_cards).exists() else ''
        index_lines.append('- '+wiki(meeting['report_path'],meeting['title'])+cards_link+'：'+
                           '、'.join(p['name'] for p in meeting['presenters'])+'分别汇报，导师共同事项单列。')
    if '<!-- meeting-workflow:meeting-index:start -->' not in existing:
        existing=existing.rstrip()+'\n\n'+block('','meeting-index','\n'.join(index_lines),initial='')
    else:existing=block(existing,'meeting-index','\n'.join(index_lines))
    put(index,encode(existing))
    # All links must resolve before publication; Markdown anchors are checked too.
    for path,data in files.items():
        if path.suffix!='.md':continue
        text=data.decode('utf8')
        for link in re.findall(r'\]\(([^)]+)\)',text):
            if '://' in link or link.startswith('#'):continue
            target=path.parent/unquote(link.strip('<>')).split('#',1)[0]
            if target.resolve() not in files and not target.is_file():
                raise ValueError('Broken Markdown target in '+str(path)+': '+link)
        for dest in re.findall(r'\[\[([^\]|]+)(?:\|[^\]]+)?\]\]',text):
            target,_,anchor=dest.partition('#')
            # The pre-existing index may use valid short vault links.
            if '/' not in target:continue
            relative=Path(target if Path(target).suffix else target+'.md')
            resolved=(vault/relative).resolve()
            if resolved not in files and not resolved.is_file():raise ValueError('Broken wiki target '+dest)
            if anchor:
                target_text=files.get(resolved) or resolved.read_bytes()
                headings=re.findall(r'^#+\s+(.+)$',target_text.decode('utf8'),re.M)
                if anchor not in headings:raise ValueError('Missing heading '+dest)
    return vault,files,event


def publish(root,vault,files,event,apply=False):
    receipt_path=root/'obsidian-export.json'
    registry_path=safe_path(vault,Path(event['assets_base'])/'同步索引.json')
    old=read_json(registry_path) if registry_path.exists() else (read_json(receipt_path) if receipt_path.exists() else {})
    prior=old.get('managed_hashes',{}) if old.get('vault')==str(vault) else {}
    changed=[]
    # Preflight the entire plan to avoid overwriting edited notes or attachments.
    for path,data in files.items():
        key=path.relative_to(vault).as_posix()
        if path.exists():
            current=path.read_bytes()
            if current==data:continue
            if key in prior and sha(current)!=prior[key]:
                if path.suffix!='.md' or '<!-- meeting-workflow:' not in current.decode('utf8'):
                    raise ValueError('Existing output changed; review before replacing: '+key)
                # Allow edits outside managed blocks, but protect edits inside them.
                expected=old.get('block_hashes',{}).get(key)
                if expected is None or block_hash(current.decode('utf8'))!=expected:
                    raise ValueError('Managed content edited; merge before replacing: '+key)
            elif key not in prior:
                if path.suffix!='.md':raise ValueError('Existing attachment differs: '+key)
                if '<!-- meeting-workflow:' in current.decode('utf8'):
                    raise ValueError('Existing managed note has no publication history: '+key)
        changed.append(path)
    result={'vault':str(vault),'meeting_id':event['meeting_id'],'report':event['report_path'],
            'people':[event['people_dir']+'/'+p['name']+'.md' for p in event['presenters']],
            'changed_files':len(changed),'planned_files':len(files),'applied':apply,
            'managed_hashes':{p.relative_to(vault).as_posix():sha(d) for p,d in files.items()},'block_hashes':{}}
    for path,data in files.items():
        if path.suffix=='.md':
            digest=block_hash(data.decode('utf8'))
            if digest:result['block_hashes'][path.relative_to(vault).as_posix()]=digest
    if apply:
        for path in changed:
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.meeting-tmp')
            temporary.write_bytes(files[path]);os.replace(str(temporary),str(path))
        registry={'vault':str(vault),'managed_hashes':dict(prior),'block_hashes':dict(old.get('block_hashes',{}))}
        registry['managed_hashes'].update(result['managed_hashes']);registry['block_hashes'].update(result['block_hashes'])
        registry_path.write_bytes(encode(json.dumps(registry,ensure_ascii=False,indent=2)))
        receipt_path.write_bytes(encode(json.dumps(result,ensure_ascii=False,indent=2)))
        state_path=root/'workflow.json';state=read_json(state_path)
        state['obsidian']={k:result[k] for k in ('vault','report','people')}
        state['obsidian']['export_receipt']=receipt_path.name
        state_path.write_bytes(encode(json.dumps(state,ensure_ascii=False,indent=2)))
    print(json.dumps({k:v for k,v in result.items() if k not in ('managed_hashes','block_hashes')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('root',type=Path)
    p.add_argument('--config',type=Path,help='Optional local JSON config; never commit real vault configuration')
    p.add_argument('--vault',type=Path);p.add_argument('--apply',action='store_true',help='Write reviewed output; default only validates the plan')
    a=p.parse_args();config=read_json(a.config) if a.config else {'meeting_dir':'知识/组会','people_dir':'知识/人物','assets_dir':'附件/组会'}
    if a.vault:config['vault']=str(a.vault.resolve())
    if not config.get('vault'):p.error('Select a vault using --vault or --config')
    root=a.root.resolve();vault,files,event=plan_export(root,config)
    publish(root,vault,files,event,a.apply)

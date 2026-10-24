// Image-based PowerPoint companion to the screenshot PDF; no reconstruction of
// native charts/formulas is claimed. Source timestamps are stored in notes.
import fs from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
const [meetingRoot,runtimeModules,runtimePython,skillDir,selectionPath]=process.argv.slice(2);
process.env.RUNTIME_NODE_MODULES=runtimeModules;
const {Presentation,PresentationFile}=await import(pathToFileURL(path.join(runtimeModules,'@oai/artifact-tool/dist/artifact_tool.mjs')).href);
const root=path.resolve(meetingRoot),captures=path.join(root,'captures');
const section=selectionPath?JSON.parse(await fs.readFile(selectionPath,'utf8')):null;
const selectionFile=await fs.stat(path.join(captures,'image-selection.json')).then(()=> 'image-selection.json',()=> 'pdf-selection.json');
const selected=section?section.pages.map(r=>r.page_id):JSON.parse(await fs.readFile(path.join(captures,selectionFile),'utf8')).included;
const reading=section?section.pages:JSON.parse(await fs.readFile(path.join(captures,'reading-order.json'),'utf8')).pages;
if(!section){const raw=JSON.parse(await fs.readFile(path.join(captures,'slides.json'),'utf8')).intervals;
  for(const id of selected)if(!reading.some(r=>r.page_id===id)){const rows=raw.filter(r=>r.page_id===id).sort((a,b)=>(b.end_s-b.start_s)-(a.end_s-a.start_s));
    if(!rows.length)throw new Error(`Unknown selected page ${id}`);reading.push(rows[0]);}}
const deck=Presentation.create({slideSize:{width:960,height:540}});
const staging=path.join(root,section?`.split-pptx/${section.id}`:'.capture-pptx');await fs.mkdir(staging,{recursive:true});
const previews=path.join(staging,'previews');await fs.mkdir(previews,{recursive:true});
for(const [i,id] of selected.entries()){
  const row=reading.find(r=>r.page_id===id),slide=deck.slides.add();
  const imagePath=row.export_image?path.join(root,row.export_image):path.join(captures,row.image);
  const bytes=await fs.readFile(imagePath);
  slide.images.add({blob:bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength),contentType:imagePath.toLowerCase().endsWith('.png')?'image/png':'image/jpeg',
    alt:id,fit:'contain',position:{left:0,top:0,width:960,height:540}});
  slide.speakerNotes.text=`${section?`${section.presenter}：${section.title}\n`:''}录像截图 ${id}\n本汇报首次展示 ${row.first_shown_in_section_s??row.first_shown_s??row.start_s} 秒\n展示区间 ${JSON.stringify(row.display_intervals??[])}\n原件页码 ${row.reference?.page??'未匹配'}\n来源: ${row.image}\n截图页面，不是原始可编辑对象。`;
  const png=await deck.export({slide,format:'png',scale:1});
  await fs.writeFile(path.join(previews,`page-${String(i+1).padStart(3,'0')}.png`),new Uint8Array(await png.arrayBuffer()));
  if((i+1)%12===0)console.log(`rendered ${i+1}/${selected.length}`);
}
const candidate=path.join(staging,'candidate.pptx');
await (await PresentationFile.exportPptx(deck)).save(candidate);
const {finalizePresentation}=await import(pathToFileURL(path.join(skillDir,'container_tools/artifact_tool_utils.mjs')).href);
const finalDirectory=path.join(root,section?'output/分人汇报':'output');await fs.mkdir(finalDirectory,{recursive:true});
const stem=section?section.file_stem:'录像截图版';
let finalPath=path.join(finalDirectory,`${stem}.pptx`);
for(let revision=2;await fs.stat(finalPath).then(()=>true,()=>false);revision++){
  finalPath=path.join(finalDirectory,`${stem}-${revision}.pptx`);
}
const result=await finalizePresentation({workspaceDir:root,candidatePath:candidate,finalPath,
  pythonExecutable:runtimePython,
  integrityValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skillDir,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','9144000,5143500','--expected-slide-count',String(selected.length)],
  explicitTotalSlideCount:selected.length,requiredNativeTableOwnerSlides:[],verifyArtifactToolImport:true,
  receiptPath:path.join(staging,'validation.json')});
console.log(JSON.stringify(result));
if(section){section.pptx=path.relative(root,finalPath).split(path.sep).join('/');
  await fs.writeFile(selectionPath,JSON.stringify(section,null,2));}

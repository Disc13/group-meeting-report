// Read-only PPTX rendering. The source is never modified.
import fs from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const [source, output, runtimePackages] = process.argv.slice(2);
if (!source || !output || !runtimePackages) throw new Error('source.pptx output-dir runtime-node-modules');
const {FileBlob, PresentationFile} = await import(pathToFileURL(`${runtimePackages}/@oai/artifact-tool/dist/artifact_tool.mjs`).href);
await fs.mkdir(output,{recursive:true});
const deck = await PresentationFile.importPptx(await FileBlob.load(source));
for (const [i,slide] of deck.slides.items.entries()) {
  const png=await deck.export({slide,format:'png',scale:1.5});
  await fs.writeFile(`${output}/page-${String(i+1).padStart(3,'0')}.png`,new Uint8Array(await png.arrayBuffer()));
  console.log(`rendered ${i+1}/${deck.slides.items.length}`);
}

"""Import native PPTX text/notes or PDF text/pages, preserving source order."""
import argparse,hashlib,json,posixpath,zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

A='http://schemas.openxmlformats.org/drawingml/2006/main'
R='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
P='http://schemas.openxmlformats.org/presentationml/2006/main'


def text(xml):
    return '\n'.join(e.text for e in ET.fromstring(xml).iter('{%s}t'%A) if e.text)


def run(source,out):
    out.mkdir(parents=True,exist_ok=True)
    pages=[]
    if source.suffix.lower()=='.pptx':
        with zipfile.ZipFile(source) as z:
            rels=ET.fromstring(z.read('ppt/_rels/presentation.xml.rels'))
            targets={r.attrib['Id']:posixpath.normpath('ppt/'+r.attrib['Target']) for r in rels}
            presentation=ET.fromstring(z.read('ppt/presentation.xml'))
            for number,item in enumerate(presentation.find('{%s}sldIdLst'%P),1):
                target=targets[item.attrib['{%s}id'%R]]
                content=text(z.read(target));notes=''
                slide_rels=posixpath.dirname(target)+'/_rels/'+posixpath.basename(target)+'.rels'
                if slide_rels in z.namelist():
                    for relation in ET.fromstring(z.read(slide_rels)):
                        if relation.attrib.get('Type','').endswith('/notesSlide'):
                            location=posixpath.normpath(posixpath.join(posixpath.dirname(target),relation.attrib['Target']))
                            notes=text(z.read(location))
                pages.append({'page':number,'text':content,'notes':notes,
                              'template_candidate':len(''.join(content.split()))<100})
    elif source.suffix.lower()=='.pdf':
        import fitz
        doc=fitz.open(source)
        for index,page in enumerate(doc):
            content=page.get_text()
            page.get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(str(out/('page-%03d.png'%(index+1))))
            pages.append({'page':index+1,'text':content,'notes':'',
                          'template_candidate':len(''.join(content.split()))<100})
    else:raise ValueError('Use PPTX or PDF')
    result={'source':str(source.resolve()),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
            'pages':pages,'note':'template_candidate is a conservative text-length heuristic, not semantic classification'}
    (out/'deck.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    (out/'deck-text.md').write_text('\n\n'.join('## Page %d\n\n%s\n\nNotes:\n%s'%(p['page'],p['text'],p['notes']) for p in pages),encoding='utf8')
    print(len(pages),'native pages imported')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();run(a.source,a.out)

"""Render an authorized local deck; no document or runtime is uploaded."""
import argparse,shutil,subprocess,sys,tempfile
from pathlib import Path


def rasterize(pdf,out):
    import fitz
    out.mkdir(parents=True,exist_ok=True)
    with fitz.open(str(pdf)) as doc:
        for i,page in enumerate(doc):page.get_pixmap(matrix=fitz.Matrix(1.5,1.5)).save(str(out/('page-%03d.png'%(i+1))))


def render(source,out,node='node',node_modules=None,soffice=None):
    source=source.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=True)
    if source.suffix.lower()=='.pdf':return rasterize(source,out)
    if source.suffix.lower()!='.pptx':raise ValueError('Use PPTX or PDF')
    if node_modules:
        subprocess.run([str(node),str(Path(__file__).with_name('render_reference.mjs')),str(source),str(out),str(node_modules)],check=True)
        return
    executable=soffice or shutil.which('soffice')
    if not executable:raise RuntimeError('Install LibreOffice and add soffice to PATH, or configure node_modules for an installed artifact runtime')
    with tempfile.TemporaryDirectory(prefix='.render-',dir=str(out)) as temporary:
        work=Path(temporary)
        subprocess.run([str(executable),'-env:UserInstallation='+(work/'profile').as_uri(),'--headless','--convert-to','pdf',
                        '--outdir',str(work),str(source)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        pdf=work/(source.stem+'.pdf')
        if not pdf.is_file():raise RuntimeError('LibreOffice did not produce a PDF')
        rasterize(pdf,out)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--node',default='node');p.add_argument('--node-modules',type=Path);p.add_argument('--soffice')
    a=p.parse_args();render(a.source,a.out,a.node,a.node_modules,a.soffice)

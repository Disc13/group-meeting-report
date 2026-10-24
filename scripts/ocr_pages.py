"""Fixed OCR: Windows.Media.Ocr on Windows, Tesseract elsewhere."""
import argparse,hashlib,json,os,shutil,subprocess
from pathlib import Path


def run(images,out):
    out.mkdir(parents=True,exist_ok=True)
    if os.name=='nt':
        subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(Path(__file__).with_name('ocr_pages.ps1')),
                        '-ImageDirectory',str(images.resolve()),'-OutputDirectory',str(out.resolve())],check=True)
        return
    executable=shutil.which('tesseract')
    if not executable:raise RuntimeError('Install Tesseract with chi_sim and eng language data')
    records=[]
    for image in sorted(images.iterdir()):
        if image.suffix.lower() not in ('.png','.jpg','.jpeg'):continue
        target=out/(image.stem+'.json');signature=hashlib.sha256(image.read_bytes()).hexdigest()
        old=json.loads(target.read_text('utf-8-sig')) if target.exists() else {}
        if old.get('image_sha256')==signature:record=old
        else:
            result=subprocess.run([executable,str(image),'stdout','-l','chi_sim+eng'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
            record={'page_id':image.stem,'source':str(image.resolve()),'engine':'Tesseract chi_sim+eng',
                    'image_sha256':signature,'text':result.stdout.decode('utf8')}
            target.write_bytes((json.dumps(record,ensure_ascii=False,indent=2)+'\n').encode('utf8'))
        records.append(record)
    (out/'all.json').write_bytes((json.dumps(records,ensure_ascii=False,indent=2)+'\n').encode('utf8'))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('images',type=Path);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();run(a.images,a.out)

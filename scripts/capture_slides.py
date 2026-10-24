"""Video -> stable page screenshots, dynamic intervals, and transcript alignment.

Acquisition/cropping/deduplication uses ffmpeg/OpenCV only. No LLM calls.
The screenshots are evidence pages, not recovered editable PowerPoint objects.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np


def read_image(path):
    return cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)


def write_image(path, image):
    ok, data = cv2.imencode(path.suffix, image)
    if not ok:
        raise ValueError('Cannot encode '+str(path))
    data.tofile(str(path))


def save_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


def auto_crop(image):
    """Largest bright document rectangle, falling back to nonblack screen area."""
    h,w=image.shape[:2]
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    bright=(gray>195).astype(np.uint8)*255
    bright=cv2.morphologyEx(bright,cv2.MORPH_CLOSE,np.ones((11,11),np.uint8))
    contours,_=cv2.findContours(bright,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    candidates=[]
    for contour in contours:
        x,y,cw,ch=cv2.boundingRect(contour)
        area=cw*ch/(w*h)
        if .20<area<=1 and 1.68<cw/ch<1.88:
            candidates.append((area,x,y,cw,ch))
    if candidates:
        _,x,y,cw,ch=max(candidates)
        return image[y:y+ch,x:x+cw], [x,y,cw,ch], 'bright_rectangle'
    rows=np.mean(gray>25,axis=1);cols=np.mean(gray>25,axis=0)
    ys=np.flatnonzero(rows>.12);xs=np.flatnonzero(cols>.12)
    if len(ys) and len(xs):
        x,y=int(xs[0]),int(ys[0]);cw,ch=int(xs[-1]-x+1),int(ys[-1]-y+1)
        if 1.6<cw/ch<1.95:
            return image[y:y+ch,x:x+cw], [x,y,cw,ch], 'nonblack_bounds'
    return image,[0,0,w,h],'full_frame_fallback'


def descriptor(image):
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    gray=cv2.resize(gray,(320,180))
    return cv2.GaussianBlur(gray,(3,3),0)


def difference(a,b):
    return float(np.mean(cv2.absdiff(a,b)>25))


def extract_frames(video,out,ffmpeg,step):
    directory=out/'samples';directory.mkdir(parents=True,exist_ok=True)
    info={'video':str(video.resolve()),'size':video.stat().st_size,
          'mtime_ns':video.stat().st_mtime_ns,'step_s':step,'width':1280}
    cache=directory/'cache.json'
    if cache.exists() and json.loads(cache.read_text('utf8'))==info:
        return sorted(directory.glob('*.jpg'))
    if list(directory.glob('*.jpg')):
        raise ValueError('Sample cache belongs to another input/config; choose another output directory')
    command=[str(ffmpeg),'-hide_banner','-nostdin','-i',str(video),'-an',
             '-vf','fps=1/%s,scale=1280:-2'%step,'-q:v','2',str(directory/'%06d.jpg')]
    with (out/'sampling.log').open('w',encoding='utf8') as log:
        subprocess.run(command,stdout=log,stderr=log,check=True)
    save_json(cache,info)
    return sorted(directory.glob('*.jpg'))


def match_references(pages,reference_dir):
    orb=cv2.ORB_create(nfeatures=2000)
    matcher=cv2.BFMatcher(cv2.NORM_HAMMING)
    references=[]
    for path in sorted(reference_dir.glob('page-*.png')):
        im=read_image(path);gray=cv2.cvtColor(cv2.resize(im,(960,540)),cv2.COLOR_BGR2GRAY)
        points,features=orb.detectAndCompute(gray,None)
        references.append((path,points,features))
    results={}
    for page_id,path in pages.items():
        im=read_image(path);gray=cv2.cvtColor(cv2.resize(im,(960,540)),cv2.COLOR_BGR2GRAY)
        kp,features=orb.detectAndCompute(gray,None)
        scores=[]
        if features is not None:
            for reference,rkp,rfeatures in references:
                if rfeatures is None: continue
                good=[pair[0] for pair in matcher.knnMatch(features,rfeatures,k=2)
                      if len(pair)==2 and pair[0].distance<.72*pair[1].distance]
                inliers=0
                if len(good)>=8:
                    src=np.float32([kp[m.queryIdx].pt for m in good])
                    dst=np.float32([rkp[m.trainIdx].pt for m in good])
                    _,mask=cv2.findHomography(src,dst,cv2.RANSAC,4)
                    inliers=int(mask.sum()) if mask is not None else 0
                scores.append((inliers,int(reference.stem.split('-')[-1])))
        scores.sort(reverse=True)
        best=scores[0] if scores else (0,None);second=scores[1][0] if len(scores)>1 else 0
        # Shared university logos/templates produce many weak false matches.
        accepted=best[0]>=100 and best[0]>=1.6*max(1,second)
        results[page_id]={'page':best[1] if accepted else None,'status':'matched' if accepted else 'ambiguous',
                          'inliers':best[0],'runner_up_inliers':second,'method':'ORB + ratio test + RANSAC'}
    return results


def build(args):
    args.out.mkdir(parents=True,exist_ok=True)
    frames=extract_frames(args.video,args.out,args.ffmpeg,args.step)
    if not frames: raise ValueError('No frames extracted')
    # Group consecutive stable frames; short runs remain explicitly unverified.
    groups=[];current=None
    for index,path in enumerate(frames):
        image=read_image(path)
        if args.crop:
            x,y,w,h=args.crop;image=image[y:y+h,x:x+w]
        cropped,bounds,crop_method=auto_crop(image)
        desc=descriptor(cropped)
        if current is None or difference(desc,current['anchor'])>args.change:
            if current: groups.append(current)
            current={'first':index,'last':index,'anchor':desc,'image':cropped,
                     'bounds':bounds,'crop_method':crop_method,'sharpness':float(cv2.Laplacian(desc,cv2.CV_64F).var())}
        else:
            current['last']=index
            sharpness=float(cv2.Laplacian(desc,cv2.CV_64F).var())
            if sharpness>current['sharpness']:
                current.update(image=cropped,bounds=bounds,crop_method=crop_method,sharpness=sharpness)
    if current: groups.append(current)
    pages_dir=args.out/'pages';pages_dir.mkdir(exist_ok=True)
    unique=[];intervals=[];dynamic=[]
    for group in groups:
        start,end=group['first']*args.step,(group['last']+1)*args.step
        stable=group['last']>group['first']
        if not stable:
            if dynamic and abs(dynamic[-1]['end_s']-start)<.01:
                dynamic[-1]['end_s']=end
            else: dynamic.append({'start_s':start,'end_s':end,'status':'moving_or_short_page'})
            # Keep a representative for every short run too; no silent page loss.
        match=None
        for previous in unique:
            if difference(group['anchor'],previous['desc'])<args.dedup:
                match=previous;break
        if match is None:
            page_id='capture-%03d'%(len(unique)+1)
            path=pages_dir/(page_id+'.jpg');write_image(path,group['image'])
            match={'id':page_id,'desc':group['anchor'],'path':path}
            unique.append(match)
        intervals.append({'id':'interval-%04d'%(len(intervals)+1),'page_id':match['id'],
                          'start_s':start,'end_s':end,'stable':stable,
                          'image':str(match['path'].relative_to(args.out)).replace('\\','/'),
                          'crop':group['bounds'],'crop_method':group['crop_method']})
    previous_result=args.out/'slides.json'
    if args.reference and args.reuse_matches and previous_result.exists():
        references=json.loads(previous_result.read_text('utf8'))['reference_matches']
        for match in references.values():
            if match['inliers']<100 or match['inliers']<1.6*max(1,match['runner_up_inliers']):
                match.update(page=None,status='ambiguous')
    else:
        references=match_references({u['id']:u['path'] for u in unique},args.reference) if args.reference else {}
    # Generic covers/chapter/thanks pages need nearby content-page support.
    first_seen={}
    for interval in intervals:
        first_seen.setdefault(interval['page_id'],interval['start_s'])
    reference_metadata=args.reference/'deck.json' if args.reference else None
    generic=set()
    if reference_metadata and reference_metadata.exists():
        generic={page['page'] for page in json.loads(reference_metadata.read_text('utf8'))['pages']
                 if page.get('template_candidate')}
    anchors=[first_seen[page_id] for page_id,match in references.items()
             if match['page'] and match['page'] not in generic]
    for page_id,match in references.items():
        if match['page'] in generic and not any(abs(first_seen[page_id]-t)<=180 for t in anchors):
            match.update(page=None,status='template_without_content_context')
    for interval in intervals:
        interval['reference']=references.get(interval['page_id'])
    result={'source_video':str(args.video.resolve()),'sample_step_s':args.step,
            'timing_uncertainty_s':args.step,'unique_pages':len(unique),'intervals':intervals,
            'dynamic_intervals':dynamic,'reference_matches':references,
            'note':'Page captures include short/moving views for review; they are not original editable slides. Frame sampling may miss pages shown less than the sampling interval.'}
    save_json(args.out/'slides.json',result)
    transcript=[json.loads(line) for line in args.transcript.read_text('utf8').splitlines()]
    aligned=[]
    for row in transcript:
        candidates=[s for s in intervals if s['start_s']<row['end_s'] and s['end_s']>row['start_s']]
        aligned.append(dict(row,pages=[{'page_id':s['page_id'],'image':s['image'],'reference':s['reference']} for s in candidates]))
    with (args.out/'aligned.jsonl').open('w',encoding='utf8') as output:
        for row in aligned: output.write(json.dumps(row,ensure_ascii=False)+'\n')
    packet=['# 视频页面与字稿','', '页面编号为录像截图编号；原 PPT 页码仅在自动匹配通过时填写。','']
    for interval in intervals:
        rows=[r for r in transcript if r['start_s']<interval['end_s'] and r['end_s']>interval['start_s']]
        ref=interval.get('reference') or {}
        packet.extend(['## %s · %.1f–%.1f 秒 · 原页 %s'%(interval['page_id'],interval['start_s'],interval['end_s'],ref.get('page') or '未匹配'),
                       '', '![页面](%s)'%interval['image'],''])
        packet.extend('[%.3f] %s：%s'%(r['start_s'],r['speaker'],r['text']) for r in rows)
        packet.append('')
    (args.out/'material-packet.md').write_text('\n'.join(packet),encoding='utf8')
    print(json.dumps({'frames':len(frames),'unique_pages':len(unique),'intervals':len(intervals),
                      'matched_unique_pages':sum(v['status']=='matched' for v in references.values())},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video',type=Path);p.add_argument('--out',type=Path,required=True)
    p.add_argument('--transcript',type=Path,required=True);p.add_argument('--reference',type=Path)
    p.add_argument('--ffmpeg',type=Path,default=shutil.which('ffmpeg') or 'ffmpeg')
    p.add_argument('--step',type=float,default=2);p.add_argument('--crop',type=int,nargs=4)
    p.add_argument('--change',type=float,default=.018);p.add_argument('--dedup',type=float,default=.006)
    p.add_argument('--reuse-matches',action='store_true',help='Reuse matches only for unchanged source and reference')
    a=p.parse_args()
    if a.step<=0:p.error('--step must be positive')
    build(a)

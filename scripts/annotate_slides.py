"""Review timed cursor evidence, then stamp numbered callouts on slide copies.

Coordinates come from recorded CV candidates. ORB/RANSAC registers the actual
sample crop to the chosen slide image; unsupported matches fail explicitly.
"""
import argparse,hashlib,json,os
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageDraw,ImageFont
from capture_slides import read_image


def load(path):return json.loads(path.read_text('utf-8-sig'))


def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes((json.dumps(obj,ensure_ascii=False,indent=2)+'\n').encode('utf8'))


def seconds(value):
    parts=str(value).split(':')
    return sum(float(p)*60**i for i,p in enumerate(reversed(parts)))


def records(root):
    return [json.loads(line) for line in (root/'cursor-candidates.jsonl').read_text('utf8').splitlines()]


def marker_font(size):
    candidates=[os.environ.get('MEETING_MARKER_FONT'),'DejaVuSans-Bold.ttf','Arial.ttf',
                str(Path(os.environ.get('WINDIR',''))/'Fonts/segoeuib.ttf')]
    for candidate in candidates:
        if not candidate:continue
        try:return ImageFont.truetype(candidate,size)
        except OSError:pass
    return ImageFont.load_default()


def query(root,time,window=4):
    time=seconds(time);metadata=load(root/'slides.json')
    rows=[r for r in records(root) if 'time_s' in r and abs(r['time_s']-time)<=window]
    rows.sort(key=lambda r:(abs(r['time_s']-time),r['time_s']))
    transcript=root.parent/'transcript.jsonl'
    utterances=[json.loads(s) for s in transcript.read_text('utf8').splitlines()] if transcript.exists() else []
    return {'requested_s':time,'window_s':window,'sample_step_s':metadata['sample_step_s'],
            'speech':[u for u in utterances if u['start_s']<=time+window and u['end_s']>=time-window],
            'samples':[dict(r,sample_file='samples/%06d.jpg'%(round(r['time_s']/metadata['sample_step_s'])+1)) for r in rows]}


def register(source,target,point):
    orb=cv2.ORB_create(nfeatures=2500)
    k1,d1=orb.detectAndCompute(cv2.cvtColor(source,cv2.COLOR_BGR2GRAY),None)
    k2,d2=orb.detectAndCompute(cv2.cvtColor(target,cv2.COLOR_BGR2GRAY),None)
    if d1 is None or d2 is None:raise ValueError('Not enough texture for registration')
    matches=cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(d1,d2,k=2)
    good=[a for pair in matches if len(pair)==2 for a,b in [pair] if a.distance<.72*b.distance]
    if len(good)<60:raise ValueError('Insufficient registration correspondences')
    a=np.float32([k1[m.queryIdx].pt for m in good]).reshape(-1,1,2)
    b=np.float32([k2[m.trainIdx].pt for m in good]).reshape(-1,1,2)
    h,mask=cv2.findHomography(a,b,cv2.RANSAC,3.0)
    if h is None or int(mask.sum())<60:raise ValueError('Registration not geometrically supported')
    projected=cv2.perspectiveTransform(a,h)
    errors=np.linalg.norm(projected-b,axis=2).ravel()[mask.ravel().astype(bool)]
    if float(np.median(errors))>2:raise ValueError('Registration residual too large')
    x,y=cv2.perspectiveTransform(np.float32([[point]]),h).reshape(2)
    if not (0<=x<target.shape[1] and 0<=y<target.shape[0]):raise ValueError('Cursor outside target slide')
    return (float(x),float(y)),{'homography':h.tolist(),'inliers':int(mask.sum()),'median_error_px':float(np.median(errors))}


def eligible(target_id,row,reading):
    if target_id==row.get('page_id'):return True
    target=next((r for r in reading if r['page_id']==target_id),{})
    return row.get('interval_id') in target.get('occurrence_ids',[])


def render(root,plan,out):
    data=load(root/'slides.json');reading=load(root/'reading-order.json')['pages']
    intervals={r['id']:r for r in data['intervals']};candidates=records(root)
    pages={r['page_id']:r for r in data['intervals']}
    pages.update({r['page_id']:r for r in reading})
    out.mkdir(parents=True,exist_ok=True);result={'pages':[]}
    utterances={u['id']:u for u in [json.loads(s) for s in (root.parent/'transcript.jsonl').read_text('utf8').splitlines()]}
    for page in plan['pages']:
        target_id=page['page_id'];target=pages[target_id]
        image=read_image(root/target['image']);paint=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB))
        draw=ImageDraw.Draw(paint);radius=max(14,round(paint.width*.013))
        font=marker_font(round(radius*1.4))
        annotations=[];numbers=set()
        for point in page['points']:
            if point.get('reviewed') is not True:raise ValueError('Review candidate frame before annotating')
            number=point['number']
            if type(number) is not int or not 1<=number<=99:raise ValueError('Callout number must be an integer from 1 to 99')
            if number in numbers:raise ValueError('Duplicate callout number')
            numbers.add(number);time=seconds(point['time_s'])
            row=next((r for r in candidates if r.get('time_s')==time),None)
            if not row or row['status']!='candidate' or row.get('x') is None:
                raise ValueError('No unique CV cursor candidate at %.3f'%time)
            if not eligible(target_id,row,reading):raise ValueError('Cursor belongs to a different slide')
            utterance=utterances.get(point.get('utterance_id'))
            if not utterance or not utterance['start_s']-data['sample_step_s']<=time<=utterance['end_s']+data['sample_step_s']:
                raise ValueError('Callout time does not match the cited utterance')
            interval=intervals[row['interval_id']];x0,y0,cw,ch=interval['crop']
            sample=root/'samples'/('%06d.jpg'%(round(time/data['sample_step_s'])+1));frame=read_image(sample)
            raw=(row['x'],row['y'])
            if not(x0<=raw[0]<x0+cw and y0<=raw[1]<y0+ch):raise ValueError('Candidate outside source slide crop')
            xy,registration=register(frame[y0:y0+ch,x0:x0+cw],image,(raw[0]-x0,raw[1]-y0))
            offset=point.get('label_offset',[radius*2,-radius*2]);cx=round(xy[0]+offset[0]);cy=round(xy[1]+offset[1])
            cx=max(radius+3,min(paint.width-radius-3,cx));cy=max(radius+3,min(paint.height-radius-3,cy))
            draw.line([tuple(map(round,xy)),(cx,cy)],fill='white',width=5)
            draw.line([tuple(map(round,xy)),(cx,cy)],fill='#d83930',width=2)
            draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius),fill='#d83930',outline='white',width=2)
            label=str(number);box=draw.textbbox((0,0),label,font=font) if hasattr(draw,'textbbox') else (0,0,*draw.textsize(label,font=font))
            draw.text((cx-(box[2]-box[0])/2,cy-(box[3]-box[1])/2-box[1]),label,font=font,fill='white')
            annotations.append(dict(point,time_s=time,raw_xy=list(raw),slide_xy=list(xy),registration=registration,
                                    utterance=utterance,
                                    candidate_meaning=row['meaning'],sample=str(sample.relative_to(root)),
                                    sample_sha256=hashlib.sha256(sample.read_bytes()).hexdigest()))
        destination=out/(target_id+'-numbered.png');paint.save(destination)
        result['pages'].append({'page_id':target_id,'image':str(destination.relative_to(root)),
                                'source_image':target['image'],'source_sha256':hashlib.sha256((root/target['image']).read_bytes()).hexdigest(),
                                'annotations':annotations})
    save(out/'annotations.json',result)
    print(json.dumps({'annotated_pages':len(result['pages']),'index':str(out/'annotations.json')},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    q=sub.add_parser('query');q.add_argument('captures',type=Path);q.add_argument('time');q.add_argument('--window',type=float,default=4)
    r=sub.add_parser('render');r.add_argument('captures',type=Path);r.add_argument('plan',type=Path);r.add_argument('--out',type=Path)
    a=p.parse_args();root=a.captures.resolve()
    if a.action=='query':print(json.dumps(query(root,a.time,a.window),ensure_ascii=False,indent=2))
    else:render(root,load(a.plan),a.out.resolve() if a.out else root/'annotated')

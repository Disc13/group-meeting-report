"""Conservative cursor candidates from temporal background subtraction.

Uses video sample pixels, not an LLM. Stationary/invisible pointers may be
unavailable; every output remains a candidate until calibrated on labels.
"""
import argparse
import json
from pathlib import Path
import cv2
import numpy as np
from capture_slides import read_image, save_json


def run(root):
    metadata=json.loads((root/'slides.json').read_text('utf8'))
    step=metadata['sample_step_s'];samples=root/'samples'
    output=[];review=[]
    for interval in metadata['intervals']:
        start=int(round(interval['start_s']/step));end=int(round(interval['end_s']/step))
        if end-start<3:
            output.append({'start_s':interval['start_s'],'end_s':interval['end_s'],
                           'status':'background_unavailable','x':None,'y':None})
            continue
        # Same screen coordinates throughout the interval, before page crop.
        indices=sorted(set(np.linspace(start,end-1,min(9,end-start)).astype(int)))
        frames=[cv2.resize(read_image(samples/('%06d.jpg'%(i+1))),(960,540)) for i in indices]
        background=np.median(np.stack(frames),axis=0).astype(np.uint8)
        for index in range(start,end):
            path=samples/('%06d.jpg'%(index+1))
            if not path.exists():continue
            frame=cv2.resize(read_image(path),(960,540))
            diff=np.max(cv2.absdiff(frame,background),axis=2)
            binary=(diff>55).astype(np.uint8)
            # Exclude clock/taskbar and account watermark bands.
            binary[:8]=0;binary[-36:]=0
            count,_,stats,centroids=cv2.connectedComponentsWithStats(binary,8)
            candidates=[]
            for component in range(1,count):
                x,y,w,h,area=stats[component]
                if 3<=w<=25 and 7<=h<=32 and .35<=w/h<=.9 and 12<=area<=350 and .12<=area/(w*h)<=.9:
                    candidates.append({'x':int(x),'y':int(y),'width':int(w),'height':int(h),'area':int(area)})
            if np.mean(binary)>.015:
                status='screen_moving';position=None
            elif len(candidates)==1:
                status='candidate';position=candidates[0]
            else:
                status='ambiguous' if candidates else 'not_found';position=None
            record={'time_s':index*step,'interval_id':interval['id'],'status':status,
                    'x':position['x']*1280/960 if position else None,
                    'y':position['y']*1280/960 if position else None,
                    'coordinate_space':'sample_frame','sample_width':1280,
                    'meaning':'candidate bounding-box origin, not verified cursor hotspot',
                    'candidates':candidates}
            if position:
                x0,y0,cw,ch=interval['crop'];px,py=record['x'],record['y']
                record['page_id']=interval['page_id']
                record['crop_u']=(px-x0)/cw if x0<=px<x0+cw and y0<=py<y0+ch else None
                record['crop_v']=(py-y0)/ch if record['crop_u'] is not None else None
            output.append(record)
            if status=='candidate' and len(review)<48:
                marked=frame.copy();p=position
                cv2.rectangle(marked,(p['x']-4,p['y']-4),(p['x']+p['width']+4,p['y']+p['height']+4),(0,0,255),2)
                review.append((index*step,marked))
    with (root/'cursor-candidates.jsonl').open('w',encoding='utf8') as f:
        for row in output:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    counts={status:sum(r['status']==status for r in output) for status in {r['status'] for r in output}}
    save_json(root/'cursor-status.json',{'method':'temporal median background + component geometry',
              'counts':counts,'status':'candidate_only_not_calibrated',
              'limitations':['2 second sampling does not recover continuous trajectories','stationary cursors can disappear into background',
                            'coordinates are candidate box origins, not click locations','animation and editor UI can create false positives']})
    if review:
        sheet=np.full((len(review)//4*155+155,4*240,3),235,np.uint8)
        for i,(time,image) in enumerate(review):
            x=i%4*240;y=i//4*155
            sheet[y:y+135,x:x+240]=cv2.resize(image,(240,135))
            cv2.putText(sheet,str(time),(x+4,y+150),cv2.FONT_HERSHEY_SIMPLEX,.4,(0,0,0),1)
        cv2.imencode('.jpg',sheet)[1].tofile(str(root/'cursor-review.jpg'))
    print(counts)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('captures',type=Path)
    run(parser.parse_args().captures)

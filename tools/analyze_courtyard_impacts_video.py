"""Extract timestamped impact-creation crops from a closed native route.

Contact sheets are visual evidence, not an automatic flicker/parity verdict.
"""
import argparse
import hashlib
import json
from pathlib import Path
import cv2
import numpy as np
from PIL import Image,ImageDraw


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe',type=Path)
    args=parser.parse_args();p=args.probe;motion=p/'motion'
    output=motion/'impact-creation-contact.json'
    if output.exists():raise ValueError('Require new output')
    native=json.loads((p/'probe.json').read_text())
    scenario=json.loads((p/'courtyard-decal-scenario.json').read_text())
    result=json.loads((motion/'result.json').read_text())
    if (native.get('timed_out') or native.get('exit_code_before_cleanup')!=0 or scenario.get('route_error') or
            len(scenario['events'])!=6 or result.get('timed_out') or result.get('exit_code')!=0):
        raise ValueError('Require normally completed route and recording')
    stamps=[json.loads(s) for s in (motion/'frames.jsonl').read_text().splitlines()]
    selected={min(stamps,key=lambda s:abs(s['seconds']-t))['index'] for t in np.arange(.25,6.01,.25)}
    if len(selected)!=24:raise ValueError('Insufficient temporal coverage')
    canvas=Image.new('RGB',(1020,760));draw=ImageDraw.Draw(canvas)
    cap=cv2.VideoCapture(str(motion/'game-window.mkv'));rows=[]
    try:
        for stamp in stamps:
            ok,frame=cap.read()
            if not ok or frame.shape!=(1119,1936,3):raise ValueError('Missing frame or changed window layout')
            if stamp['index'] not in selected:continue
            # Original 1280x720 region [550,180,170,170], displayed at 1.5x,
            # plus the recorded 8-pixel frame and 31-pixel title offset.
            rgb=cv2.cvtColor(frame[301:556,833:1088],cv2.COLOR_BGR2RGB)
            i=len(rows);x,y=i%6*170,i//6*190
            canvas.paste(Image.fromarray(rgb).resize((170,170)),(x,y+20))
            draw.text((x+3,y+3),'%.3fs'%stamp['seconds'])
            rows.append({'index':stamp['index'],'seconds':stamp['seconds'],
                         'crop_sha256':hashlib.sha256(rgb.tobytes()).hexdigest()})
    finally:cap.release()
    if len(rows)!=24:raise ValueError('Incomplete selected frames')
    canvas.save(motion/'impact-creation-contact.png')
    output.write_text(json.dumps({'scope':__doc__,'crop':[833,301,255,255],
        'video_sha256':hashlib.sha256((motion/'game-window.mkv').read_bytes()).hexdigest(),'frames':rows},indent=2)+'\n')
    print(output)


if __name__=='__main__':main()

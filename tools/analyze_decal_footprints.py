"""Verify saved depth masks and summarize absolute-pixel coverage changes."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from PIL import Image, ImageDraw, ImageFont


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--group-size',type=int,default=3)
    a=p.parse_args()
    raw=(a.source/'footprints.json').read_bytes(); report=json.loads(raw)
    if report.get('error') or not report.get('complete') or not report.get('restoration_exact'):
        raise ValueError('Require completed restored comparison')
    runs={r['mode']:r['draws'] for r in report['runs']}
    if list(runs)!=['original','precise','restored']: raise ValueError('Require three comparison modes')
    events=[d['event'] for d in runs['original']]
    if any([d['event'] for d in rows]!=events for rows in runs.values()): raise ValueError('Mismatched draws')
    if not 1<=a.group_size<=12 or len(events)%a.group_size: raise ValueError('Require complete groups of one to twelve draws')
    masks={}
    for mode,rows in runs.items():
        for row in rows:
            data=(a.source/row['file']).read_bytes()
            x,y,right,bottom=row['box']; width,height=right-x,bottom-y
            if len(data)!=width*height*8 or hashlib.sha256(data).hexdigest()!=row['sha256']:
                raise ValueError('Changed or short mask data')
            passed,rejected=set(),set()
            pixels=[]
            for i,color in enumerate(struct.iter_unpack('<4e',data)):
                point=(x+i%width,y+i//width)
                if color==(0,1,0,1): passed.add(point); pixels.append((47,200,106))
                elif color==(1,0,0,1): rejected.add(point); pixels.append((245,79,91))
                elif color==(0,0,0,0): pixels.append((25,31,42))
                else: raise ValueError('Unexpected overlay color')
            if len(passed)!=row['passing'] or len(rejected)!=row['rejected']: raise ValueError('Count mismatch')
            im=Image.new('RGB',(width,height));im.putdata(pixels)
            masks[mode,row['event']]={'pass':passed,'reject':rejected,'image':im,'bytes':data,'box':row['box']}
    result={'source':str(a.source.resolve()),'source_sha256':hashlib.sha256(raw).hexdigest(),'draws':[]}
    for event in events:
        before,after,restored=[masks[m,event] for m in runs]
        if before['box']!=restored['box'] or before['bytes']!=restored['bytes']: raise ValueError('Restoration mismatch')
        old=before['pass']|before['reject'];new=after['pass']|after['reject']
        result['draws'].append({'event':event,'original_pass':len(before['pass']),'original_reject':len(before['reject']),
            'precise_pass':len(after['pass']),'precise_reject':len(after['reject']),
            'rejected_to_pass':len(before['reject']&after['pass']),
            'pass_to_rejected':len(before['pass']&after['reject']),
            'coverage_added':len(new-old),'coverage_removed':len(old-new)})
    result['totals']={k:sum(d[k] for d in result['draws']) for k in result['draws'][0] if k!='event'}
    a.output.mkdir(parents=True,exist_ok=False)
    (a.output/'summary.json').write_text(json.dumps(result,indent=2)+'\n')
    font=ImageFont.truetype('C:/Windows/Fonts/segoeui.ttf',13)
    group_width=150*a.group_size; group_height=160; columns=4 if a.group_size<=3 else 1
    count=len(events)//a.group_size
    sheet=Image.new('RGB',(columns*group_width,70+((count+columns-1)//columns)*group_height),(15,20,28))
    draw=ImageDraw.Draw(sheet)
    draw.text((12,8),'Decal triangle footprints: original / corrected',font=font,fill='white')
    draw.text((12,30),'Green: passes. Red: rejected. Blank: no triangle coverage. Groups follow the requested event order.',font=font,fill=(190,200,215))
    for group in range(count):
        left=(group%columns)*group_width; top=70+(group//columns)*group_height
        draw.text((left+8,top),'Group %d'%(group+1),font=font,fill='white')
        for item in range(a.group_size):
            event=events[group*a.group_size+item]; x=left+item*150
            row=result['draws'][group*a.group_size+item]
            draw.text((x+8,top+20),'Event %d'%event,font=font,fill=(190,200,215))
            for n,mode in enumerate(('original','precise')):
                im=masks[mode,event]['image']; im=im.resize((im.width*2,im.height*2),Image.Resampling.NEAREST)
                im.thumbnail((68,100),Image.Resampling.NEAREST)
                sheet.paste(im,(x+6+n*72,top+42))
            draw.text((x+8,top+140),'Rejected: %d / %d'%(row['original_reject'],row['precise_reject']),font=font,fill=(190,200,215))
    sheet.save(a.output/'footprints.png')
    print(json.dumps(result['totals']))


if __name__=='__main__': main()

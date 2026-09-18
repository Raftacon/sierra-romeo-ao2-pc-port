"""Inspect guarded wall replay colors and the visible/occluded impact controls."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args(); p = args.directory
    source = p/'validation.json'; report = json.loads(source.read_text())
    if not report.get('complete') or not report.get('restoration_exact') or not report.get('original_replacement_exact'):
        raise ValueError('Require completed replay controls')
    output = p/'analysis.json'
    if output.exists(): raise ValueError('Require new analysis output')
    runs = {r['mode']:r for r in report['runs']}
    epochs = (9,10,15,20,25,30,35,40,45,50,55,59)
    canvas = Image.new('RGB',(1020,760)); draw = ImageDraw.Draw(canvas)
    for mode_index,mode in enumerate(('original','precise')):
        frames = {f['epoch']:f for f in runs[mode]['frames']}
        for i,epoch in enumerate(epochs):
            row = frames[epoch];raw=(p/row['file']).read_bytes()
            if hashlib.sha256(raw).hexdigest()!=row['sha256']:raise ValueError('Changed material crop')
            rgb=np.frombuffer(raw,dtype='<f2').reshape(170,170,4)[:,:,:3].astype(np.float32)
            if not np.isfinite(rgb).all():raise ValueError('Nonfinite color')
            rgb=np.maximum(rgb,0);display=np.clip((rgb/(1+rgb))**(1/2.2)*255,0,255).astype(np.uint8)
            x,y=i%6*170,(i//6*2+mode_index)*190
            canvas.paste(Image.fromarray(display),(x,y+20));draw.text((x+4,y+3),'%s epoch %d'%(mode,epoch))
    summary = {'validation_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'pixels':[]}
    for run in (runs['original'],runs['precise']):
        for pixel in run['pixels']:
            if pixel['event']==159821:
                hits=[h for h in pixel['history'] if h['event']==159821 and h['details']['shaderDiscarded']=='False']
            else:
                hits=[h for h in pixel['history'] if h['event'] in (438804,438825,438832,438842,438848,438858,438865,438875,438882,438889,438896)
                      and h['details']['shaderDiscarded']=='False']
            summary['pixels'].append({'mode':run['mode'],'pixel':pixel['pixel'],'event':pixel['event'],
                'hits':[{'event':h['event'],'passed':h['passed'],'stored_depth':h['details']['preMod']['depth'],
                         'fragment_depth':h['details']['shaderOut']['depth']} for h in hits]})
    visible=[p['hits'] for p in summary['pixels'] if p['event']==159821]
    if len(visible)!=2 or not visible[0] or not visible[1] or any(h['passed'] for h in visible[0]) or not all(h['passed'] for h in visible[1]):
        raise ValueError('Visible control did not change from rejected to passing')
    for pixel in summary['pixels']:
        if pixel['event']!=438896:continue
        if not any(h['event']==438804 and h['passed'] for h in pixel['hits']):raise ValueError('Missing debris control')
        if any(h['event']!=438804 and h['passed'] for h in pixel['hits']):raise ValueError('Impact incorrectly covers foreground debris')
    geometry={g['event']:g for g in runs['precise']['geometry']}
    summary['paired_wall_positions_exact']=geometry[157448]['positions_hex']==geometry[158223]['positions_hex']
    if not summary['paired_wall_positions_exact']:raise ValueError('Wall depth/color positions disagree')
    canvas.save(p/'material-contact.png');output.write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()

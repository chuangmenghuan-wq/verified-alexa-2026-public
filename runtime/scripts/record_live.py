"""Capture the actual owned browser renderer over real wall-clock time.
No DOM changes/actions. Audio narration is muxed separately and disclosed.
"""
import argparse,base64,json,time
from pathlib import Path
from verified_alexa.cdp import BrowserConnection
p=argparse.ArgumentParser();p.add_argument('--launch',required=True);p.add_argument('--output',required=True);p.add_argument('--seconds',type=float,default=104)
a=p.parse_args(); root=Path(a.output);root.mkdir(parents=True,exist_ok=True);launch=json.loads(Path(a.launch).read_text())
from urllib.request import Request,urlopen
frames=[]; states=[]; last_state=-1; start=time.monotonic(); print('RECORDING_STARTED',flush=True)
with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
    while time.monotonic()-start<a.seconds:
        at=time.monotonic()-start
        result=c.command('Page.captureScreenshot',{'format':'jpeg','quality':85,'fromSurface':True})
        name=f'frame-{len(frames):05d}.jpg';(root/name).write_bytes(base64.b64decode(result['data']))
        frames.append({'file':name,'at_seconds':at})
        if int(at)>last_state:
            last_state=int(at)
            try:
                states.append({'at_seconds':at,'ui':c.evaluate("({status:document.querySelector('#status')?.textContent,chat:document.querySelector('#chat')?.innerText,player:document.querySelector('#playerstate')?.textContent,progress:document.querySelector('#progressstatus')?.textContent,observed_at:document.querySelector('#observedtime')?.textContent,freshness:document.querySelector('#freshness')?.textContent,agreement:document.querySelector('#agreementterms')?.textContent,plan_status:document.querySelector('#planstatus')?.textContent,preview:document.querySelector('#previewterms')?.textContent,preview_hidden:document.querySelector('#speechpreview')?.hidden})")})
            except Exception as error:
                states.append({'at_seconds':at,'error':str(error)})
        if len(frames)==1:
            urlopen(Request(launch['ui_url']+'api/start-walkthrough',data=b'{}',headers={'Content-Type':'application/json'},method='POST')).read()
        time.sleep(max(0,.20-(time.monotonic()-start-at)))
duration=time.monotonic()-start
(root/'FRAME_TIMES.json').write_text(json.dumps({'scope':'actual renderer capture; no artificial screen states','duration_seconds':duration,'frames':frames},indent=2))
(root/'SCREEN_STATES.json').write_text(json.dumps(states,ensure_ascii=False,indent=2),encoding='utf-8')
lines=[]
for i,f in enumerate(frames):
    end=frames[i+1]['at_seconds'] if i+1<len(frames) else duration
    lines += ["file '"+f['file']+"'",'duration '+str(max(.01,end-f['at_seconds']))]
lines.append("file '"+frames[-1]['file']+"'")
(root/'frames.ffconcat').write_text('\n'.join(lines)+'\n')
print(json.dumps({'recorded_seconds':duration,'frames':len(frames)}),flush=True)

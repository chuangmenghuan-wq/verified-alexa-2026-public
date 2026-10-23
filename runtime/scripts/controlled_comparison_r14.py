"""Three seeded, same-world local command-only vs verified outcome comparison.

The baseline is an intentionally weak command-only straw baseline, not Alexa
measurement. Both predictions use the SAME actual action in the SAME world.
No third-party model/device, participants, or production claim is implied.
"""
import argparse
import base64
import hashlib
import json
import os
import random
import time
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen
from verified_alexa.simulator import run_owned
from verified_alexa.mcp_client import MCPClient
from verified_alexa.cdp import BrowserConnection
from verified_alexa.media_catalog import get_clip


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default=str(Path(__file__).resolve().parents[2]/'r14_comparison_evidence'))
    parser.add_argument('--seeds',nargs='+',type=int,default=[1701,4207,9913])
    parser.add_argument('--headed',action='store_true')
    args=parser.parse_args()
    if len(set(args.seeds))<3:raise ValueError('Use at least three distinct fixed seeds')
    output=Path(args.output).resolve();output.mkdir(parents=True,exist_ok=True)
    report={'schema':'controlled-local-outcome-comparison/v1','started_at':datetime.now(timezone.utc).isoformat(),
        'seeds':args.seeds,'baseline':'straw command-only: declares done when actual executor returned SUCCESS; not Alexa empirical performance',
        'treatment':'same actual command/world; actual fresh MCP native/compositor/source observer controls completion',
        'scope':'controlled local procedural clips, actual HTTP/Chrome/MCP; no human, native Alexa, production or field efficacy claim',
        'cases':[],'failures':[]}

    def save():
        rows=report['cases'];negatives=[row for row in rows if not row['ground_truth']['expected_done']]
        report['metrics']={'case_denominator':len(rows),'negative_case_denominator':len(negatives),
            'positive_case_denominator':len(rows)-len(negatives),
            'baseline_false_done':sum(row['baseline_false_done'] for row in rows),
            'verified_false_done':sum(row['verified_false_done'] for row in rows),
            'verified_true_done':sum(row['verified_done'] and row['ground_truth']['expected_done'] for row in rows),
            'execution_failures_retained':len(report['failures'])}
        (output/'CONTROLLED_COMPARISON.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')

    for seed in args.seeds:
        rng=random.Random(seed);root=output/('seed-'+str(seed)+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        runtime=run_owned(root,port=0,headless=not args.headed)
        try:
            launch=next(runtime);client=MCPClient(launch['mcp_url'],root/'RAW_COMPARISON_MCP.json')
            content=rng.choice(['clip-a','clip-b']);other='clip-b' if content=='clip-a' else 'clip-a'
            goal='Play clip '+content[-1].upper()
            def evaluate(expression,native=False):
                with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as b:
                    b.socket.settimeout(20)
                    return b.evaluate_native(expression) if native else b.evaluate(expression)
            def clear():
                evaluate("(()=>{document.querySelector('#comparison-overlay')?.remove();document.querySelector('#player').removeAttribute('style');return true;})()")
            def ground_truth(case,binding,expected):
                try:
                    with urlopen(binding['media_url'],timeout=3) as response:
                        raw=response.read();http={'status':response.status,'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}
                except Exception as error:http={'status':getattr(error,'code',None),'error':str(error)}
                snap=evaluate("""(()=>{const v=document.querySelector('#player'),o=document.querySelector('#comparison-overlay'),s=getComputedStyle(v);const r=v.getBoundingClientRect(),top=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {native_paused:v.paused,native_time:v.currentTime,native_src:v.currentSrc,native_error:v.error?.code||null,opacity:s.opacity,top_id:top?.id||null,overlay:o?{kind:o.tagName,paused:o.tagName==='VIDEO'?o.paused:null,time:o.tagName==='VIDEO'?o.currentTime:null,canvas_sha_input:o.tagName==='CANVAS'?o.toDataURL():null}:null};})()""",native=True)
                time.sleep(.25)
                later=evaluate("(()=>{const v=document.querySelector('#player'),o=document.querySelector('#comparison-overlay');return {native_paused:v.paused,native_time:v.currentTime,overlay_paused:o?.tagName==='VIDEO'?o.paused:null,overlay_time:o?.tagName==='VIDEO'?o.currentTime:null,canvas_sha_input:o?.tagName==='CANVAS'?o.toDataURL():null};})()",native=True)
                for item in (snap,later):
                    if item.get('canvas_sha_input'):
                        item['canvas_sha256']=hashlib.sha256(item.pop('canvas_sha_input').encode()).hexdigest()
                if snap.get('overlay',{}):
                    canvas=snap['overlay'].pop('canvas_sha_input',None)
                    if canvas:snap['overlay']['canvas_sha256']=hashlib.sha256(canvas.encode()).hexdigest()
                with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as b:
                    png=base64.b64decode(b.command('Page.captureScreenshot',{'format':'png','fromSurface':True,'captureBeyondViewport':True})['data'])
                path=root/(case+'.png');path.write_bytes(png)
                substantiated=(http.get('status')==503 if case=='route503' else snap['opacity']=='0' if case=='opacity'
                    else bool(snap['overlay'] and snap['overlay']['kind']=='VIDEO' and later['overlay_paused'] is False and later['overlay_time']>snap['overlay']['time']) if case=='overlay'
                    else bool(snap['overlay'] and snap['overlay']['kind']=='CANVAS' and snap['overlay'].get('canvas_sha256')==later.get('canvas_sha256')) if case=='freeze'
                    else http.get('sha256')==get_clip(content,root)['sha256'] and not snap['native_paused'] and later['native_time']>snap['native_time'])
                return {'expected_done':expected,'injection_substantiated':bool(substantiated),'http':http,'first':snap,'later':later,
                    'screenshot':str(path),'screenshot_sha256':hashlib.sha256(png).hexdigest(),
                    'authority':'controlled scenario label plus independent direct HTTP/native/visible-renderer measurements; not authored passed fields'}
            def row(case,result,expected,started,tool_calls):
                binding=result.get('binding') or result.get('evidence',{}).get('binding')
                if not binding:raise RuntimeError('Actual MCP result has no measured binding: '+json.dumps(result))
                truth=ground_truth(case,binding,expected)
                action=result.get('tool_claim',{})
                baseline=action.get('status')=='SUCCESS'
                verified=result.get('final_status') in ('VERIFIED_DONE','PLAYING_VERIFIED')
                record={'seed':seed,'case':case,'content_id':content,'mission_id':result.get('mission_id'),
                    'elapsed_seconds':time.monotonic()-started,'actions':tool_calls,'executor_action':action,
                    'baseline_done':baseline,'verified_done':verified,'baseline_false_done':bool(baseline and not expected),
                    'verified_false_done':bool(verified and not expected),'ground_truth':truth,'actual_mcp_result':result,
                    'raw_mcp_transcript':str(root/'RAW_COMPARISON_MCP.json')}
                report['cases'].append(record);save()
                print(json.dumps({'seed':seed,'case':case,'baseline_done':baseline,'verified_done':verified,
                    'ground_truth_done':expected,'injection_substantiated':truth['injection_substantiated']}),flush=True)
            clear();started=time.monotonic()
            normal=client.call('execute_verified_goal',{'goal':goal,'allow_recovery':False,'new_mission':True})
            row('normal',normal,True,started,['execute_verified_goal(allow_recovery=False)'])
            client.call('inject_playback_fault',{'mission_id':normal['mission_id']})
            started=time.monotonic();broken=client.call('execute_verified_goal',{'goal':goal,'allow_recovery':False,'new_mission':True})
            recheck=client.call('recheck_playback',{'mission_id':broken['mission_id']})
            broken['comparison_fresh_recheck']=recheck
            row('route503',broken,False,started,['inject_playback_fault','execute_verified_goal(allow_recovery=False)','recheck_playback'])
            started=time.monotonic();repaired=client.call('repair_playback',{'mission_id':broken['mission_id']})
            row('recovery',repaired,True,started,['repair_playback'])
            faults=['opacity','overlay','freeze'];rng.shuffle(faults)
            for case in faults:
                clear()
                if case=='opacity':evaluate("document.querySelector('#player').style.opacity='0'")
                else:
                    script="""(async()=>{const v=document.querySelector('#player'),p=v.parentElement;p.style.position='relative';const r=v.getBoundingClientRect(),pr=p.getBoundingClientRect(),o=document.createElement(TYPE);o.id='comparison-overlay';o.style.cssText=`position:absolute;left:${r.x-pr.x}px;top:${r.y-pr.y}px;width:${r.width}px;height:${r.height}px;z-index:20;pointer-events:none`;
if(TYPE==='canvas'){o.width=640;o.height=360;o.getContext('2d').drawImage(v,0,0,640,360);p.appendChild(o);}else{o.src=SOURCE;o.muted=true;o.loop=true;p.appendChild(o);await o.play();}return true;})()"""
                    evaluate(script.replace('TYPE',json.dumps('canvas' if case=='freeze' else 'video')).replace('SOURCE',json.dumps('/assets/'+other+'.webm')))
                started=time.monotonic();result=client.call('execute_verified_goal',{'goal':goal,'allow_recovery':False,'new_mission':True})
                fresh=client.call('recheck_playback',{'mission_id':result['mission_id']});result['comparison_fresh_recheck']=fresh
                row(case,result,False,started,['page-world presentation fault','execute_verified_goal(allow_recovery=False)','recheck_playback'])
            clear()
        except Exception as error:
            report['failures'].append({'seed':seed,'error':str(error),'state_root':str(root)});save()
            print(json.dumps({'seed':seed,'execution_error_retained':str(error)}),flush=True)
        finally:runtime.close()
    report['completed_at']=datetime.now(timezone.utc).isoformat();save()
    expected_denominator=len(args.seeds)*6
    if len(report['cases'])!=expected_denominator or report['failures'] or any(not row['ground_truth']['injection_substantiated'] for row in report['cases']):
        raise RuntimeError('Controlled comparison incomplete or unsubstantiated; all original failures retained')
    print(json.dumps({'final_metrics':report['metrics']}),flush=True)


if __name__=='__main__':main()

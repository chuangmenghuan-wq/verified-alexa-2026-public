"""Actual R14 UI/MCP walkthrough and renderer capture, never manufactured status.
The disclosed controlled setup establishes a bookmark via actual boundary proof.
Root may mux the resulting frames with separately disclosed synthetic narration.
"""
import argparse,json,subprocess,sys,time
from pathlib import Path
from verified_alexa.simulator import run_owned
from verified_alexa.cdp import BrowserConnection

p=argparse.ArgumentParser()
p.add_argument('--state-dir',required=True);p.add_argument('--output',required=True)
p.add_argument('--seconds',type=float,default=118);p.add_argument('--headless',action='store_true')
a=p.parse_args();root=Path(a.state_dir).resolve();frames=Path(a.output).resolve();root.mkdir(parents=True,exist_ok=True)
evidence={'scope':'automated actual owned UI + real MCP + actual synthetic Mandarin PCM ASR; controlled route interruptions; no human/microphone acceptance','actions':[],'checks':{}}
runtime=run_owned(root,port=0,headless=a.headless);recorder=None;started=None

def save():
    (root/'UI_WALKTHROUGH_EVIDENCE.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf8')

def action(c,name,expression):
    result=c.evaluate(expression)
    evidence['actions'].append({'name':name,'at_seconds':time.monotonic()-started if started else None,'result':result})
    save();return result

def state(c):
    return c.evaluate("({status:document.querySelector('#status').textContent,progress:document.querySelector('#progressstatus').textContent,plan:document.querySelector('#planstatus').textContent,paused:document.querySelector('#player').paused,time:document.querySelector('#player').currentTime,currentMission:localStorage.getItem('verifiedAlexaActualMission'),previewHidden:document.querySelector('#speechpreview').hidden})")

def wait_until(offset):
    while time.monotonic()-started<offset:time.sleep(.15)

def await_boundary(c,plan_id,timeout):
    end=time.monotonic()+timeout;last=None
    while time.monotonic()<end:
        last=c.evaluate("api('/api/plan/status',{plan_id:"+json.dumps(plan_id)+"})")
        if last.get('final_status')=='VERIFIED_DONE':return last
        if last.get('final_status') in ('SUPERSEDED','CANCELLED','NEEDS_AUTHORIZATION','MONITOR_EXPIRED'):return last
        time.sleep(.5)
    return last

def plan_state(c,plan_id):
    deadline=time.monotonic()+7;last={}
    while time.monotonic()<deadline:
        last=c.evaluate("api('/api/plan/status',{plan_id:"+json.dumps(plan_id)+"})")
        if last.get('final_status')!='MONITOR_BUSY':return last
        time.sleep(.17)
    return last

try:
    launch=next(runtime);evidence['launch']=launch
    with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
        c.socket.settimeout(55)
        for _ in range(60):
            if c.evaluate("typeof window.previewPlan==='function' && [...document.querySelector('#media').options].some(o=>o.value==='clip-long')"):break
            time.sleep(.15)
        # Controlled setup, not a fake bookmark: actual segment20 completion.
        action(c,'setup_preview',"window.previewPlan({content_id:'clip-long',start_seconds:18,end_seconds:20,auto_repairs:0})")
        seed=action(c,'setup_confirm','window.confirmVoice()')
        if not seed or not seed.get('plan_id'):raise RuntimeError('Setup plan was not confirmed')
        seed_done=await_boundary(c,seed['plan_id'],25)
        evidence['setup_boundary']=seed_done;save()
        if not seed_done or seed_done.get('final_status')!='VERIFIED_DONE':raise RuntimeError('Setup did not independently prove the bookmark boundary')
        capture=Path(__file__).with_name('record_live.py')
        recorder=subprocess.Popen([sys.executable,str(capture),'--launch',str(root/'launch.json'),'--output',str(frames),'--seconds',str(a.seconds)],stdout=(root/'RECORDER.log').open('w',encoding='utf8'),stderr=subprocess.STDOUT)
        while not (root/'START_WALKTHROUGH').exists():
            if recorder.poll() is not None:raise RuntimeError('Recorder exited before first captured frame')
            time.sleep(.1)
        started=time.monotonic()
        wait_until(2)
        preview=action(c,'actual_synthetic_agreement_preview',"document.querySelector('#planfixture').closest('details').open=true;document.getElementById('planfixture').onclick()")
        if not preview or preview.get('recognition',{}).get('parsed_request',{}).get('operation')!='plan':raise RuntimeError('Actual synthesized agreement recognition did not produce a plan preview')
        evidence['checks']['synthetic_plan_preview']=preview.get('confirmation_required') is True
        # Break the old, independently proven target before confirming the new
        # promise. Its real restart will accept the command while HTTP is503;
        # the plan then spends exactly one authorized automatic repair.
        wait_until(8)
        action(c,'first_controlled_interruption',"document.getElementById('drift').onclick()")
        wait_until(11)
        first=action(c,'confirm_agreement','window.confirmVoice()')
        if not first or not first.get('plan_id'):raise RuntimeError('Agreement confirmation failed')
        evidence['checks']['false_success_with_live_503']=bool(first.get('tool_claim',{}).get('status')=='SUCCESS' and
            first.get('first_independent_verification',{}).get('http_status')==503 and
            first.get('first_independent_verification',{}).get('passed') is False and first.get('final_status')!='VERIFIED_DONE')
        save()
        wait_until(19)
        first_recovery=plan_state(c,first['plan_id'])
        recovery_deadline=time.monotonic()+8
        while first_recovery.get('final_status')!='PLAYING_VERIFIED' and time.monotonic()<recovery_deadline:
            time.sleep(.25);first_recovery=plan_state(c,first['plan_id'])
        evidence['actions'].append({'name':'first_recovery_state','at_seconds':time.monotonic()-started,'result':first_recovery});save()
        evidence['checks']['one_repair_used']=first_recovery.get('auto_repairs_used')==1
        evidence['checks']['first_recovery_visibly_verified']=bool(first_recovery.get('final_status')=='PLAYING_VERIFIED' and
            first_recovery.get('evidence',{}).get('passed') and first_recovery.get('evidence',{}).get('playback_observed'))
        wait_until(28)
        action(c,'second_controlled_interruption',"document.getElementById('drift').onclick()")
        wait_until(36)
        budget=plan_state(c,first['plan_id'])
        evidence['actions'].append({'name':'repair_budget_state','at_seconds':time.monotonic()-started,'result':budget});save()
        evidence['checks']['budget_requires_authorization']=budget.get('final_status')=='NEEDS_AUTHORIZATION'
        old=action(c,'old_repair_speech_preview',"document.getElementById('fixture').onclick()")
        token=old.get('confirmation_id') if old else None
        if not token:raise RuntimeError('No old speech capability to revoke')
        wait_until(43)
        stopped=action(c,'stop_revokes_earlier_agreement',"document.getElementById('stop').onclick()")
        stopped_ui=state(c);evidence['checks']['stop_native_paused']=stopped_ui['paused'] and stopped_ui['time']<.1
        wait_until(49)
        retry=action(c,'stale_confirmation_rejected',"fetch('/api/voice/confirm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirmation_id:"+json.dumps(token)+"})}).then(async r=>{const body=await r.json();bubble(requestErrorMessage(body));return {http_status:r.status,body};})")
        evidence['checks']['old_consent_rejected']=retry['http_status']>=400
        evidence['checks']['stop_survives_old_confirm']=state(c)['paused']
        wait_until(56)
        new=action(c,'new_explicit_agreement_preview',"window.previewPlan({content_id:'clip-long',start_seconds:-1,end_seconds:45,auto_repairs:1})")
        evidence['checks']['new_preview_is_concrete']=bool(new.get('plan') and new.get('preview_id'))
        wait_until(61)
        renewed=action(c,'new_explicit_agreement_confirm','window.confirmVoice()')
        if not renewed or not renewed.get('plan_id'):raise RuntimeError('New agreement failed')
        remaining=max(1,a.seconds-(time.monotonic()-started)-5)
        done=await_boundary(c,renewed['plan_id'],remaining)
        evidence['boundary']=done;evidence['boundary_ui']=state(c)
        boundary_evidence=(done or {}).get('evidence') or (done or {}).get('boundary_observation') or {}
        evidence['checks']['boundary_verified']=bool(done and done.get('final_status')=='VERIFIED_DONE' and boundary_evidence.get('passed'))
        evidence['checks']['native_at_45']=evidence['boundary_ui']['paused'] and abs(evidence['boundary_ui']['time']-45)<.2
        evidence['checks']['new_generation']=renewed['mission_id']!=first['mission_id']
        evidence['api_responses']=c.evaluate('window.apiResponses');save()
        while recorder.poll() is None:time.sleep(.25)
        evidence['final_ui']=state(c)
        evidence['recorder_exit']=recorder.returncode
        evidence['status']='PASS' if recorder.returncode==0 and all(evidence['checks'].values()) else 'NOT_VERIFIED'
        save();print(json.dumps({'status':evidence['status'],'checks':evidence['checks'],'state_dir':str(root),'frames':str(frames)}),flush=True)
except Exception as error:
    evidence['status']='NOT_VERIFIED';evidence['error']=repr(error);save();print(json.dumps({'status':'NOT_VERIFIED','error':repr(error)}),flush=True)
    raise
finally:
    if recorder and recorder.poll() is None:
        recorder.terminate();recorder.wait(timeout=5)
    runtime.close()

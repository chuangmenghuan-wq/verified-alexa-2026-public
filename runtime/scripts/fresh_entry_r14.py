"""Exercise the installed public entry in this extracted folder, not run_owned directly."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
from verified_alexa.mcp_client import MCPClient
import verified_alexa

root=Path(__file__).resolve().parents[1]
proof=root/'validation';proof.mkdir(exist_ok=True)
state=proof/'entry-state';state.mkdir(exist_ok=True)
result={'scope':'actual Windows installed console entry, fresh state and actual MCP',
        'interpreter':sys.executable,'installed_module':verified_alexa.__file__,'checks':{}}
manifest=root/'FILE_MANIFEST.json'
if manifest.exists():
    items=json.loads(manifest.read_text(encoding='utf8'))['files']
    mismatches=[i['path'] for i in items if hashlib.sha256((root/i['path']).read_bytes()).hexdigest()!=i['sha256']]
    result['manifest_mismatches']=mismatches
    assert not mismatches,mismatches
env=os.environ.copy();env.pop('PYTHONPATH',None)
entry=Path(sys.executable).parent/('verified-alexa-sim.exe' if os.name=='nt' else 'verified-alexa-sim')
process=None
def start(initial_fault=False):
    global process
    (state/'stop-owned-demo').unlink(missing_ok=True)
    errors=(proof/('ENTRY_START_'+str(len(result.get('launches',[])))+'.log')).open('w',encoding='utf8')
    cmd=[str(entry),'--port','0','--state-dir',str(state),'--headless']
    if initial_fault:cmd.append('--initial-fault')
    process=subprocess.Popen(cmd,cwd=root,env=env,stdout=subprocess.PIPE,stderr=errors,text=True,encoding='utf8')
    line=process.stdout.readline()
    if not line:raise RuntimeError('Public entry did not emit launch JSON; see ENTRY_START log')
    launch=json.loads(line);result.setdefault('launches',[]).append(launch)
    return launch
def stop():
    if process and process.poll() is None:
        (state/'stop-owned-demo').write_text('stop only owned entry',encoding='utf8')
        try:process.wait(timeout=15)
        except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=10)
try:
    launch=start(True);client=MCPClient(launch['mcp_url'],proof/'ENTRY_MCP_1.json')
    original=client.call('execute_verified_goal',{'goal':'Play clip A'})
    result['original']=original
    result['checks']['false_success_rejected_then_recovered']=original['tool_claim']['status']=='SUCCESS' and original['first_independent_verification']['http_status']==503 and not original['first_independent_verification']['passed'] and original['final_status']=='VERIFIED_DONE'
    mid=original['mission_id'];aid=original['attempt_id']
    historical=client.call('get_execution_receipt',{'run_id':mid,'attempt_id':aid})
    client.call('inject_playback_fault',{'mission_id':mid})
    drift=client.call('recheck_playback',{'mission_id':mid});result['drift']=drift
    result['checks']['fresh_drift_downgrade']=drift['final_status']=='NOT_VERIFIED'
    stop();launch2=start();client2=MCPClient(launch2['mcp_url'],proof/'ENTRY_MCP_2.json')
    repaired=client2.call('repair_playback',{'mission_id':mid});result['repair_after_full_restart']=repaired
    after=client2.call('get_execution_receipt',{'run_id':mid,'attempt_id':aid})
    result['checks']['same_mission_restart_repair']=repaired['mission_id']==mid and repaired['target_id']==original['target_id'] and repaired['attempt_id']!=aid and repaired['final_status']=='VERIFIED_DONE'
    result['checks']['immutable_historical']=after['historical'] and after['current_status'] is None and after['receipt']['receipt_sha256']==historical['receipt']['receipt_sha256']
    preview=client2.call('preview_playback_plan',{'content_id':'clip-long','start_seconds':1,'end_seconds':5,'auto_repairs':1})
    context=client2.call('get_target_context',{})
    plan=client2.call('confirm_playback_plan',{'plan':preview['plan'],'expected_revision':context['revision']})
    result['plan_initial']=plan
    result['checks']['playing_not_segment_done']=plan['final_status']!='VERIFIED_DONE'
    deadline=time.monotonic()+35;done={}
    while time.monotonic()<deadline:
        done=client2.call('tick_playback_plan',{'mission_id':plan['mission_id']})
        if done['final_status']=='VERIFIED_DONE':break
        if done['final_status'] in ('NEEDS_AUTHORIZATION','CANCELLED','SUPERSEDED'):break
        time.sleep(.3)
    result['plan_final']=done
    result['checks']['actual_endpoint_and_stopped']=done.get('final_status')=='VERIFIED_DONE' and done.get('evidence',{}).get('passed') is True and done.get('evidence',{}).get('observed_state')=='stopped' and abs(done['position_seconds']-5)<.2
    cancelled=client2.call('cancel_playback_plan',{'mission_id':plan['mission_id']})
    result['cancel']=cancelled
    result['checks']['cancel_revokes']=cancelled['final_status']=='CANCELLED' and cancelled['revoked'] and cancelled['stop_verified']
    result['passed']=all(result['checks'].values())
finally:
    stop()
    (proof/'FRESH_ENTRY_RESULTS.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'passed':result.get('passed',False),'checks':result['checks']},indent=2))
if not result.get('passed'):raise SystemExit(1)

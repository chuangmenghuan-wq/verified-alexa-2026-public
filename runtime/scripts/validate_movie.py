"""Decode the whole delivered audio/video and inspect actual captured states."""
import argparse,hashlib,json,math,re,subprocess
from datetime import datetime
from pathlib import Path
from array import array
import imageio_ffmpeg

def independent_raw_checks(raw, states, evidence_root):
    """Recompute concrete trace predicates; bundled checks are not authority."""
    def obj(v):return v if isinstance(v,dict) else {}
    def body(v):return v['result'] if isinstance(v.get('result'),dict) else v
    actions={v['name']:v for v in raw.get('actions',[])}
    def result(name):return body(obj(actions.get(name,{}).get('result')))
    first=result('confirm_agreement'); recovery=result('first_recovery_state')
    failure=obj(first.get('first_independent_verification')); recovered=obj(recovery.get('evidence'))
    stop=result('stop_revokes_earlier_agreement'); stale=result('stale_confirmation_rejected')
    old_preview=result('old_repair_speech_preview'); new_preview=result('new_explicit_agreement_preview')
    new=result('new_explicit_agreement_confirm'); budget=result('repair_budget_state')
    boundary=obj(raw.get('boundary')); endpoint=obj(boundary.get('boundary_observation'))
    def samples(o):return o.get('native_samples',[]) if isinstance(o.get('native_samples'),list) else []
    def fresh_process(o):
        try:age=(datetime.fromisoformat(o['observed_at'])-datetime.fromisoformat(o['started_at'])).total_seconds()
        except (KeyError,ValueError,TypeError):return False
        return (0<=age<=20 and type(o.get('probe_pid')) is int and o.get('probe_pid')!=o.get('parent_pid')
                and isinstance(o.get('challenge'),str) and bool(re.fullmatch('[a-f0-9]{32}',o['challenge'])))
    def complete_identity(o):
        expected=('target_identity','content_identity','native_player_observation','goal_state','stream_identity',
                  'element_identity','visible_compositor','reference_content')
        return all(obj(o.get('checks')).get(k) is True for k in expected)
    frame_hashes=[]
    def measured_images(o):
        frames=o.get('presentation_frames',[])
        if len(frames)<2:return False
        good=True;root=Path(evidence_root).resolve()
        for frame in frames:
            path=(root/str(frame.get('relative_path',''))).resolve()
            safe=path.is_relative_to(root) and path.is_file()
            actual=hashlib.sha256(path.read_bytes()).hexdigest() if safe else None
            frame_hashes.append({'relative_path':frame.get('relative_path'),'recorded_sha256':frame.get('sha256'),
                                 'actual_sha256':actual,'at_native_time':frame.get('at_native_time')})
            errors=[obj(frame.get('reference_error')),obj(frame.get('compositor_error'))]
            good &= (safe and actual==frame.get('sha256') and frame.get('reference_passed') is True
                     and frame.get('compositor_passed') is True
                     and all(type(v.get('mean_absolute_error')) in (int,float)
                             and 0<=v['mean_absolute_error']<=15
                             and type(v.get('mismatch_fraction')) in (int,float)
                             and 0<=v['mismatch_fraction']<=.15 for v in errors))
        return bool(good)
    rs=samples(recovered);es=samples(endpoint)
    playing=[s for s in states if 14<=s.get('at_seconds',-1)<=28
             and 'Currently playing' in obj(s.get('ui')).get('progress','')
             and 'Actual video advancing' in obj(s.get('ui')).get('player','')]
    completion=[s for s in states if 'completed at its boundary' in obj(s.get('ui')).get('plan_status','')]
    visible45=[s for s in states if 'paused at 45.0' in obj(s.get('ui')).get('player','')]
    refusal=[s for s in states if 11<=s.get('at_seconds',-1)<=16
             and first.get('assistant_message') and obj(s.get('ui')).get('chat','').rstrip().endswith(first['assistant_message'].rstrip())
             and 'Player error' in obj(s.get('ui')).get('player','')]
    rejected_visible=[s for s in states if 47<=s.get('at_seconds',-1)<=56
                      and 'expired after your newer instruction' in obj(s.get('ui')).get('chat','')]
    duration_plan=obj(new_preview.get('plan'));stop_binding=obj(stop.get('binding'));new_binding=obj(new.get('binding'))
    new_start=duration_plan.get('start_seconds');saved=rs[-1].get('current_time') if rs else None
    checks={
        'executor_success_refused_by_fresh_http503':obj(first.get('tool_claim')).get('status')=='SUCCESS'
            and first.get('final_status')=='NOT_VERIFIED' and failure.get('passed') is False
            and failure.get('http_status')==503 and fresh_process(failure)
            and obj(failure.get('binding')).get('mission_id')==first.get('mission_id'),
        'failure_refusal_visible':bool(refusal),
        'first_recovery_native_motion_and_images':recovery.get('final_status')=='PLAYING_VERIFIED'
            and recovery.get('auto_repairs_used')==1 and recovered.get('passed') is True
            and recovered.get('http_status')==200 and fresh_process(recovered) and complete_identity(recovered)
            and len(rs)>=2 and all(s.get('paused') is False for s in rs)
            and rs[-1].get('current_time',0)>rs[0].get('current_time',0)
            and rs[-1].get('total_frames',0)>rs[0].get('total_frames',0) and measured_images(recovered),
        'first_recovery_visibly_verified_not_plan_done':bool(playing)
            and all('completed at its boundary' not in obj(s.get('ui')).get('plan_status','') for s in playing),
        'second_fault_exhausts_same_generation_allowance':budget.get('final_status')=='NEEDS_AUTHORIZATION'
            and budget.get('auto_repairs_used')==1 and budget.get('mission_id')==recovery.get('mission_id')
            and obj(budget.get('evidence')).get('http_status')==503 and obj(budget.get('evidence')).get('passed') is False,
        'stop_advances_authority_and_old_token_rejected':stop_binding.get('goal_revision',0)>old_preview.get('expected_revision',0)
            and stop.get('mission_id')!=old_preview.get('mission_id') and stale.get('http_status')==400
            and obj(stale.get('body')).get('error')=='PREVIEW_CONTEXT_CHANGED' and bool(rejected_visible)
            and len(samples(obj(stop.get('final_independent_verification'))))>=2
            and all(s.get('paused') is True and abs(s.get('current_time',999))<=.05
                    for s in samples(obj(stop.get('final_independent_verification')))),
        'new_generation_preserves_observed_bookmark_and_content':type(new_start) in (float,int)
            and type(saved) in (float,int) and abs(new_start-saved)<=.01
            and new.get('mission_id')!=recovery.get('mission_id')
            and new_binding.get('goal_revision',0)>stop_binding.get('goal_revision',0)
            and all(new_binding.get(k)==obj(recovered.get('binding')).get(k) for k in ('target_id','content_id','content_sha256'))
            and duration_plan.get('end_seconds')==45 and duration_plan.get('auto_repairs')==1,
        'endpoint_done_has_fresh_stopped_45_images':boundary.get('final_status')=='VERIFIED_DONE'
            and endpoint.get('passed') is True and fresh_process(endpoint) and complete_identity(endpoint)
            and obj(endpoint.get('binding')).get('mission_id')==new.get('mission_id')
            and obj(endpoint.get('binding')).get('operation')=='stop'
            and obj(endpoint.get('binding')).get('stop_position_seconds')==45
            and len(es)>=2 and all(s.get('paused') is True and abs(s.get('current_time',999)-45)<=.2 for s in es)
            and measured_images(endpoint),
        'endpoint_and_completion_visible':bool(visible45) and bool(completion)
            and completion[0]['at_seconds']>=visible45[0]['at_seconds']}
    facts={'first_visible_recovery':playing[0]['at_seconds'] if playing else None,
           'first_visible_stopped_45':visible45[0]['at_seconds'] if visible45 else None,
           'first_visible_completion':completion[0]['at_seconds'] if completion else None,
           'bookmark_seconds':new_start,'first_http_status':failure.get('http_status'),
           'first_tool_status':obj(first.get('tool_claim')).get('status'),
           'first_plan_status':first.get('final_status'),'recovery_status':recovery.get('final_status'),
           'stop_final_status':stop.get('final_status'),'stop_native_paused':all(s.get('paused') is True for s in samples(obj(stop.get('final_independent_verification')))),
           'old_token_http_status':stale.get('http_status'),'endpoint_status':boundary.get('final_status'),
           'endpoint_native_times':[s.get('current_time') for s in es],
           'observer_frame_hashes':frame_hashes,
           'visibility_limit':'Literal SUCCESS/HTTP503 fields are verified in the companion raw trace and narrated; the renderer shows a blank failed player and refusal, not an expanded technical JSON panel.'}
    return {'checks':checks,'facts':facts,'passed':all(checks.values())}

def narration_timing_checks(raw, states, narration, independent):
    """Gate each spoken outcome on renderer evidence and concrete raw predicates.

    Action timestamps are CDP completion elapsed times, not capture timestamps.
    Where useful, we require both as a conservative scheduling margin. Renderer
    times remain the authority for what is already visible in the movie.
    """
    segments={s['id']:s for s in narration.get('segments',[])}
    actions={s['name']:s for s in raw.get('actions',[])}
    def action_time(name):
        value=actions.get(name,{}).get('at_seconds')
        return value if type(value) in (int,float) else None
    def first_visible(predicate, after_seconds=0):
        matches=[s['at_seconds'] for s in states if s['at_seconds']>=after_seconds and predicate(s.get('ui',{}))]
        return min(matches) if matches else None
    def after(*values):
        return max(values) if values and all(type(v) in (int,float) for v in values) else None
    preview=first_visible(lambda u:'Start 20.0 s' in u.get('preview','')
        and 'stop at 45.0 s' in u.get('preview','') and '1 automatic repair' in u.get('preview','')
        and 'Recognized:' in u.get('chat',''))
    first=actions.get('confirm_agreement',{}).get('result',{})
    first=first.get('result',first)
    message=first.get('assistant_message','').rstrip()
    refusal=first_visible(lambda u:bool(message) and u.get('chat','').rstrip().endswith(message) and 'Player error' in u.get('player',''))
    budget=first_visible(lambda u:'Automatic repair allowance used' in u.get('plan_status',''),action_time('second_controlled_interruption') or 0)
    rejected=first_visible(lambda u:'expired after your newer instruction' in u.get('chat',''))
    repair_preview=first_visible(lambda u:'Only this saved target and objective' in u.get('preview',''))
    new_preview=first_visible(lambda u:'Start 27.7 s' in u.get('preview','') and 'stop at 45.0 s' in u.get('preview',''))
    facts=independent['facts'];raw_checks=independent['checks']
    requirements={
        '01':(0.,True,'scope disclosure; no outcome claimed'),
        '02':(after(preview,action_time('actual_synthetic_agreement_preview')),preview is not None,'recognized audio and concrete preview'),
        '03':(after(refusal,action_time('confirm_agreement')),raw_checks['executor_success_refused_by_fresh_http503'],'accepted executor; live 503; verification refusal'),
        '04':(after(facts['first_visible_recovery'],action_time('first_recovery_state')),raw_checks['first_recovery_native_motion_and_images'] and raw_checks['first_recovery_visibly_verified_not_plan_done'],'one repair; actual advancing playback; plan remains open'),
        '05':(budget,raw_checks['second_fault_exhausts_same_generation_allowance'],'allowance exhaustion and request for new permission are visible'),
        '06':(after(repair_preview,action_time('old_repair_speech_preview')),True,'conditional capability statement; repair preview exists'),
        '07':(after(rejected,action_time('stop_revokes_earlier_agreement'),action_time('stale_confirmation_rejected')),raw_checks['stop_advances_authority_and_old_token_rejected'],'Stop revokes authority and old token has been rejected; Stop verification remains NOT_VERIFIED'),
        '08':(after(new_preview,action_time('new_explicit_agreement_preview')),raw_checks['new_generation_preserves_observed_bookmark_and_content'],'new concrete preview preserves content and measured bookmark; confirmation is still required'),
        '09':(after(facts['first_visible_stopped_45'],facts['first_visible_completion']),raw_checks['endpoint_done_has_fresh_stopped_45_images'] and raw_checks['endpoint_and_completion_visible'],'paused 45-second endpoint and completed agreement; playing verification alone is insufficient'),
        '10':(facts['first_visible_completion'],independent['passed'],'bounded local MCP scope and explicit native Alexa/human usability limits')}
    rows=[]
    for segment_id,(required_at,predicate,claim) in requirements.items():
        segment=segments.get(segment_id,{})
        at=segment.get('at_seconds')
        passed=type(at) in (int,float) and required_at is not None and at>=required_at and bool(predicate)
        rows.append({'id':segment_id,'narration_at_seconds':at,'evidence_available_by_seconds':required_at,
                     'claim':claim,'raw_predicate_supported':bool(predicate),'passed':passed})
    starts=[s.get('at_seconds') for s in narration.get('segments',[])]
    checks={r['id']:r['passed'] for r in rows}
    checks['ordered_ten_segments']=len(segments)==10 and len(starts)==10 and all(type(t) in (float,int) for t in starts) and starts==sorted(starts)
    checks['bounded_schedule']=narration.get('duration_seconds')==118 and starts[-1]<=110.6
    checks['scope_disclosed']=('synthesized' in segments.get('01',{}).get('text','').lower()
        and 'native alexa' in segments.get('10',{}).get('text','').lower()
        and 'unverified' in segments.get('10',{}).get('text','').lower())
    return {'checks':checks,'segments':rows,'passed':all(checks.values()),
        'clock_limit':'Renderer SCREEN_STATES uses capture-relative monotonic time. Raw actions use a distinct CDP completion elapsed clock; this verifier does not equate those clocks. Both are required where possible as a conservative margin. Audio durations and overlaps require the regenerated WAV/mux validation.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--movie',required=True);p.add_argument('--frames',required=True);p.add_argument('--walkthrough',required=True);p.add_argument('--output',required=True);p.add_argument('--narration',default=str(Path(__file__).with_name('R14_NARRATION.json')));a=p.parse_args()
    movie=Path(a.movie).resolve();ff=imageio_ffmpeg.get_ffmpeg_exe()
    metadata=subprocess.run([ff,'-hide_banner','-i',str(movie)],capture_output=True)
    info=metadata.stderr.decode('utf8',errors='replace')
    duration=re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)',info)
    assert duration,info
    seconds=int(duration[1])*3600+int(duration[2])*60+float(duration[3])
    decoded=subprocess.run([ff,'-hide_banner','-loglevel','error','-i',str(movie),'-f','null','-'],capture_output=True,timeout=180)
    audio=subprocess.run([ff,'-hide_banner','-loglevel','error','-i',str(movie),'-vn','-ac','1','-ar','16000','-f','s16le','pipe:1'],capture_output=True,timeout=120)
    samples=array('h');samples.frombytes(audio.stdout)
    rms=math.sqrt(sum(x*x for x in samples)/len(samples)) if samples else 0
    active=sum(abs(x)>128 for x in samples)/len(samples) if samples else 0
    raw=json.loads(Path(a.walkthrough).read_text(encoding='utf8'))
    frame_info=json.loads((Path(a.frames)/'FRAME_TIMES.json').read_text(encoding='utf8'))
    states=json.loads((Path(a.frames)/'SCREEN_STATES.json').read_text(encoding='utf8'))
    endpoint=[s for s in states if 'paused at 45.0' in s.get('ui',{}).get('player','')]
    completed=[s for s in states if 'completed at its boundary' in s.get('ui',{}).get('plan_status','')]
    independent=independent_raw_checks(raw,states,Path(a.walkthrough).parent)
    narration_path=Path(a.narration);narration=json.loads(narration_path.read_text(encoding='utf8'))
    timing=narration_timing_checks(raw,states,narration,independent)
    checks={'duration_90_to_120':90<=seconds<=120,'full_av_decode':decoded.returncode==0,
        'audio_full_duration':audio.returncode==0 and abs(len(samples)/16000-seconds)<.15,
        'audio_non_silent':rms>100 and active>.15,'actual_capture':frame_info['duration_seconds']>=seconds-.5,
        'independent_concrete_raw_checks':independent['passed'],'narration_claim_timing':timing['passed'],
        'visible_native_endpoint':bool(endpoint),'visible_plan_completion':bool(completed)}
    report={'scope':'full encode/decode and signal check, actual renderer/raw MCP trace; not human acoustic or physical-microphone acceptance',
        'movie':str(movie),'sha256':hashlib.sha256(movie.read_bytes()).hexdigest(),'bytes':movie.stat().st_size,
        'duration_seconds':seconds,'audio_seconds':len(samples)/16000,'rms':rms,'active_sample_fraction':active,
        'capture_duration':frame_info['duration_seconds'],'frame_count':len(frame_info['frames']),
        'first_visible_stopped_45':endpoint[0]['at_seconds'] if endpoint else None,
        'first_visible_completed_card':completed[0]['at_seconds'] if completed else None,
        'checks':checks,'independent_raw':independent,'narration_timing':timing,
        'narration_sha256':hashlib.sha256(narration_path.read_bytes()).hexdigest(),
        'passed':all(checks.values()),'ffmpeg_metadata':info,'decode_stderr':decoded.stderr.decode('utf8',errors='replace')}
    Path(a.output).write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps({k:report[k] for k in ('passed','duration_seconds','rms','active_sample_fraction','checks')},indent=2))
    if not report['passed']:raise SystemExit(1)

if __name__=='__main__':main()

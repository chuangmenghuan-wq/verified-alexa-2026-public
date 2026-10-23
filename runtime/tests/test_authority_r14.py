"""Authority orchestration regressions; stubs are not media completion evidence."""
import threading
from datetime import datetime, timezone
import pytest
from verified_alexa.consumer_media_mission import LivingRoomPlaybackMission


class Executor:
    def __init__(self): self.actions=[]; self.operation='play'; self.position=7.; self.content='clip-a'
    def bind(self, mid, operation='play', content_id=None):
        return {'mission_id':mid,'intent':'playback','operation':operation,
            'desired_operation':self.operation if operation=='check' else operation,
            'content_id':content_id or self.content,'content_sha256':'a'*64,
            'target_id':'media-'+'a'*24,'browser_target_id':'owned-player',
            'cdp_endpoint':'http://127.0.0.1:1','media_url':'http://127.0.0.1:2/media/media-'+'a'*24,
            'duration':60.}
    def resolve_binding(self,binding):return dict(binding)
    def execute(self,binding):
        self.actions.append(('execute',binding['operation'],binding['mission_id']))
        self.operation=binding['operation'];self.content=binding['content_id']
        if self.operation=='play':self.position=binding.get('start_seconds',self.position)
        if self.operation=='stop':self.position=binding.get('stop_position_seconds',0.)
        return {'status':'SUCCESS'}
    def repair(self,binding):
        self.actions.append(('repair',binding['operation'],binding['mission_id']))
        return self.execute(binding)
    def stop_at_boundary(self,binding,end_seconds):
        return self.execute({**binding,'operation':'stop','stop_position_seconds':end_seconds})


@pytest.fixture
def mission(tmp_path):
    m=LivingRoomPlaybackMission(tmp_path);m.executor=Executor()
    def probe(binding):
        goal=binding.get('desired_operation',binding['operation']) if binding['operation']=='check' else binding['operation']
        ok=goal==m.executor.operation
        return {'passed':ok,'observed_at':datetime.now(timezone.utc).isoformat(),
                'binding':binding,'checks':{k:True for k in ('target_identity','content_identity','native_player_observation','element_identity','visible_compositor','reference_content')},'observed_state':'playing' if m.executor.operation=='play' else 'stopped',
                'native_samples':[{'currentTime':m.executor.position,'paused':m.executor.operation!='play','width':320}],
                'playback_observed':m.executor.operation=='play'}
    m._probe=probe
    return m


def test_stale_generic_confirmation_is_rejected_after_stop(mission):
    first=mission.run('Play clip A')
    preview=mission.preview_request('Yes, fix it',first['mission_id'])
    stopped=mission.run('Stop playback')
    before=list(mission.executor.actions)
    result=mission.confirm_request(preview['preview_id'])
    assert result['final_status']=='STALE_AUTHORIZATION'
    assert mission.executor.actions==before and mission.executor.operation=='stop'
    assert mission.get_target_context()['active_mission_id']==stopped['mission_id']


def test_superseded_explicit_repair_requires_new_authorization(mission):
    first=mission.run('Play clip A');mission.run('Stop playback')
    before=list(mission.executor.actions)
    assert mission.repair(first['mission_id'])['final_status']=='SUPERSEDED'
    assert mission.executor.actions==before


def test_stop_and_cancel_are_revocations_even_with_saved_mission_id(mission):
    first=mission.run('Play clip A')
    stopped=mission.request(first['mission_id'],'Stop playback')
    assert stopped['final_status']=='VERIFIED_DONE' and stopped['mission_id']!=first['mission_id']
    assert mission.repair(first['mission_id'])['final_status']=='SUPERSEDED'
    assert mission.request(stopped['mission_id'],'Cancel')['final_status']=='CANCELLED'
    assert mission.executor.operation=='stop'


def test_bookmark_needs_complete_identity_and_stop_zero_preserves_it(mission):
    first=mission.run('Play clip A');saved=mission.get_target_context()['bookmark']
    assert saved['position_seconds']==7
    m=mission._read(first['mission_id'])
    assert mission._record_observation(m,{'passed':False,'observed_at':datetime.now(timezone.utc).isoformat(),
        'binding':m['binding'],'checks':{},'native_samples':[{'current_time':99}]})
    assert mission.get_target_context()['bookmark']==saved
    mission.run('Stop playback')
    assert mission.get_target_context()['bookmark']==saved


def test_registered_content_failure_uses_title_or_generic_label():
    binding={'operation':'play','content_id':'clip-long','content_sha256':'a'*64}
    observation={'passed':False,'observed_state':'stopped','checks':{}}
    titled=LivingRoomPlaybackMission._message({**binding,'content_title':'Long Journey'},observation)
    generic=LivingRoomPlaybackMission._message(binding,observation)
    assert 'Long Journey is playing' in titled and 'clip G' not in titled
    assert 'the selected video is playing' in generic and 'clip G' not in generic


def test_plan_playing_is_not_done_and_boundary_is_fresh(mission):
    preview=mission.preview_playback_plan('clip-a',7,45,1)
    plan=mission.confirm_playback_plan(preview['plan'],preview['expected_revision'])
    assert plan['final_status']=='PLAYING_VERIFIED'
    assert plan['plan']['start_seconds']==7 and plan['plan']['end_seconds']==45
    mission.executor.position=45
    done=mission.tick_playback_plan(plan['mission_id'])
    assert done['final_status']=='VERIFIED_DONE'
    assert done['boundary_observation']['passed'] is True
    assert mission.executor.operation=='stop' and mission.executor.position==45


def test_allowance_and_cancel_survive_new_controller(mission):
    p=mission.preview_playback_plan('clip-a',7,45,1)
    first=mission.confirm_playback_plan(p['plan'],p['expected_revision'])
    mission.executor.operation='stop'
    repaired=mission.tick_playback_plan(first['mission_id'])
    assert repaired['auto_repairs_used']==1
    reboot=LivingRoomPlaybackMission(mission.root);reboot.executor=mission.executor;reboot._probe=mission._probe
    reboot.executor.operation='stop'
    before=len([a for a in reboot.executor.actions if a[0]=='repair'])
    result=reboot.tick_playback_plan(first['mission_id'])
    assert result['final_status']=='NEEDS_AUTHORIZATION'
    assert len([a for a in reboot.executor.actions if a[0]=='repair'])==before
    stopped=reboot.cancel_playback_plan(first['mission_id'])
    assert stopped['final_status']=='CANCELLED'
    again=LivingRoomPlaybackMission(mission.root);again.executor=mission.executor;again._probe=mission._probe
    assert again.tick_playback_plan(first['mission_id'])['final_status']=='CANCELLED'


def test_race_stop_during_old_probe_has_priority(mission):
    entered=threading.Event();release=threading.Event();old_probe=mission._probe;results=[]
    def blocked(binding):
        if binding['operation']=='play':
            entered.set();assert release.wait(3)
            return {'passed':False,'observed_at':datetime.now(timezone.utc).isoformat(),'checks':{}}
        return old_probe(binding)
    mission._probe=blocked
    worker=threading.Thread(target=lambda:results.append(mission.run('Play clip A')));worker.start()
    assert entered.wait(2)
    stopped=mission.run('Stop playback')
    assert stopped['final_status']=='VERIFIED_DONE'
    release.set();worker.join(3)
    assert not worker.is_alive() and results[0]['final_status']=='SUPERSEDED'
    assert mission.executor.operation=='stop'
    assert not any(a[0]=='repair' for a in mission.executor.actions)


def test_race_cancel_during_plan_probe_cannot_repair(mission):
    p=mission.preview_playback_plan('clip-a',7,45,1);first=mission.confirm_playback_plan(p['plan'],p['expected_revision'])
    entered=threading.Event();release=threading.Event();original=mission._probe;results=[]
    def blocked(binding):
        if binding['operation']=='play':
            entered.set();assert release.wait(3)
            return {'passed':False,'observed_at':datetime.now(timezone.utc).isoformat(),'checks':{}}
        return original(binding)
    mission._probe=blocked
    worker=threading.Thread(target=lambda:results.append(mission.tick_playback_plan(first['mission_id'])));worker.start()
    assert entered.wait(2)
    assert mission.cancel_playback_plan(first['mission_id'])['final_status']=='CANCELLED'
    release.set();worker.join(3)
    assert results[0]['final_status']=='CANCELLED' and mission.executor.operation=='stop'
    assert not any(a[0]=='repair' for a in mission.executor.actions)


def test_race_late_old_recheck_cannot_overwrite_new_bookmark(mission):
    first=mission.run('Play clip A');entered=threading.Event();release=threading.Event();original=mission._probe;results=[]
    def blocked(binding):
        if binding['mission_id']==first['mission_id']:
            entered.set();assert release.wait(3)
            return {'passed':True,'observed_at':datetime.now(timezone.utc).isoformat(),'checks':{},
                    'native_samples':[{'currentTime':33.,'paused':False}]}
        return original(binding)
    mission._probe=blocked
    worker=threading.Thread(target=lambda:results.append(mission.resume(first['mission_id'])));worker.start()
    assert entered.wait(2)
    stop=mission.run('Stop playback');context=mission.get_target_context()
    release.set();worker.join(3)
    assert results[0]['final_status']=='SUPERSEDED'
    assert mission.get_target_context()['active_mission_id']==stop['mission_id']
    assert mission.get_target_context()['bookmark']==context['bookmark']

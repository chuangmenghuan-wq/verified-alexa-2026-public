"""Intent-bound actual playback missions, immutable attempts, fresh rechecks."""
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
import time
from pathlib import Path
from datetime import datetime, timezone
from filelock import FileLock
from .intent import rejection
from .goal_contract import (parse_request, canonical_goal, canonical_plan, request_matches_binding,
                            goal_description, REQUIRED_OBSERVER_CHECKS)
from .media_runtime import MediaExecutor, atomic_save, state_root

def now():
    return datetime.now(timezone.utc).isoformat()

class LivingRoomPlaybackMission:
    MAX_ATTEMPTS = 3
    def __init__(self, store_dir=None):
        self.root = Path(store_dir) if store_dir else state_root()
        self.store_dir = self.root / 'missions'
        self.store_dir.mkdir(parents=True, exist_ok=True)
        self.executor = MediaExecutor(self.root)
        self.lock = FileLock(str(self.root / 'owned-player.lock'), timeout=5)
        self.effect_lock = FileLock(str(self.root / 'player-effect.lock'), timeout=6)
        self.context_path = self.root / 'target-context.json'
        self.preview_dir = self.root / 'previews'
        self.preview_dir.mkdir(exist_ok=True)

    def _path(self, mid, suffix):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', mid):
            raise ValueError('Invalid mission ID')
        return self.store_dir / (mid + suffix)

    @staticmethod
    def _hash(data):
        return hashlib.sha256(json.dumps({k:v for k,v in data.items() if k not in ('receipt_sha256', 'recheck_sha256')}, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()

    def _read(self, mid):
        data = json.loads(self._path(mid, '.mission.json').read_text(encoding='utf-8'))
        if data['mission_id'] != mid or data['binding']['mission_id'] != mid or data['binding']['intent'] != 'playback':
            raise ValueError('Mission binding mismatch')
        if data.get('goal_contract') != canonical_goal(data['binding']):
            raise ValueError('Mission goal predicate mismatch')
        requested = parse_request(data['goal'])
        if data.get('plan'):
            if data['plan'] != canonical_plan(data['plan']) or any(data['plan'][k] != data['binding'][k] for k in ('target_id','content_id','content_sha256')):
                raise ValueError('Playback plan binding mismatch')
        elif requested['operation'] != 'check' and not request_matches_binding(requested, data['binding']):
            raise ValueError('Original request does not match saved goal')
        return data

    def _probe(self, binding):
        challenge = uuid.uuid4().hex
        env = os.environ.copy()
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
        try:
            process = subprocess.run([sys.executable, '-m', 'verified_alexa.playback_observer', json.dumps(binding), challenge],
                                     env=env, capture_output=True, timeout=12)
            if process.returncode:
                raise RuntimeError('Observer process failed')
            observation = json.loads(process.stdout)
            if observation['challenge'] != challenge or observation['probe_pid'] == os.getpid():
                raise ValueError('Fresh observer identity mismatch')
            if observation.get('binding') != binding:
                raise ValueError('Observer mission/target/intent/attempt binding mismatch')
            checks = observation.get('checks')
            if type(observation.get('passed')) is not bool or not isinstance(checks, dict):
                raise ValueError('Observer result has no valid predicate checks')
            if observation['passed'] and (not REQUIRED_OBSERVER_CHECKS.issubset(checks)
                    or any(type(value) is not bool or not value for value in checks.values())):
                raise ValueError('Observer PASS does not prove every required goal condition')
            if observation.get('goal_operation') != binding['operation']:
                raise ValueError('Observer checked a different requested operation')
            observed = datetime.fromisoformat(observation['observed_at'])
            if abs((datetime.now(timezone.utc) - observed).total_seconds()) > 15:
                raise ValueError('Observer evidence is stale')
            observation['observer_stdout_sha256'] = hashlib.sha256(process.stdout).hexdigest()
            observation['parent_pid'] = os.getpid()
            return observation
        except Exception as error:
            return {'passed': False, 'observed_at': now(), 'challenge': challenge, 'error': str(error), 'checks': {},
                    'verifier': {'independent_process': False, 'source': 'observer unavailable; no verified read'}}

    def _tool_restart(self, binding):
        # Compatibility seam for false-SUCCESS regression injection. The binding
        # specifies the real operation; this is not a generic playback restart.
        issued = binding if binding['operation'] != 'check' else {
            **binding, 'operation': canonical_goal(binding)['operation']}
        return self.executor.execute(issued)

    @staticmethod
    def _clarify():
        return {'final_status': 'NEEDS_CLARIFICATION', 'accepted': False, 'current_status': None,
                'assistant_message': 'Which saved local-player mission should I check or repair?'}

    def _context(self):
        if self.context_path.exists():
            value = json.loads(self.context_path.read_text(encoding='utf-8'))
            if type(value.get('revision')) is not int or value['revision'] < 0:
                raise ValueError('Invalid target authority revision')
            return value
        return {'revision':0, 'active_mission_id':'', 'revoked':False, 'bookmark':None}

    def get_target_context(self):
        with self.lock:
            context = self._context()
        try:
            launch = json.loads((self.root/'launch.json').read_text(encoding='utf-8'))
            context['target_id'] = launch['target_id']
        except (OSError, ValueError, KeyError):
            context.setdefault('target_id', None)
        return context

    def _active(self, mission):
        context = self._context()
        return (context['active_mission_id'] == mission['mission_id']
                and context['revision'] == mission.get('authority_revision')
                and not context.get('revoked') and not mission.get('revoked'))

    @staticmethod
    def _fenced(mission):
        return {'mission_id':mission['mission_id'], 'plan_id':mission['mission_id'] if mission.get('plan') else None,
                'final_status':'CANCELLED' if mission.get('cancelled') else 'SUPERSEDED',
                'accepted':False, 'current_status':None, 'reason':'GOAL_AUTHORITY_REVOKED',
                'assistant_message':'That saved objective is no longer authorized. Confirm a new request to resume it.'}

    def _resolve(self, binding):
        resolved = self.executor.resolve_binding(binding) if hasattr(self.executor, 'resolve_binding') else dict(binding)
        if any(resolved.get(k) != binding.get(k) for k in ('mission_id','target_id','content_id','content_sha256')):
            raise ValueError('Runtime rebind changed stable goal identity')
        return resolved

    def _effect(self, mission, binding, action='execute', revocation=False):
        # No observer is called under either lock. Stop first persists a new
        # revision, then serializes only the bounded native effect.
        with self.effect_lock:
            with self.lock:
                current = self._read(mission['mission_id'])
                context = self._context()
                permitted = self._active(current)
                if revocation:
                    permitted = (context['revision']==binding['goal_revision']
                                 and context['active_mission_id']==binding['mission_id']
                                 and context.get('revoked') and binding.get('revocation_stop') is True
                                 and binding['operation']=='stop')
                if not permitted:
                    return {'status':'FENCED', 'outcome_verified':False}
            resolved = self._resolve(binding)
            if action=='boundary':
                return self.executor.stop_at_boundary(resolved, mission['plan']['end_seconds'])
            return self._tool_restart(resolved) if action=='execute' else getattr(self.executor, action)(resolved)

    def _activate(self, binding, goal, expected_revision=None, plan=None, lineage=None):
        with self.lock:
            context = self._context()
            if expected_revision is not None and context['revision'] != expected_revision:
                return None
            revision = context['revision']+1
            old_id = context.get('active_mission_id')
            if old_id and self._path(old_id,'.mission.json').exists():
                old = self._read(old_id)
                old.update(revoked=True, superseded_by=binding['mission_id'])
                atomic_save(self._path(old_id,'.mission.json'),old)
            binding = {**binding, 'goal_revision':revision, 'state_dir':str(self.root.resolve())}
            mission = {'mission_id':binding['mission_id'], 'goal':goal, 'intent':'playback',
                       'binding':binding, 'goal_contract':canonical_goal(binding),
                       'authority_revision':revision, 'requested_operation':binding['operation'],
                       'attempts':[], 'attempt_bindings':{}, 'action_attempts':0,
                       'revoked':False, 'created_at':now()}
            if plan:
                mission.update(plan=canonical_plan(plan), auto_repairs_used=0,
                               monitor_deadline=datetime.now(timezone.utc).timestamp()+120,
                               final_status='STARTING')
            if lineage:
                mission['lineage'] = lineage
            atomic_save(self._path(binding['mission_id'],'.mission.json'),mission)
            context.update(revision=revision, active_mission_id=binding['mission_id'], revoked=False,
                           target_id=binding['target_id'])
            atomic_save(self.context_path,context)
            return mission

    def _reserve(self, mission, action=True):
        runtime_binding=self._resolve(mission['binding'])
        with self.lock:
            current = self._read(mission['mission_id'])
            if not self._active(current):
                return None
            if action and current.get('action_attempts',len(current['attempts'])) >= self.MAX_ATTEMPTS:
                return None
            aid = 'attempt-'+uuid.uuid4().hex[:16]
            binding = {**runtime_binding, 'attempt_id':aid}
            current['attempts'].append(aid)
            current.setdefault('attempt_bindings',{})[aid] = binding
            if action:
                current['action_attempts'] = current.get('action_attempts',0)+1
            atomic_save(self._path(current['mission_id'],'.mission.json'),current)
            mission.update(current)
            return binding

    @staticmethod
    def _position(observation):
        samples = observation.get('native_samples', observation.get('samples',[]))
        for sample in reversed(samples):
            value = sample.get('current_time',sample.get('currentTime')) if isinstance(sample,dict) else None
            if type(value) in (float,int) and value >= 0:
                return float(value)
        return None

    def _record_observation(self, mission, observation):
        with self.lock:
            current = self._read(mission['mission_id'])
            if not self._active(current):
                return False
            position = self._position(observation)
            checks = observation.get('checks',{})
            # Persist only freshly observed native positions for the same
            # target/content. A failed stream may still yield a truthful cursor.
            observed_binding=observation.get('binding') or {}
            identity = all(observed_binding.get(k)==mission['binding'].get(k) for k in ('mission_id','target_id','content_id','content_sha256'))
            required=('target_identity','content_identity','native_player_observation','element_identity','visible_compositor','reference_content')
            try:fresh=abs((datetime.now(timezone.utc)-datetime.fromisoformat(observation['observed_at'])).total_seconds())<=15
            except (KeyError,ValueError,TypeError):fresh=False
            reset_stop=observed_binding.get('operation')=='stop' and not observed_binding.get('stop_position_seconds')
            if position is not None and identity and fresh and not reset_stop and not observation.get('error') and all(checks.get(k) is True for k in required):
                context = self._context()
                context['bookmark'] = {'content_id':mission['binding']['content_id'],
                    'content_sha256':mission['binding']['content_sha256'], 'target_id':mission['binding']['target_id'],
                    'position_seconds':position, 'observed_at':observation['observed_at'],
                    'mission_id':mission['mission_id'], 'goal_revision':mission['authority_revision']}
                atomic_save(self.context_path,context)
            return True

    def request(self, mid, user_request, allow_recovery=True, on_event=None, expected_revision=None):
        requested = parse_request(user_request)
        operation = requested['operation']
        if operation=='unsupported': return rejection(user_request)
        if operation=='plan': return self.preview_request(user_request)
        if operation=='decline': return self.decline(mid)
        if operation=='cancel': return self.cancel_playback_plan(mid,expected_revision) if mid else self._clarify()
        if operation=='resume':
            return self.resume_playback_plan(mid, expected_revision) if mid else self._clarify()
        if not mid:
            if operation in ('recheck','repair'): return self._clarify()
            return self.run(user_request,allow_recovery,on_event=on_event,expected_revision=expected_revision)
        if operation in ('recheck','check'): return self.resume(mid)
        if operation=='repair': return self.repair(mid,on_event=on_event)
        if operation=='stop':
            with self.lock:existing=self._read(mid)
            if existing['goal_contract']['operation']!='stop' or not self._active(existing):
                return self.run(user_request,False,on_event=on_event,expected_revision=expected_revision)
        with self.lock:
            mission = self._read(mid)
            if not request_matches_binding(requested,mission['binding']):
                return {'mission_id':mid,'final_status':'GOAL_CONFLICT','accepted':False,'current_status':None,
                        'reason':'MISSION_GOAL_CONFLICT','requested_goal':requested,'saved_goal':mission['goal_contract'],
                        'assistant_message':'That changes the saved goal. Confirm a new mission for the new request.'}
        return self.resume(mid)

    def decline(self, mid=''):
        with self.lock:
            for path in self.preview_dir.glob('*.json'):
                data=json.loads(path.read_text(encoding='utf-8'))
                if not mid or data.get('mission_id')==mid:
                    data['revoked']=True;atomic_save(path,data)
        return {'mission_id':mid or None,'final_status':'DECLINED','accepted':False,'current_status':None,
                'assistant_message':'Okay. I will not change the player or repair it.'}

    def run(self, user_request='Play clip A', allow_recovery=True, mission_id=None,
            adversarial_interrupt=False, on_event=None, expected_revision=None):
        requested=parse_request(user_request);operation=requested['operation']
        if operation=='unsupported':return rejection(user_request)
        if operation not in ('play','pause','stop','check'):
            return self.request(mission_id or '',user_request,allow_recovery,on_event,expected_revision)
        mid=mission_id or 'mission-'+uuid.uuid4().hex[:20]
        if self._path(mid,'.mission.json').exists():return self.request(mid,user_request,allow_recovery,on_event,expected_revision)
        # Bind the current content and publish the new authority revision under
        # the same effect lock. A concurrent Play must not switch content between
        # a Stop binding its target and the Stop fencing older effects.
        with self.effect_lock:
            try:
                binding=self.executor.bind(mid,operation=operation,content_id=requested['content_id'])
                if operation!='check' and not request_matches_binding(requested,binding):raise ValueError('Executor changed requested goal')
            except (OSError,KeyError,ValueError,RuntimeError) as error:
                return {'final_status':'UNAVAILABLE','accepted':False,'current_status':None,'error':str(error),
                        'assistant_message':'The owned player is unavailable.'}
            mission=self._activate(binding,user_request,expected_revision)
        if mission is None:return self._stale()
        return self._attempt(mission,allow_recovery,adversarial_interrupt,on_event,perform_action=operation!='check')

    @staticmethod
    def _stale():
        return {'final_status':'STALE_AUTHORIZATION','accepted':False,'current_status':None,
                'reason':'PREVIEW_CONTEXT_CHANGED','assistant_message':'The objective changed. Review and confirm a fresh preview.'}

    def _attempt(self, mission, allow_recovery, interrupt=False, on_event=None, perform_action=True, repair_action=False):
        binding=self._reserve(mission,action=perform_action)
        if binding is None:
            with self.lock:current=self._read(mission['mission_id'])
            if not self._active(current):return self._fenced(current)
            return {'mission_id':mission['mission_id'],'final_status':'NOT_VERIFIED','reason':'ATTEMPT_LIMIT_REACHED',
                    'current_status':None,'assistant_message':'The repair limit has been reached.'}
        timeline=[]
        def emit(stage,status,summary,evidence=None):
            event={'sequence':len(timeline)+1,'stage':stage,'status':status,'summary':summary,'at':now()}
            if evidence is not None:event['evidence']=evidence
            timeline.append(event)
            if on_event:on_event(event)
        emit('ALEXA','CHECKING','I’m checking the requested player state now.')
        action={'status':'NOT_REQUESTED','outcome_verified':False};recovery=None;action_failed=False
        try:
            if perform_action:
                if repair_action:action=self._effect(mission,binding,'repair' if canonical_goal(binding)['operation']=='play' else 'execute')
                else:action=self._effect(mission,binding)
                emit('EXECUTOR','CLAIMED_SUCCESS' if action.get('status')=='SUCCESS' else 'UNAVAILABLE','Command response is not completion proof.',action)
            first=self._probe(binding)
        except Exception as error:
            action_failed=True
            first={'passed':False,'observed_at':now(),'error':str(error),'checks':{}}
        final=first
        if not self._record_observation(mission,first):return self._fenced(self._read(mission['mission_id']))
        emit('OBSERVER','PASS' if first['passed'] else 'FAIL','A separate observer measures the requested outcome.',first)
        if not first['passed'] and allow_recovery and perform_action and not repair_action and not action_failed and canonical_goal(binding)['operation']=='play' and not mission.get('plan'):
            try:
                recovery=self._effect(mission,binding,'repair')
                if recovery.get('status')=='FENCED':return self._fenced(self._read(mission['mission_id']))
                if interrupt:self._effect(mission,binding,'drift')
                final=self._probe(binding)
            except Exception as error:
                final={'passed':False,'observed_at':now(),'error':str(error),'checks':{}}
        if not self._record_observation(mission,final):return self._fenced(self._read(mission['mission_id']))
        status=('PLAYING_VERIFIED' if mission.get('plan') else 'VERIFIED_DONE') if final['passed'] else 'NOT_VERIFIED'
        message=self._message(binding,final)
        if mission.get('plan') and final['passed']:message='Playback is verified now. The segment is still in progress; its endpoint has not completed.'
        emit('ALEXA',status,message)
        receipt={'schema':'verified-alexa-goal-bound-playback/v3','mission_id':mission['mission_id'],'run_id':mission['mission_id'],
            'attempt_id':binding['attempt_id'],'target_id':binding['target_id'],'intent':'playback','operation':binding['operation'],
            'goal':mission['goal'],'goal_contract':mission['goal_contract'],'binding':binding,'tool_claim':action,
            'first_independent_verification':first,'recovery':recovery,'final_independent_verification':final,
            'final_status':status,'assistant_message':message,'verified_at':final['observed_at'] if final['passed'] else None,
            'created_at':now(),'timeline':timeline,'scope':'actual owned local media; simulated Alexa+; historical attempt',
            'receipt_security':'SHA-256 self-consistency, not authentication or current proof'}
        if mission.get('plan'):receipt.update(plan=mission['plan'],plan_id=mission['mission_id'],auto_repairs_used=mission['auto_repairs_used'])
        receipt['receipt_sha256']=self._hash(receipt)
        with self.lock:
            current=self._read(mission['mission_id'])
            if not self._active(current):return self._fenced(current)
            path=self._path(mission['mission_id'],'.'+binding['attempt_id']+'.receipt.json')
            with path.open('x',encoding='utf-8') as output:json.dump(receipt,output,ensure_ascii=False,indent=2)
            if mission.get('plan'):
                current.update(final_status=status,last_observation=final);atomic_save(self._path(mission['mission_id'],'.mission.json'),current)
            atomic_save(self._path(mission['mission_id'],'.latest-status.json'),{'mission_id':mission['mission_id'],'attempt_id':binding['attempt_id'],'final_status':status,'checked_at':final['observed_at']})
        return receipt

    def preview_playback_plan(self, content_id, start_seconds=-1, end_seconds=45, auto_repairs=1):
        context=self.get_target_context()
        binding=self.executor.bind('preview',operation='play',content_id=content_id)
        bookmark=context.get('bookmark') or {}
        if start_seconds==-1:
            start_seconds=bookmark.get('position_seconds',0.) if bookmark.get('content_id')==content_id and bookmark.get('content_sha256')==binding['content_sha256'] else 0.
        plan=canonical_plan({**binding,'start_seconds':start_seconds,'end_seconds':end_seconds,'auto_repairs':auto_repairs})
        duration=binding.get('duration',binding.get('content_duration'))
        if duration is not None and plan['end_seconds']>float(duration):raise ValueError('Endpoint exceeds this content duration')
        return {'plan':plan,'expected_revision':context['revision'],'confirmation_required':True,'current_status':None,
                'concrete_goal':f"Play {content_id} from {plan['start_seconds']:g}s to {plan['end_seconds']:g}s; repair at most {auto_repairs} time(s)."}

    def confirm_playback_plan(self, plan, expected_revision, lineage=None):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError('Confirmation requires an integer authority revision')
        plan=canonical_plan(plan)
        mid='mission-'+uuid.uuid4().hex[:20]
        binding=self.executor.bind(mid,operation='play',content_id=plan['content_id'])
        if any(binding[k]!=plan[k] for k in ('target_id','content_id','content_sha256')):return self._stale()
        duration=binding.get('duration',binding.get('content_duration'))
        if duration is not None and plan['end_seconds']>float(duration):raise ValueError('Endpoint exceeds duration')
        binding.update(start_seconds=plan['start_seconds'],end_seconds=plan['end_seconds'])
        mission=self._activate(binding,'Confirmed playback segment',expected_revision,plan,lineage)
        if mission is None:return self._stale()
        return self._attempt(mission,False)

    def preview_request(self, goal='', mission_id='', new_mission=False, plan=None):
        request=parse_request(goal)
        if plan is None and request['operation']=='plan':
            plan=self.preview_playback_plan(request['content_id'],request['start_seconds'],request['end_seconds'],request['auto_repairs'])['plan']
        context=self.get_target_context()
        if plan is not None:
            plan=canonical_plan(plan);concrete=f"Play {plan['content_id']} from {plan['start_seconds']:g}s to {plan['end_seconds']:g}s, with {plan['auto_repairs']} repair(s)."
            binding={**plan,'authority_revision':context['revision']}
        else:
            if request['operation']=='decline':return self.decline(mission_id)
            if request['operation']=='unsupported':return rejection(goal)
            if request['operation'] in ('repair','recheck','resume') and not mission_id:return self._clarify()
            if mission_id and self._path(mission_id,'.mission.json').exists():
                saved=self._read(mission_id);binding={**saved['binding'],'authority_revision':context['revision']}
                concrete=goal_description(saved['binding'])+'; '+request['operation']+' this saved objective'
            else:
                binding={'operation':request['operation'],'content_id':request.get('content_id'),'target_id':context.get('target_id'),'authority_revision':context['revision']}
                concrete=goal
        pid=uuid.uuid4().hex
        record={'preview_id':pid,'mission_id':mission_id,'goal':goal,'new_mission':new_mission,
                'plan':plan,'expected_revision':context['revision'],'expires_at':datetime.now(timezone.utc).timestamp()+90,
                'revoked':False,'consumed':False,'parsed_request':request,'binding':binding,'concrete_goal':concrete}
        with self.lock:
            if self._context()['revision']!=context['revision']:return self._stale()
            atomic_save(self.preview_dir/(pid+'.json'),record)
        return {**record,'confirmation_required':True,'final_status':'AWAITING_CONFIRMATION','current_status':None}

    def _preview_path(self,pid):
        if not re.fullmatch(r'[a-f0-9]{32}',pid):raise ValueError('Invalid preview ID')
        return self.preview_dir/(pid+'.json')

    def confirm_request(self, preview_id):
        with self.lock:
            path=self._preview_path(preview_id)
            if not path.exists():return self._stale()
            record=json.loads(path.read_text(encoding='utf-8'))
            if record.get('revoked') or record.get('consumed') or record['expires_at']<datetime.now(timezone.utc).timestamp() or record['expected_revision']!=self._context()['revision']:
                return self._stale()
            record['consumed']=True;atomic_save(path,record)
        if record.get('plan'):
            return self.confirm_playback_plan(record['plan'],record['expected_revision'])
        mid='' if record['new_mission'] and record['parsed_request']['operation'] in ('play','pause','stop','check') else record['mission_id']
        return self.request(mid,record['goal'],expected_revision=record['expected_revision'])

    def revoke_request(self, preview_id):
        with self.lock:
            path=self._preview_path(preview_id)
            if path.exists():
                record=json.loads(path.read_text(encoding='utf-8'));record['revoked']=True;atomic_save(path,record)
        return {'final_status':'PREVIEW_REVOKED','current_status':None,'accepted':False,'assistant_message':'Confirmation cancelled. No player action taken.'}


    @staticmethod
    def _message(binding, observation, recheck=False):
        description = goal_description(binding)
        if observation['passed']:
            return ('Fresh check: ' if recheck else 'Verified — ') + description + ' now.'
        if observation.get('error'):
            return 'I could not observe the requested player state, so I cannot verify it. You can ask me to try the same goal again.'
        checks = observation.get('checks', {})
        state = observation.get('observed_state', 'unknown')
        playing = observation.get('playback_observed') is True
        operation = canonical_goal(binding)['operation']
        metadata = binding.get('content_metadata') or binding.get('metadata') or {}
        title = binding.get('content_title') or (metadata.get('title') if isinstance(metadata, dict) else None)
        clip = title.strip() if isinstance(title, str) and title.strip() else 'the selected video'
        if checks.get('target_identity') is False:
            return 'The player is using a different or missing source. I cannot verify the saved goal for ' + clip + '.'
        samples = observation.get('native_samples', observation.get('samples', []))
        has_decoded_image = any(sample.get('width', 0) > 0 for sample in samples if isinstance(sample, dict))
        if checks.get('content_identity') is False and has_decoded_image:
            return 'The player shows different or unrecognized content. I cannot verify the requested ' + clip + '.'
        if playing and observation.get('stream_health') is False:
            return 'The player is still advancing, but the media stream is unavailable or has the wrong content. Reliable playback of ' + clip + ' is not verified.'
        if operation == 'play' and state in ('paused', 'stopped'):
            return 'The player is ' + state + '. I cannot verify that ' + clip + ' is playing. Want me to restore this same goal?'
        if operation in ('pause', 'stop') and state == 'playing':
            return 'The player is still playing. Your ' + operation + ' request is not verified.'
        if operation == 'stop' and state == 'paused':
            return 'The player is paused, but I have not verified that it is stopped at the beginning.'
        if state == 'unknown':
            unavailable = observation.get('http_status') == 503 or observation.get('stream_health') is False
            return ('The media stream is unavailable. ' if unavailable else '') + 'The current player state is unknown, so I cannot verify the requested goal.'
        return 'I checked, but I cannot verify that ' + description + '. Want me to retry this same goal?'

    def get_receipt(self, mid, attempt_id=None):
        if not self._path(mid, '.mission.json').exists():
            return None
        mission = self._read(mid)
        aid = attempt_id or (mission['attempts'][-1] if mission['attempts'] else None)
        if aid not in mission['attempts']:
            raise ValueError('Unknown attempt')
        path=self._path(mid, '.' + aid + '.receipt.json')
        if not path.exists():
            return None  # A crash/fence consumed the reservation; it proves no outcome.
        data = json.loads(path.read_text(encoding='utf-8'))
        expected = mission.get('attempt_bindings', {}).get(aid, {**mission['binding'], 'attempt_id': aid})
        if data['receipt_sha256'] != self._hash(data) or data['mission_id'] != mid or data['attempt_id'] != aid or data['binding'] != expected or data['goal'] != mission['goal'] or data['target_id'] != expected['target_id'] or data['intent'] != expected['intent'] or data.get('goal_contract') != mission['goal_contract'] or data.get('operation') != expected['operation']:
            raise ValueError('Receipt integrity mismatch')
        if mission.get('plan') and data.get('plan') != mission['plan']:
            raise ValueError('Receipt segment contract mismatch')
        return {**data, 'historical': True, 'current_status': None}

    def resume(self, mid):
        with self.lock:
            mission=self._read(mid)
            if not self._active(mission):return self._fenced(mission)
        if mission.get('plan'):return self.tick_playback_plan(mid)
        previous_path=self._path(mid,'.latest-status.json')
        previous=json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
        historical=self.get_receipt(mid) if mission['attempts'] and self._path(mid,'.'+mission['attempts'][-1]+'.receipt.json').exists() else None
        result=self._attempt(mission,False,perform_action=False)
        result.update(status_source='fresh actual player observer',evidence=result.get('final_independent_verification'),
                      previous_status=previous.get('final_status'),historical_receipt_sha256=historical['receipt_sha256'] if historical else None)
        return result

    def repair(self, mid, on_event=None):
        with self.lock:
            mission=self._read(mid)
            if not self._active(mission):return self._fenced(mission)
        if mission.get('plan'):
            return {'mission_id':mid,'plan_id':mid,'final_status':'NEEDS_AUTHORIZATION','current_status':None,
                    'assistant_message':'The segment has a fixed repair allowance. Confirm a new generation to continue after it is used.'}
        return self._attempt(mission,True,on_event=on_event)

    def inject_drift(self, mid):
        with self.lock:
            mission=self._read(mid)
            if not self._active(mission):return self._fenced(mission)
        result=self._effect(mission,self._resolve(mission['binding']),'drift')
        return {'mission_id':mid,'plan_id':mid if mission.get('plan') else None,**result,'current_status':None}

    def cancel_playback_plan(self, mid, expected_revision=None):
        with self.lock:
            mission=self._read(mid)
            context=self._context()
            if expected_revision is not None and context['revision']!=expected_revision:return self._stale()
            if not self._active(mission):return self._fenced(mission)
            revision=context['revision']+1
            # Revocation is durable BEFORE waiting for any native effect.
            mission.update(revoked=True,cancelled=True,final_status='CANCELLED',cancelled_at=now())
            atomic_save(self._path(mid,'.mission.json'),mission)
            context.update(revision=revision,revoked=True,active_mission_id=mid)
            atomic_save(self.context_path,context)
            binding={**mission['binding'],'operation':'stop','desired_operation':'stop',
                     'goal_revision':revision,'revocation_stop':True}
            binding.pop('stop_position_seconds',None)
        try:
            action=self._effect(mission,binding,revocation=True)
            observed=self._probe(self._resolve(binding))
        except Exception as error:
            action={'status':'UNAVAILABLE','error':str(error)}
            observed={'passed':False,'observed_at':now(),'error':str(error),'checks':{}}
        return {'mission_id':mid,'plan_id':mid if mission.get('plan') else None,'final_status':'CANCELLED',
                'current_status':None,'revoked':True,'goal_revision':revision,'stop_verified':observed['passed'],
                'evidence':observed,'action':action,'assistant_message':'The objective is cancelled. Old confirmations and automatic repairs cannot resume it.'}

    def resume_playback_plan(self, mid, expected_revision=None):
        if type(expected_revision) is not int or expected_revision < 0:
            return self._stale()
        with self.lock:
            mission=self._read(mid);context=self._context()
            if expected_revision is None:return self._stale()
            if context['revision']!=expected_revision:return self._stale()
            bookmark=context.get('bookmark') or {}
        if not mission.get('plan'):
            requested=mission['goal']
            return self.run(requested,expected_revision=expected_revision)
        plan=dict(mission['plan'])
        if bookmark.get('content_id')==plan['content_id'] and bookmark.get('content_sha256')==plan['content_sha256']:
            plan['start_seconds']=bookmark['position_seconds']
        if plan['start_seconds']>=plan['end_seconds']:
            return {'final_status':'NEEDS_CLARIFICATION','current_status':None,'assistant_message':'This segment already reached its endpoint. Confirm a new segment to replay.'}
        return self.confirm_playback_plan(plan,expected_revision,lineage={
            'previous_mission_id':mid,'previous_goal_revision':mission['authority_revision'],
            'authorization':'explicit resume confirmation'})

    def _plan_result(self, mission, observation, status, **extra):
        with self.lock:
            current=self._read(mission['mission_id'])
            if not self._active(current):return self._fenced(current)
            current.update(final_status=status,last_observation=observation,checked_at=observation['observed_at'])
            atomic_save(self._path(current['mission_id'],'.mission.json'),current)
            result={'mission_id':current['mission_id'],'plan_id':current['mission_id'],'plan':current['plan'],
                'goal_revision':current['authority_revision'],'final_status':status,'evidence':observation,
                'checked_at':observation['observed_at'],'position_seconds':self._position(observation),
                'auto_repairs_used':current['auto_repairs_used'],'status_source':'fresh actual player observer',
                'valid_for_seconds':2,'current_status':status,
                'assistant_message':{'PLAYING_VERIFIED':'Playback is verified now; the segment is still in progress.',
                'VERIFIED_DONE':'The requested segment endpoint and stopped player are freshly verified.',
                'NEEDS_AUTHORIZATION':'Playback is not verified. The repair allowance is used; confirm a new generation to continue.',
                'NOT_VERIFIED':'The segment outcome is not verified.',
                'MONITOR_EXPIRED':'The bounded monitor expired. Confirm a new generation to continue.'}[status]}
            result.update(extra)
            atomic_save(self._path(current['mission_id'],'.latest-status.json'),result)
            return result

    def tick_playback_plan(self, mid):
        self._path(mid,'.mission.json')  # validate before deriving lock path
        tick_lock=FileLock(str(self.root/(mid+'.monitor.lock')),timeout=0)
        try:tick_lock.acquire()
        except Exception:
            with self.lock:
                mission=self._read(mid)
                if not self._active(mission):return self._fenced(mission)
                observation=mission.get('last_observation') or {}
            try:fresh=(datetime.now(timezone.utc)-datetime.fromisoformat(observation['observed_at'])).total_seconds()<=2
            except (KeyError,ValueError,TypeError):fresh=False
            return {'mission_id':mid,'plan_id':mid,'plan':mission['plan'],
                    'final_status':mission.get('final_status','NOT_VERIFIED') if fresh else 'NOT_VERIFIED',
                    'current_status':None,'evidence':observation,'cached_observation':True,
                    'checked_at':observation.get('observed_at'),'position_seconds':self._position(observation),
                    'goal_revision':mission['authority_revision'],'auto_repairs_used':mission['auto_repairs_used'],
                    'assistant_message':'An observation is in progress. This saved observation retains its original expiry.'}
        try:
            with self.lock:
                mission=self._read(mid)
                if not mission.get('plan'):raise ValueError('Mission has no segment plan')
                if not self._active(mission):return self._fenced(mission)
                if mission.get('final_status')=='STARTING':
                    return {'mission_id':mid,'plan_id':mid,'plan':mission['plan'],'final_status':'STARTING','current_status':None}
            binding=self._resolve(mission['binding'])
            observation=self._probe(binding)
            if not self._record_observation(mission,observation):return self._fenced(self._read(mid))
            position=self._position(observation)
            end=mission['plan']['end_seconds']
            checks=observation.get('checks',{})
            identity_checks=('content_identity','target_identity','native_player_observation','element_identity','visible_compositor','reference_content')
            if position is not None and position>=end-.05 and not observation.get('error') and all(checks.get(k) is True for k in identity_checks):
                boundary={**binding,'operation':'stop','desired_operation':'stop','stop_position_seconds':end}
                try:
                    action=self._effect(mission,boundary,'boundary')
                    if action.get('status')=='FENCED':return self._fenced(self._read(mid))
                    final=self._probe(boundary)
                except Exception as error:
                    final={'passed':False,'observed_at':now(),'error':str(error),'checks':{}}
                if not self._record_observation(mission,final):return self._fenced(self._read(mid))
                endpoint=self._position(final)
                done=final['passed'] and endpoint is not None and abs(endpoint-end)<=.2
                return self._plan_result(mission,final,'VERIFIED_DONE' if done else 'NOT_VERIFIED',boundary_observation=final,boundary_binding=boundary)
            if datetime.now(timezone.utc).timestamp()>=mission['monitor_deadline']:
                # An expired autonomous lease must fail closed and pause; a new
                # confirmation is required to continue its bounded monitor.
                paused={**binding,'operation':'pause','desired_operation':'pause'}
                self._effect(mission,paused)
                return self._plan_result(mission,observation,'MONITOR_EXPIRED')
            if observation['passed']:
                return self._plan_result(mission,observation,'PLAYING_VERIFIED')
            with self.lock:
                current=self._read(mid)
                if not self._active(current):return self._fenced(current)
                if current['auto_repairs_used']>=current['plan']['auto_repairs']:
                    return self._plan_result(current,observation,'NEEDS_AUTHORIZATION')
                if current.get('action_attempts',0)>=self.MAX_ATTEMPTS:
                    return self._plan_result(current,observation,'NEEDS_AUTHORIZATION')
                current['auto_repairs_used']+=1
                atomic_save(self._path(mid,'.mission.json'),current)
                bookmark=self._context().get('bookmark') or {}
                start=bookmark.get('position_seconds',current['plan']['start_seconds'])
                mission=current
            # Reservation and quota consumption precede the side effect; a
            # crash cannot replenish either durable allowance.
            reserved=self._reserve(mission,action=True)
            if reserved is None:return self._fenced(self._read(mid))
            reserved['start_seconds']=min(max(start,mission['plan']['start_seconds']),end)
            # Persist actual attempt binding before issuing it.
            with self.lock:
                current=self._read(mid)
                current['attempt_bindings'][reserved['attempt_id']]=reserved
                atomic_save(self._path(mid,'.mission.json'),current)
            action={'status':'UNAVAILABLE','outcome_verified':False}
            try:
                action=self._effect(mission,reserved,'repair')
                if action.get('status')=='FENCED':return self._fenced(self._read(mid))
                final=self._probe(reserved)
            except Exception as error:
                final={'passed':False,'observed_at':now(),'error':str(error),'checks':{}}
            if not self._record_observation(mission,final):return self._fenced(self._read(mid))
            result=self._plan_result(mission,final,'PLAYING_VERIFIED' if final['passed'] else 'NEEDS_AUTHORIZATION',repair_attempt_id=reserved['attempt_id'],repair_action=action)
            # Every recovery attempt has immutable independently read evidence.
            attempt={'schema':'verified-alexa-plan-recovery/v1','mission_id':mid,'run_id':mid,'attempt_id':reserved['attempt_id'],
                'target_id':reserved['target_id'],'intent':'playback','operation':reserved['operation'],'binding':reserved,
                'goal':mission['goal'],'goal_contract':mission['goal_contract'],'plan':mission['plan'],'final_status':result['final_status'],
                'final_independent_verification':final,'created_at':now(),'receipt_security':'historical self-consistency only'}
            attempt['receipt_sha256']=self._hash(attempt)
            with self._path(mid,'.'+reserved['attempt_id']+'.receipt.json').open('x',encoding='utf-8') as output:json.dump(attempt,output,ensure_ascii=False,indent=2)
            return result
        finally:tick_lock.release()

    def get_plan_status(self, mid):
        return self.tick_playback_plan(mid)


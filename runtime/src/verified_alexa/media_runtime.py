"""Stable owned player, explicit requested state, actual executor operations."""
import json
import os
import uuid
import time
from pathlib import Path
from urllib.parse import urlsplit
from .cdp import BrowserConnection, local_url
from .media_catalog import get_clip

def state_root():
    return Path(os.getenv('VERIFIED_ALEXA_STATE_DIR', str(Path.cwd() / 'evidence' / 'live_state'))).resolve()

def atomic_save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    try:
        for attempt in range(8):
            try:
                tmp.replace(path)
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 7:
                    raise
                time.sleep(.02 * (attempt + 1))
    finally:
        tmp.unlink(missing_ok=True)

def config_path(root, target_id):
    import re
    if not re.fullmatch(r'media-[a-f0-9]{24}', target_id):
        raise ValueError('Invalid owned target')
    return Path(root) / 'targets' / (target_id + '.config.json')

def initialize_player(root, launch):
    """Initialize the single owned target ONCE, without injecting a failure."""
    root = Path(root)
    previous = root / 'launch.json'
    target = json.loads(previous.read_text(encoding='utf-8')).get('target_id') if previous.exists() else None
    target = target or 'media-' + uuid.uuid4().hex[:24]
    path = config_path(root, target)
    if not path.exists():
        atomic_save(path, {'route':'live', 'revision':1, 'content_id':'clip-a', 'desired_operation':'play'})
    launch['target_id'] = target
    launch['default_content_id'] = 'clip-a'
    launch['runtime_id'] = uuid.uuid4().hex
    launch['state_dir'] = str(root.resolve())
    return launch

class MediaExecutor:
    def __init__(self, root):
        self.root = Path(root)

    def bind(self, mission_id, operation='play', content_id='clip-a'):
        launch = json.loads((self.root / 'launch.json').read_text(encoding='utf-8'))
        if 'target_id' not in launch:
            raise ValueError('Owned stable target is not initialized')
        if operation not in ('play','pause','stop','check'):
            raise ValueError('Unsupported local operation')
        config = json.loads(config_path(self.root, launch['target_id']).read_text(encoding='utf-8'))
        selected = content_id or config['content_id']
        if operation in ('pause','stop') and selected != config['content_id']:
            raise ValueError('Requested content is not the current target content; no switch is authorized')
        clip = get_clip(selected, root=self.root)
        desired = config.get('desired_operation','play') if operation=='check' else operation
        if desired not in ('play','pause','stop'):
            raise ValueError('Invalid requested state in owned configuration')
        binding = {'target_id':launch['target_id'], 'browser_target_id':launch['browser_target_id'],
                'cdp_endpoint':launch['cdp_endpoint'], 'media_url':launch['ui_url']+'media/'+launch['target_id'],
                'mission_id':mission_id, 'intent':'playback', 'operation':operation,
                'desired_operation':desired, 'content_id':selected, 'content_sha256':clip['sha256'],
                'state_dir':str(self.root.resolve()), 'content_title':clip['title'],
                'duration_seconds':clip.get('duration',12), 'runtime_id':launch.get('runtime_id')}
        for key in ('player_backend_node_id','player_loader_id','player_document_url'):
            if key in launch: binding[key]=launch[key]
        return binding

    def resolve_binding(self, binding):
        """Resolve a stable target through the current owned launcher only.

        Reassociate the native element after a CDP document-loader change, never
        after same-document DOM replacement. Historical attempt bindings stay intact.
        """
        launch=json.loads((self.root/'launch.json').read_text(encoding='utf-8'))
        if launch.get('target_id')!=binding['target_id']: raise ValueError('Stable target mismatch')
        clip=get_clip(binding['content_id'],root=self.root)
        if clip['sha256']!=binding['content_sha256']: raise ValueError('Registered content changed')
        local_url(launch['cdp_endpoint']); local_url(launch['ui_url'])
        with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as browser:
            frame=browser.command('Page.getFrameTree')['frameTree']['frame']
            if urlsplit(frame['url'])._replace(query='',fragment='')!=urlsplit(launch['ui_url']):
                raise ValueError('Owned document URL mismatch')
            if not launch.get('player_backend_node_id') or launch.get('player_loader_id')!=frame.get('loaderId'):
                launch.update(browser.player_identity())
                atomic_save(self.root/'launch.json',launch)
        result={**binding,'browser_target_id':launch['browser_target_id'],'cdp_endpoint':launch['cdp_endpoint'],
                'media_url':launch['ui_url']+'media/'+launch['target_id'],'runtime_id':launch.get('runtime_id'),
                'state_dir':str(self.root.resolve()),'content_title':clip['title'],'duration_seconds':clip.get('duration',12)}
        for key in ('player_backend_node_id','player_loader_id','player_document_url'): result[key]=launch[key]
        return result

    def _guard(self,binding):
        path=self.root/'target-context.json'
        if not path.exists() or 'goal_revision' not in binding: return
        context=json.loads(path.read_text(encoding='utf-8'))
        if context.get('revision')!=binding['goal_revision'] or context.get('active_mission_id')!=binding['mission_id']:
            raise ValueError('SUPERSEDED_GOAL: player effect rejected')
        if context.get('revoked') and not (binding.get('revocation_stop') is True and binding['operation']=='stop'):
            raise ValueError('REVOKED_GOAL: player effect rejected')

    def _browser_action(self, binding, action):
        self._guard(binding)
        with BrowserConnection(binding['cdp_endpoint'],binding['browser_target_id']) as browser:
            frame=browser.command('Page.getFrameTree')['frameTree']['frame']['id']
            context=browser.command('Page.createIsolatedWorld',{'frameId':frame,'worldName':'verified-owned-executor',
                'grantUniveralAccess':False})['executionContextId']
            if not binding.get('player_backend_node_id'): raise ValueError('Pinned player identity unavailable')
            node=browser.command('DOM.resolveNode',{'backendNodeId':binding['player_backend_node_id'],
                'executionContextId':context})['object']['objectId']
            self._guard(binding)
            result=browser.command('Runtime.callFunctionOn',{'objectId':node,'functionDeclaration':'function(){return '+action+';}',
                'returnByValue':True,'awaitPromise':True})
            if result.get('exceptionDetails'): raise RuntimeError('Native player action failed')
            return result['result'].get('value')

    def load_player(self, binding):
        source = binding['media_url']+'?revision='+uuid.uuid4().hex
        start=max(0,float(binding.get('start_seconds',0))); end=binding.get('end_seconds')
        nonce=uuid.uuid4().hex
        boundary=''
        if end is not None:
            boundary="""const limit=%s;const tick=()=>{if(Element.prototype.getAttribute.call(v,'data-execution-nonce')!==token)return;
            const t=Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime').get.call(v);
            if(t>=limit-.02){HTMLMediaElement.prototype.pause.call(v);Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime').set.call(v,limit);return;}
            if(HTMLVideoElement.prototype.requestVideoFrameCallback)HTMLVideoElement.prototype.requestVideoFrameCallback.call(v,tick);};
            if(HTMLVideoElement.prototype.requestVideoFrameCallback)HTMLVideoElement.prototype.requestVideoFrameCallback.call(v,tick);
            v.addEventListener('timeupdate',()=>{if(Element.prototype.getAttribute.call(v,'data-execution-nonce')===token&&
            Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime').get.call(v)>=limit-.02){
            HTMLMediaElement.prototype.pause.call(v);Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime').set.call(v,limit);}});
            """ % json.dumps(float(end))
        expression = """(async()=>{const v=this,token=%s;
          Element.prototype.setAttribute.call(v,'data-target',%s);
          Element.prototype.setAttribute.call(v,'data-execution-nonce',token);
          Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'loop').set.call(v,%s);
          Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'src').set.call(v,%s);
          HTMLMediaElement.prototype.load.call(v);
          await new Promise(resolve=>{if(Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'readyState').get.call(v)>=1)return resolve();
          v.addEventListener('loadedmetadata',resolve,{once:true});v.addEventListener('error',resolve,{once:true});setTimeout(resolve,1800);});
          if(Element.prototype.getAttribute.call(v,'data-execution-nonce')!==token)return {accepted:false,reason:'superseded'};
          if(Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'readyState').get.call(v)>=1)
          Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime').set.call(v,%s);
          %s try{await HTMLMediaElement.prototype.play.call(v);}catch{}return {accepted:true};})()""" % (
              json.dumps(nonce),json.dumps(binding['target_id']),'false' if end is not None else 'true',json.dumps(source),json.dumps(start),boundary)
        return self._browser_action(binding,expression)

    def execute(self, binding):
        self._guard(binding)
        operation = binding['operation']
        if operation=='check':
            return {'status':'NO_ACTION','outcome_verified':False,'message':'Observation only; target was not changed.'}
        path = config_path(self.root,binding['target_id'])
        config = json.loads(path.read_text(encoding='utf-8'))
        if operation in ('pause','stop') and config['content_id']!=binding['content_id']:
            raise ValueError('Target content changed; refusing a different-content operation')
        config['desired_operation'] = operation
        if operation=='play':
            config['content_id'] = binding['content_id']
        config['revision'] = int(config.get('revision',0))+1
        self._guard(binding); atomic_save(path,config)
        if operation=='play':
            self.load_player(binding)
        else:
            position=max(0,float(binding.get('stop_position_seconds',0)))
            expression = """(()=>{const v=this;
              Element.prototype.setAttribute.call(v,'data-target',%s);
              Element.prototype.setAttribute.call(v,'data-execution-nonce',%s);
              HTMLMediaElement.prototype.pause.call(v);%s return {accepted:true};})()""" % (
                json.dumps(binding['target_id']),json.dumps(uuid.uuid4().hex),
                "if(Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'readyState').get.call(v)>=1)Object.getOwnPropertyDescriptor(HTMLMediaElement.prototype,'currentTime').set.call(v,"+json.dumps(position)+");" if operation=='stop' else '')
            self._browser_action(binding,expression)
        return {'status':'SUCCESS','executor':'native_owned_player_'+operation,
                'message':'Command accepted. Independent observation still decides completion.','outcome_verified':False}

    def restart(self, binding):
        return self.execute(binding)

    def seek_and_play(self,binding,start_seconds):
        return self.execute({**binding,'operation':'play','desired_operation':'play','start_seconds':float(start_seconds)})

    def stop_at_boundary(self,binding,end_seconds):
        return self.execute({**binding,'operation':'stop','desired_operation':'stop','stop_position_seconds':float(end_seconds)})

    def repair(self, binding):
        self._guard(binding)
        if binding.get('desired_operation',binding['operation'])!='play':
            raise ValueError('Playback repair cannot override a pause or stop goal')
        path = config_path(self.root,binding['target_id'])
        config = json.loads(path.read_text(encoding='utf-8'))
        config.update(route='live',content_id=binding['content_id'],desired_operation='play',revision=int(config.get('revision',0))+1)
        self._guard(binding); atomic_save(path,config)
        self.load_player(binding)
        return {'status':'APPLIED','action':'repair same real media route and reload requested content','outcome_verified':False}

    def drift(self, binding):
        self._guard(binding)
        path = config_path(self.root,binding['target_id'])
        config = json.loads(path.read_text(encoding='utf-8'))
        config.update(route='stale',revision=int(config.get('revision',0))+1)
        atomic_save(path,config)
        self.load_player(binding)
        return {'fault_injected':True,'target_id':binding['target_id'],
                'assistant_message':'The media route has failed. Ask for a fresh check.'}

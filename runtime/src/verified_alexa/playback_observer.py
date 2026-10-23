"""Fresh pinned native player, compositor screenshots and independent decoder.

OS/Chrome/CDP and packaged code remain trusted. Page-world DOM, CSS, getters
and claimed outcome fields are not completion authority.
"""
import base64
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlsplit
from filelock import FileLock
from .cdp import BrowserConnection, local_url
from .media_catalog import get_clip, decoder_run, MAX_MEDIA_BYTES

IMAGE_WIDTH, IMAGE_HEIGHT = 128, 72
SAMPLE_FUNCTION = """function(){const v=this,p=HTMLMediaElement.prototype,h=HTMLVideoElement.prototype;
const get=(proto,key)=>Object.getOwnPropertyDescriptor(proto,key).get.call(v);
const q=h.getVideoPlaybackQuality.call(v),width=get(h,'videoWidth'),height=get(h,'videoHeight');
const r=Element.prototype.getBoundingClientRect.call(v),all=Document.prototype.querySelectorAll.call(document,'#player');
let appearance=true,reason=[];
for(let e=v;e;e=e.parentElement){const s=getComputedStyle(e);if(s.display==='none'||s.visibility!=='visible'||Number(s.opacity)<.99||s.filter!=='none'||s.transform!=='none'){appearance=false;reason.push('hidden/filter/transform ancestor');}}
const viewport=r.width>=128&&r.height>=72&&r.left>=0&&r.top>=0&&r.right<=innerWidth&&r.bottom<=innerHeight;
const fit=getComputedStyle(v);let painted={x:r.x,y:r.y,width:r.width,height:r.height};
const matchingAspect=Math.abs(r.width/r.height-(width&&height?width/height:16/9))<.03;
let shape=matchingAspect||fit.objectFit==='fill';
if(width&&height&&(fit.objectFit==='contain'||fit.objectFit==='scale-down')&&fit.objectPosition==='50% 50%'){
const scale=Math.min(r.width/width,r.height/height,fit.objectFit==='scale-down'?1:Infinity);
painted={x:r.x+(r.width-width*scale)/2,y:r.y+(r.height-height*scale)/2,width:width*scale,height:height*scale};shape=true;}
let unobstructed=true;
for(const x of [.08,.25,.5,.75,.92])for(const y of [.08,.25,.5,.75,.90]){
if(Document.prototype.elementFromPoint.call(document,r.x+r.width*x,r.y+r.height*y)!==v)unobstructed=false;}
let rgb=null,frameHash=null,pixels=null,pixelError=null;
try{if(width>0&&height>0){const c=Document.prototype.createElement.call(document,'canvas');c.width=128;c.height=72;
const cx=HTMLCanvasElement.prototype.getContext.call(c,'2d',{willReadFrequently:true});
cx.imageSmoothingEnabled=true;cx.imageSmoothingQuality='high';
CanvasRenderingContext2D.prototype.drawImage.call(cx,v,0,0,128,72);
const data=CanvasRenderingContext2D.prototype.getImageData.call(cx,0,0,128,72).data;
let binary='',hash=2166136261;for(let i=0;i<data.length;i++){hash^=data[i];hash=Math.imul(hash,16777619);if(i%4!==3)binary+=String.fromCharCode(data[i]);}rgb=btoa(binary);frameHash=(hash>>>0).toString(16);
const c2=Document.prototype.createElement.call(document,'canvas');c2.width=640;c2.height=360;const x2=HTMLCanvasElement.prototype.getContext.call(c2,'2d');CanvasRenderingContext2D.prototype.drawImage.call(x2,v,0,0,640,360);
pixels=[32,96,160,224,288].map(x=>Array.from(CanvasRenderingContext2D.prototype.getImageData.call(x2,x,32,1,1).data).slice(0,3));
}}catch(e){pixelError=String(e);}
return {target_id:Element.prototype.getAttribute.call(v,'data-target')||'',current_src:get(p,'currentSrc'),requested_src:get(p,'src'),network_state:get(p,'networkState'),current_time:get(p,'currentTime'),
duration:Number.isFinite(get(p,'duration'))?get(p,'duration'):null,loop:get(p,'loop'),paused:get(p,'paused'),ended:get(p,'ended'),ready_state:get(p,'readyState'),
error:get(p,'error')?{code:get(p,'error').code}:null,width,height,total_frames:q.totalVideoFrames,dropped_frames:q.droppedVideoFrames,
pixel_samples:pixels,frame_hash:frameHash,pixel_error:pixelError,rgb,at_ms:Date.now(),native_realm:true,
element_identity:all.length===1&&all[0]===v&&v.isConnected&&v.ownerDocument===document,
presentation:{rect:painted,element_rect:{x:r.x,y:r.y,width:r.width,height:r.height},object_fit:fit.objectFit,appearance,viewport,shape,unobstructed,document_visible:document.visibilityState==='visible',reason}};}"""


def _masked(rgb):
    # Ignore rounded corners and native browser controls near the bottom.
    return bytes(rgb[(y*IMAGE_WIDTH+x)*3+c] for y in range(4,58) for x in range(6,122) for c in range(3))


def _difference(left, right):
    if left is None or right is None or len(left)!=IMAGE_WIDTH*IMAGE_HEIGHT*3 or len(right)!=len(left):
        return {'mean_absolute_error':None,'mismatch_fraction':None,'passed':False}
    a,b=_masked(left),_masked(right)
    delta=[abs(x-y) for x,y in zip(a,b)]
    return {'mean_absolute_error':sum(delta)/len(delta),'mismatch_fraction':sum(x>24 for x in delta)/len(delta)}


def _source_frame(clip, position):
    position=max(0,min(float(position),max(0,clip['duration']-.001)))
    positions=[position]
    if position>=clip['duration']-.01:
        # Container duration marks the endpoint after the last decoded frame.
        # A native stopped/ended player legitimately retains that final frame.
        positions.extend([max(0,clip['duration']-.1),max(0,clip['duration']-1)])
    for requested in positions:
        process=decoder_run(['-hide_banner','-loglevel','error','-ss',str(requested),'-i',str(clip['asset_path']),
            '-frames:v','1','-vf',f'scale={IMAGE_WIDTH}:{IMAGE_HEIGHT}','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],capture_output=True,timeout=5)
        if process.returncode==0 and len(process.stdout)==IMAGE_WIDTH*IMAGE_HEIGHT*3:return process.stdout
    raise RuntimeError('Independent source decoder did not produce a frame')


def _crop_filter(rectangle):
    return 'crop={}:{}:{}:{}'.format(max(1,round(rectangle['width'])),max(1,round(rectangle['height'])),
                                    max(0,round(rectangle['x'])),max(0,round(rectangle['y'])))


def _png_frame(raw,rectangle):
    process=decoder_run(['-hide_banner','-loglevel','error','-f','image2pipe','-i','pipe:0','-frames:v','1',
        '-vf',_crop_filter(rectangle)+f',scale={IMAGE_WIDTH}:{IMAGE_HEIGHT}','-pix_fmt','rgb24','-f','rawvideo','pipe:1'],input=raw,capture_output=True,timeout=5)
    if process.returncode or len(process.stdout)!=IMAGE_WIDTH*IMAGE_HEIGHT*3:
        raise RuntimeError('Compositor screenshot decoder did not produce a frame')
    return process.stdout


def _thumbnail(raw,rectangle):
    process=decoder_run(['-hide_banner','-loglevel','error','-f','image2pipe','-i','pipe:0','-frames:v','1',
        '-vf',_crop_filter(rectangle)+',scale=256:-1','-c:v','png','-f','image2pipe','pipe:1'],input=raw,capture_output=True,timeout=5)
    if process.returncode or not process.stdout:raise RuntimeError('Screenshot thumbnail unavailable')
    return 'data:image/png;base64,'+base64.b64encode(process.stdout).decode('ascii')


def _sample_view(browser,node,context,capture_lock):
    """Short serialized reads only; never resize/emulate the household screen."""
    with FileLock(str(capture_lock),timeout=4):
        sample=browser.call_on_node(node,SAMPLE_FUNCTION,context)
        if not sample:raise RuntimeError('Pinned native video is unavailable')
        rgb=base64.b64decode(sample.pop('rgb')) if sample.get('rgb') else None
        presentation_ok=all(sample['presentation'][key] for key in ('appearance','viewport','shape','unobstructed','document_visible'))
        png,after_rgb=None,None
        if presentation_ok:
            # A scaled/cropped CDP capture may temporarily emulate a different
            # viewport. Capture the unmodified viewport, crop off-browser.
            result=browser.command('Page.captureScreenshot',{'format':'png','fromSurface':True,'captureBeyondViewport':False})
            png=base64.b64decode(result['data'])
            after=browser.call_on_node(node,SAMPLE_FUNCTION,context)
            after_rgb=base64.b64decode(after['rgb']) if after.get('rgb') else None
            sample['element_identity']=sample['element_identity'] and after['element_identity']
            presentation_ok=presentation_ok and all(after['presentation'][key] for key in ('appearance','viewport','shape','unobstructed','document_visible'))
        return sample,rgb,png,after_rgb,bool(presentation_ok)


def _native_empty(sample):
    # Chrome may retain the last currentSrc after src is removed and load()
    # empties the resource. Requested source, NETWORK_EMPTY/HAVE_NOTHING and
    # zero decoded dimensions jointly describe that actual native state.
    return (not sample.get('requested_src') and sample.get('network_state')==0
        and sample.get('ready_state')==0 and sample.get('width')==0 and sample.get('height')==0)


def _settle_native_readiness(browser,node,context,maximum_wait=2.0):
    """Bounded independent reads; never load/play/seek to manufacture proof."""
    beginning=time.monotonic();observations=[]
    while True:
        sample=browser.call_on_node(node,SAMPLE_FUNCTION,context)
        elapsed=time.monotonic()-beginning
        observations.append({key:sample.get(key) for key in
            ('current_src','requested_src','network_state','paused','current_time','ready_state','width','height','error')})
        if _native_empty(sample):
            status='genuinely_empty';break
        if sample.get('error'):
            status='native_error';break
        if sample.get('ready_state',0)>=2 and sample.get('width',0)>0 and sample.get('height',0)>0:
            status='decoded_ready';break
        if elapsed>=maximum_wait:
            status='readiness_timeout';break
        time.sleep(.05)
    return {'status':status,'elapsed_seconds':elapsed,'maximum_wait_seconds':maximum_wait,'observations':observations}


def observe(binding,challenge):
    started=datetime.now(timezone.utc).isoformat()
    root=Path(binding.get('state_dir') or os.getenv('VERIFIED_ALEXA_STATE_DIR',Path.cwd()/'evidence'/'live_state')).resolve()
    clip=get_clip(binding['content_id'],root)
    operation=binding.get('desired_operation',binding.get('operation','play'))
    if operation not in ('play','pause','stop'):raise ValueError('Unknown required goal predicate')
    status,digest,byte_count,http_error=None,None,0,None
    try:
        with urlopen(Request(local_url(binding['media_url']),headers={'Cache-Control':'no-cache'}),timeout=3) as response:
            status=response.status;hasher=hashlib.sha256()
            for chunk in iter(lambda:response.read(65536),b''):
                byte_count+=len(chunk)
                if byte_count>MAX_MEDIA_BYTES:raise ValueError('Owned media exceeds verifier bound')
                hasher.update(chunk)
            digest=hasher.hexdigest()
    except HTTPError as error:status=error.code
    except OSError as error:http_error=str(error)
    frames=[];source_frames=[];screen_frames=[];samples=[];errors=[];thumbnail=None
    with BrowserConnection(binding['cdp_endpoint'],binding['browser_target_id']) as browser:
        browser.socket.settimeout(8)
        frame=browser.command('Page.getFrameTree')['frameTree']['frame']
        expected_node=binding.get('player_backend_node_id')
        loader_ok=bool(expected_node and frame['loaderId']==binding.get('player_loader_id') and frame['url']==binding.get('player_document_url'))
        if not loader_ok:raise RuntimeError('Bound document/player identity changed; trusted re-association is required')
        context=browser.isolated_context()
        readiness=_settle_native_readiness(browser,expected_node,context)
        capture_lock=root/('observer_capture_'+hashlib.sha256(binding['browser_target_id'].encode()).hexdigest()[:16]+'.lock')
        for index in range(2):
            if index:time.sleep(1.2)
            sample,rgb,png,after_rgb,presentation_ok=_sample_view(browser,expected_node,context,capture_lock)
            rectangle=sample['presentation']['rect']
            entry={'at_native_time':sample['current_time'],'presentation_geometry_passed':bool(presentation_ok)}
            screen,source=None,None
            # Invalid layouts are rejected without moving/resizing the real UI.
            if presentation_ok:
                try:
                    screen=_png_frame(png,rectangle)
                    directory=root/'observer_frames';directory.mkdir(parents=True,exist_ok=True)
                    filename=hashlib.sha256(challenge.encode()).hexdigest()[:20]+f'-{index}.png'
                    (directory/filename).write_bytes(png)
                    entry.update(relative_path='observer_frames/'+filename,sha256=hashlib.sha256(png).hexdigest())
                    thumbnail=_thumbnail(png,rectangle)
                    comparisons=[_difference(screen,ref) for ref in (rgb,after_rgb)]
                    comparison=min(comparisons,key=lambda value:value.get('mean_absolute_error') if value.get('mean_absolute_error') is not None else float('inf'))
                    entry['compositor_error']=comparison
                except Exception as error:errors.append(str(error))
            if rgb is not None:
                try:
                    source=_source_frame(clip,sample['current_time']);entry['reference_error']=_difference(rgb,source)
                except Exception as error:errors.append(str(error))
            # Raw RGB makes calibration/rejection independently reproducible.
            directory=root/'observer_frames';directory.mkdir(parents=True,exist_ok=True)
            stem=hashlib.sha256(challenge.encode()).hexdigest()[:20]+f'-{index}'
            for label,data in (('native',rgb),('source',source),('screen',screen)):
                if data is not None:(directory/(stem+'.'+label+'.rgb')).write_bytes(data)
            entry['compositor_passed']=bool(entry.get('compositor_error',{}).get('mean_absolute_error') is not None
                and entry['compositor_error']['mean_absolute_error']<=7 and entry['compositor_error']['mismatch_fraction']<=.10)
            entry['reference_passed']=bool(entry.get('reference_error',{}).get('mean_absolute_error') is not None
                and entry['reference_error']['mean_absolute_error']<=8 and entry['reference_error']['mismatch_fraction']<=.12)
            sample['presentation']['passed']=bool(presentation_ok)
            samples.append(sample);frames.append(entry);screen_frames.append(screen);source_frames.append(source)
    a,b=samples
    elapsed=b['current_time']-a['current_time']
    if elapsed<0 and b['loop'] and b['duration']:elapsed+=b['duration']
    rendered=(b['total_frames']-b['dropped_frames'])-(a['total_frames']-a['dropped_frames'])
    stop_position=float(binding.get('stop_position_seconds',0))
    if not math.isfinite(stop_position) or stop_position<0:raise ValueError('Invalid stopped position')
    empty_stopped=operation=='stop' and stop_position==0 and all(s['paused'] and _native_empty(s) and abs(s['current_time'])<=.05 for s in samples)
    target_ok=all(s['target_id']==binding['target_id'] and
        (urlsplit(s['current_src'])._replace(query='',fragment='')==urlsplit(binding['media_url']) or empty_stopped) for s in samples)
    element_ok=loader_ok and all(s['element_identity'] for s in samples)
    native_ok=all(s.get('native_realm') is True and isinstance(s['paused'],bool) and isinstance(s['current_time'],(int,float)) for s in samples)
    running=all(not s['paused'] and not s['ended'] and not s['error'] and s['ready_state']>=2 and s['width']>0 for s in samples)
    advancing=elapsed>=.4 and rendered>=12
    paused=all(s['paused'] for s in samples) and abs(b['current_time']-a['current_time'])<=.05 and abs(rendered)<=1
    stopped=paused and all(abs(s['current_time']-stop_position)<=.2 for s in samples)
    state_ok=running and advancing if operation=='play' else paused if operation=='pause' else stopped
    source_motion=_difference(*source_frames);screen_motion=_difference(*screen_frames)
    # Static source media is legitimate. Changing source with a frozen screen is not.
    motion_ok=operation!='play' or (source_motion.get('mean_absolute_error') is not None and screen_motion.get('mean_absolute_error') is not None
        and (source_motion['mean_absolute_error']<.2 or screen_motion['mean_absolute_error']>=min(.4,source_motion['mean_absolute_error']*.15)))
    # Native empty paused/reset state is useful diagnosis, but has no decoded
    # content bitmap to compare with the screen. It cannot earn visible DONE.
    reference_ok=all(entry['reference_passed'] for entry in frames)
    compositor_ok=all(entry['presentation_geometry_passed'] and entry['compositor_passed'] for entry in frames) and motion_ok
    stream_ok=status==200 and digest==binding['content_sha256'] and digest==clip['sha256'] and byte_count>0
    checks={'target_identity':bool(target_ok),'content_identity':bool(reference_ok),'native_player_observation':bool(native_ok),
            'goal_state':bool(state_ok),'stream_identity':bool(stream_ok or operation=='stop'),
            'element_identity':bool(element_ok),'visible_compositor':bool(compositor_ok),'reference_content':bool(reference_ok)}
    native_playing=bool(running and advancing)
    observed_state='stopped' if stopped else 'paused' if paused else 'playing' if native_playing else 'unknown'
    return {'passed':all(checks.values()),'binding':binding,'observed_at':datetime.now(timezone.utc).isoformat(),'started_at':started,
        'challenge':challenge,'probe_pid':os.getpid(),'checks':checks,'http_status':status,'http_sha256':digest,'http_bytes':byte_count,'http_error':http_error,
        'stream_health':bool(stream_ok),'samples':samples,'native_samples':samples,'rendered_frame_delta':rendered,'media_time_delta':elapsed,
        'pixel_identity':bool(reference_ok),'pixel_identity_scope':'independent decoder reference of entire registered source frame; no marker authority',
        'goal_operation':binding.get('operation','play'),'desired_operation':operation,'observed_state':observed_state,
        'playback_observed':bool(native_playing and compositor_ok and reference_ok),'native_playback_observed':native_playing,
        'presentation_frames':frames,'presentation_thumbnail':thumbnail,'source_motion':source_motion,'screen_motion':screen_motion,
        'presentation_errors':errors,'stop_position_seconds':stop_position,
        'readiness':readiness,'empty_native_stopped':bool(empty_stopped),'native_stop_observed':bool(stopped),
        'completion_limit':'Empty native player has no decoded content; visible stopped outcome is unverified' if empty_stopped else None,
        'observation_scope':'pinned native player + actual Chrome compositor + independently decoded registered source; fresh temporal window',
        'verifier':{'independent_process':True,'read_only':True,'source':'actual HTTP SHA, pinned isolated native DOM, Chrome compositor screenshot, independent FFmpeg decoder',
                    'independence_scope':'page-world getter/DOM/CSS independent; shared trusted OS/Chrome/CDP and packaged code, not device attestation'}}


def main():
    try:result=observe(json.loads(sys.argv[1]),sys.argv[2])
    except Exception as error:
        result={'passed':False,'binding':json.loads(sys.argv[1]),'observed_at':datetime.now(timezone.utc).isoformat(),
            'challenge':sys.argv[2],'probe_pid':os.getpid(),'error':str(error),'checks':{},
            'verifier':{'independent_process':True,'read_only':True,'source':'observer unavailable; no goal proof'}}
    print(json.dumps(result,allow_nan=False))


if __name__=='__main__':main()

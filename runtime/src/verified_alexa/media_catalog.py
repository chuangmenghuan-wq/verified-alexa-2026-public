"""Validated local media; registration hashes are checked at every observation."""
import hashlib
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from filelock import FileLock

ASSETS = Path(__file__).with_name('assets')
MAX_MEDIA_BYTES = 128_000_000
CATALOG = {
    'clip-a': {'title':'CLIP A — BLUE ORBIT','sha256':'5d327d4dba7020c31e10f8f50458aa2306b497241b87bda357f18072126480c5',
               'colors':[(32,224,96),(32,96,224),(224,64,32),(224,224,32),(192,64,224)]},
    'clip-b': {'title':'CLIP B — AMBER TRAIL','sha256':'f41fe3e1fd3998be3fc7fbd05865317994a067ea9585ad6ef3e59aa89d4ee40b',
               'colors':[(224,32,64),(224,192,64),(32,224,224),(224,64,192),(64,224,32)]},
}

def ffmpeg_path():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()

def decoder_run(args, **kwargs):
    return subprocess.run([ffmpeg_path(), *args], creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0), **kwargs)

def media_metadata(path):
    process=decoder_run(['-hide_banner','-i',str(path),'-map','0:v:0','-t','0','-f','null','-'],capture_output=True,timeout=15)
    text=process.stderr.decode('utf-8',errors='replace')
    duration=re.search(r'Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)',text)
    video=next((line for line in text.splitlines() if 'Video:' in line),'')
    dimensions=re.search(r'[, ](\d{2,5})x(\d{2,5})(?:[, \[])',video)
    if process.returncode or not duration or not dimensions:
        raise ValueError('The file is not a decodable local video with finite duration')
    seconds=int(duration[1])*3600+int(duration[2])*60+float(duration[3])
    width,height=int(dimensions[1]),int(dimensions[2])
    if not (0<seconds<=7200 and 16<=width<=4096 and 16<=height<=4096):
        raise ValueError('Local video exceeds supported duration/dimension bounds')
    return {'duration':seconds,'width':width,'height':height}

def _root(root=None):
    return Path(root or os.getenv('VERIFIED_ALEXA_STATE_DIR',Path.cwd()/'evidence'/'live_state')).resolve()

def _digest(path):
    if not 0<path.stat().st_size<=MAX_MEDIA_BYTES:
        raise ValueError('Local media must contain at most 128 MB')
    hasher=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):hasher.update(block)
    return hasher.hexdigest()

def _registry(root):
    path=_root(root)/'media'/'catalog.json'
    if not path.exists():return {}
    value=json.loads(path.read_text(encoding='utf-8'))
    if value.get('schema')!='owned-local-media/v1' or not isinstance(value.get('items'),dict):
        raise ValueError('Local media registry is invalid')
    return value['items']

def register_media(root,source,filename=None,title=None):
    """Copy a local path or bytes; return its content-addressed catalog record."""
    if isinstance(source,(bytes,bytearray,memoryview)):
        raw=bytes(source);name=filename or 'local.webm'
    else:
        path=Path(source).resolve()
        if not path.is_file() or path.stat().st_size>MAX_MEDIA_BYTES:
            raise ValueError('Choose a local WebM/MP4 file within 128 MB')
        raw=path.read_bytes();name=filename or path.name
    extension=Path(name).suffix.casefold()
    if extension not in ('.webm','.mp4') or not 0<len(raw)<=MAX_MEDIA_BYTES:
        raise ValueError('Choose a nonempty WebM/MP4 file within 128 MB')
    digest=hashlib.sha256(raw).hexdigest();content_id='media-'+digest[:24]
    media=_root(root)/'media';media.mkdir(parents=True,exist_ok=True)
    with FileLock(str(media/'catalog.lock'),timeout=5):
        target=media/(content_id+extension)
        if target.exists() and _digest(target)!=digest:
            raise ValueError('Registered content bytes changed; registration refused')
        if not target.exists():
            temporary=media/(uuid.uuid4().hex+extension);temporary.write_bytes(raw)
            try:
                metadata=media_metadata(temporary);temporary.replace(target)
            finally:temporary.unlink(missing_ok=True)
        else:metadata=media_metadata(target)
        record={'content_id':content_id,'filename':target.name,'sha256':digest,'title':str(title or Path(name).stem)[:120],**metadata,
                'scope':'user-selected local media; no semantic or license attestation'}
        items=_registry(root);prior=items.get(content_id)
        if prior and prior['sha256']!=digest:raise ValueError('Local content identity collision')
        items[content_id]=record;temporary=media/('catalog.'+uuid.uuid4().hex+'.tmp')
        temporary.write_text(json.dumps({'schema':'owned-local-media/v1','items':items},ensure_ascii=False,indent=2),encoding='utf-8')
        temporary.replace(media/'catalog.json')
    return get_clip(content_id,root)

def get_clip(content_id,root=None):
    packaged=dict(CATALOG);extra=ASSETS/'media_catalog.json'
    if extra.exists():packaged.update(json.loads(extra.read_text(encoding='utf-8')))
    if content_id in packaged:
        record=packaged[content_id];path=ASSETS/(record.get('filename') or content_id+'.webm')
        scope='original procedural CC0 demo media, not real-world footage'
    else:
        record=_registry(root).get(content_id)
        if not record or not re.fullmatch(r'media-[a-f0-9]{24}',str(content_id)):
            raise ValueError('Unknown local content: '+str(content_id))
        path=_root(root)/'media'/record['filename']
        if path.resolve().parent!=(_root(root)/'media').resolve():
            raise ValueError('Local media registry path is outside the owned directory')
        scope=record['scope']
    if not path.is_file():raise ValueError('Registered local media is unavailable')
    digest=_digest(path)
    if digest!=record['sha256']:raise ValueError('Local media digest differs from its registration')
    metadata={key:record[key] for key in ('duration','width','height') if key in record}
    if len(metadata)!=3:metadata=media_metadata(path)
    result={'content_id':content_id,'title':record['title'],'asset_path':path,'sha256':digest,**metadata,'scope':scope}
    # Legacy diagnostics, no longer completion authority.
    if 'colors' in record:
        result.update(marker_samples=[{'x':32+i*64,'y':32,'rgb':list(color)} for i,color in enumerate(record['colors'])],pixel_tolerance=36)
    return result

def list_clips(root=None):
    keys=list(CATALOG);extra=ASSETS/'media_catalog.json'
    if extra.exists():keys.extend(key for key in json.loads(extra.read_text(encoding='utf-8')) if key not in keys)
    keys.extend(key for key in _registry(root) if key not in keys)
    return [{key:record[key] for key in ('content_id','title','sha256','duration','width','height','scope')}
            for record in (get_clip(key,root) for key in keys)]

def catalog(root=None):return list_clips(root)

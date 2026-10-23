"""Single portable entry: owns local HTTP/MCP servers and a private Chrome player."""
import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import tempfile
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, unquote
from urllib.request import urlopen
from .media_runtime import atomic_save, config_path
from .mcp_client import MCPClient
from websocket import WebSocketException

ASSETS = Path(__file__).with_name('assets')

def chrome_path():
    candidates = [os.getenv('VERIFIED_ALEXA_BROWSER', '')]
    candidates += [shutil.which(name) or '' for name in ('google-chrome', 'chromium', 'chromium-browser', 'chrome', 'msedge')]
    if os.name == 'nt':
        for base in (os.getenv('PROGRAMFILES', ''), os.getenv('PROGRAMFILES(X86)', ''), os.getenv('LOCALAPPDATA', '')):
            candidates.extend(str(Path(base) / path) for path in ('Google/Chrome/Application/chrome.exe', 'Microsoft/Edge/Application/msedge.exe'))
    candidates += ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge']
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError('Chrome/Chromium/Edge is required. Set VERIFIED_ALEXA_BROWSER to its executable.')

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def make_handler(root, client, window_token=''):
    # Speech metadata only. The MCP authority owns every token/revision/revoke.
    voice_metadata = {}
    metadata_lock = threading.Lock()
    def voice_preview(recognized, request):
        from .intent import parse_request
        parsed = parse_request(recognized.get('text', ''))
        if recognized.get('low_confidence'):
            parsed={'operation':'unsupported','content_id':None,'original_text':recognized.get('text',''),
                    'reason':'Speech confidence too low; please type the intended request'}
        # A low-confidence waveform still takes the real preview path, with an
        # unsupported request. Confidence never grants execution authority.
        core = client.call('preview_request', {'goal': '' if recognized.get('low_confidence') else recognized.get('text',''),
            'mission_id':request.get('mission_id',''), 'new_mission':bool(request.get('new_mission',False))})
        token = core.get('preview_id')
        if token:
            with metadata_lock: voice_metadata[token] = recognized
        return {**core,'recognition':recognized,'parsed_request':core.get('parsed_request',parsed),
                'confirmation_id':token,'result':None}

    def confirmed(token, voice=False):
        result = client.call('confirm_request', {'preview_id':token})
        if result.get('error') or result.get('final_status') in ('PREVIEW_REJECTED','REJECTED','STALE_AUTHORIZATION','SUPERSEDED','CANCELLED'):
            raise ValueError(result.get('error') or result.get('reason') or 'Preview is no longer authorized. Review a new request.')
        with metadata_lock: recognized = voice_metadata.pop(token, {'text':result.get('recognized_text','Confirmed request'),'source':'MCP-bound confirmed request'})
        return {'recognition':recognized,'result':result,'confirmation_required':False} if voice else result
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def send_json(self, status, value):
            body = json.dumps(value, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            path = urlparse(self.path).path
            if path == '/':
                body = (ASSETS / 'index.html').read_bytes()
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            elif path == '/api/ready':
                self.send_json(200, {'ready': (root / 'launch.json').exists(), 'protocol': '2025-11-25', 'transport': 'Streamable HTTP'})
            elif path == '/api/player-binding':
                bound = bool(window_token) and self.headers.get('X-Verified-Player-Session') == window_token
                self.send_json(200, {'bound_player':bound, 'mode':'owned-player' if bound else 'remote-control',
                                    'scope':'Window association only; not an OS/browser-owner security boundary'})
            elif path in ('/assets/consumer.js','/assets/consumer.css'):
                body=(ASSETS/Path(path).name).read_bytes()
                self.send_response(200); self.send_header('Content-Type','text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body)))
                self.end_headers(); self.wfile.write(body)
            elif path in ('/api/test-audio','/api/plan-test-audio'):
                body=(ASSETS/('synthetic-plan-zh.wav' if path=='/api/plan-test-audio' else 'synthetic-fix-it-zh.wav')).read_bytes()
                self.send_response(200); self.send_header('Content-Type','audio/wav')
                self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(body)))
                self.end_headers(); self.wfile.write(body)
            elif path == '/api/catalog':
                catalog = client.call('list_media', {})
                self.send_json(200, {**catalog,'clips':catalog.get('media',catalog.get('clips',[]))})
            elif path == '/api/events':
                event_file = root / 'events.json'
                self.send_json(200, json.loads(event_file.read_text(encoding='utf-8')) if event_file.exists() else {'events': []})
            elif path == '/api/walkthrough-state':
                self.send_json(200, {'started': (root / 'START_WALKTHROUGH').exists()})
            elif path.startswith('/assets/') and path.endswith('.webm'):
                try:
                    from .media_catalog import get_clip
                    raw = get_clip(path.rsplit('/',1)[1].removesuffix('.webm'))['asset_path'].read_bytes()
                    self.send_response(200); self.send_header('Content-Type','video/webm'); self.send_header('Cache-Control','no-store')
                    self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
                except (KeyError,ValueError,FileNotFoundError): self.send_json(404,{'error':'unknown clip'})
            elif path.startswith('/media/'):
                try:
                    config = json.loads(config_path(root, path.removeprefix('/media/')).read_text(encoding='utf-8'))
                    if config.get('route') != 'live':
                        self.send_json(503, {'error': 'stale media route'}); return
                    from .media_catalog import get_clip
                    clip = get_clip(config['content_id'], root=root)
                    raw = clip['asset_path'].read_bytes()
                    start, end = 0, len(raw)-1
                    byte_range = self.headers.get('Range', '')
                    if byte_range:
                        import re
                        match = re.fullmatch(r'bytes=(\d+)-(\d*)', byte_range)
                        if not match:
                            self.send_json(416, {'error': 'unsupported range'}); return
                        start = int(match[1]); end = min(int(match[2]) if match[2] else end, end)
                        if start > end:
                            self.send_json(416, {'error': 'range out of bounds'}); return
                    body = raw[start:end+1]
                    self.send_response(206 if byte_range else 200)
                    self.send_header('Content-Type', clip.get('mime_type','video/mp4' if clip['asset_path'].suffix.lower()=='.mp4' else 'video/webm')); self.send_header('Accept-Ranges', 'bytes')
                    self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
                    if byte_range:
                        self.send_header('Content-Range', f'bytes {start}-{end}/{len(raw)}')
                    self.end_headers(); self.wfile.write(body)
                except (ValueError, KeyError, FileNotFoundError):
                    self.send_json(404, {'error': 'unknown owned media target'})
            else:
                self.send_json(404, {'error': 'not found'})
        def do_POST(self):
            try:
                if self.headers.get('Origin') and self.headers['Origin'] != 'http://' + self.headers.get('Host', ''):
                    self.send_json(403, {'error': 'invalid origin'}); return
                length = int(self.headers.get('Content-Length', '0'))
                if self.path == '/api/media/upload':
                    if length<=0 or length>32_000_000: raise ValueError('Local video must be 32 MB or smaller')
                    suffix = self.headers.get('X-Media-Extension','').lower()
                    if suffix not in ('.webm','.mp4'): raise ValueError('Only local WebM and MP4 files are accepted')
                    incoming = root/'uploads'; incoming.mkdir(parents=True,exist_ok=True)
                    path = incoming/(uuid.uuid4().hex+suffix)
                    path.write_bytes(self.rfile.read(length))
                    try:
                        result = client.call('import_local_media', {'staged_path':str(path),'title':unquote(self.headers.get('X-Media-Title','Local video'))[:100]})
                    finally: path.unlink(missing_ok=True)
                    self.send_json(200,result); return
                if length > 3_000_000: raise ValueError('Request too large')
                request = json.loads(self.rfile.read(length))
                if self.path == '/api/start-walkthrough':
                    (root / 'START_WALKTHROUGH').touch()
                    self.send_json(200, {'started': True}); return
                if self.path == '/api/ui-evidence':
                    atomic_save(root / 'UI_ACCEPTANCE.json', request)
                    self.send_json(200, {'recorded': True, 'scope': 'UI test assertions; not completion authority'}); return
                if self.path == '/api/voice':
                    from .voice_input import recognize_wave
                    recognized = recognize_wave(root, request['audio_base64'])
                    result = voice_preview(recognized, request)
                elif self.path == '/api/voice/text-preview':
                    result = voice_preview({'text':request.get('text',''),'source':'browser SpeechRecognition transcript; not offline waveform proof'}, request)
                elif self.path == '/api/voice/confirm':
                    result = confirmed(request.get('confirmation_id',''),voice=True)
                elif self.path in ('/api/voice/cancel','/api/preview/revoke'):
                    token = request.get('preview_id',request.get('confirmation_id',''))
                    result = client.call('revoke_request', {'preview_id':token})
                    with metadata_lock: voice_metadata.pop(token,None)
                elif self.path == '/api/preview':
                    result = client.call('preview_request', {'goal':request.get('goal',''),
                        'mission_id':request.get('mission_id',''),'new_mission':bool(request.get('new_mission',False)),
                        'plan':request.get('plan')})
                elif self.path == '/api/preview/confirm':
                    result = confirmed(request.get('preview_id',''))
                elif self.path == '/api/plan/preview':
                    arguments={key:request[key] for key in ('content_id','start_seconds','end_seconds','auto_repairs') if key in request}
                    if request.get('use_bookmark'):arguments['start_seconds']=-1
                    if 'auto_repair_limit' in request:arguments['auto_repairs']=request['auto_repair_limit']
                    plan = client.call('preview_playback_plan', arguments)
                    if plan.get('error') or not plan.get('plan'): result=plan
                    else: result=client.call('preview_request', {'plan':plan['plan'],'mission_id':request.get('mission_id','')})
                elif self.path == '/api/plan/status':
                    result = client.call('get_playback_plan_state', request)
                elif self.path == '/api/plan/control':
                    result = client.call('control_playback_plan', request)
                elif self.path == '/api/plan/fault':
                    result = client.call('inject_plan_fault', request)
                elif self.path == '/api/plan/cancel':
                    result = client.call('cancel_playback_plan', request)
                elif self.path == '/api/observe':
                    result = client.call('get_playback_progress', request)
                elif self.path == '/api/command':
                    atomic_save(root / 'events.json', {'events': [], 'mission_id': request.get('mission_id', '')})
                    result = client.call('execute_verified_goal', {'goal': request.get('goal', ''), 'mission_id': request.get('mission_id', ''),
                                                                  'new_mission':bool(request.get('new_mission',False))})
                elif self.path == '/api/recheck':
                    result = client.call('recheck_playback', {'mission_id': request['mission_id']})
                elif self.path == '/api/repair':
                    result = client.call('repair_playback', {'mission_id': request['mission_id']})
                elif self.path == '/api/drift':
                    result = client.call('inject_playback_fault', {'mission_id': request['mission_id']})
                elif self.path == '/api/receipt':
                    result = client.call('get_execution_receipt', {'run_id': request['mission_id'], 'attempt_id': request.get('attempt_id', '')})
                else:
                    self.send_json(404, {'error': 'not found'}); return
                self.send_json(200, result)
            except Exception as error:
                self.send_json(400, {'final_status': 'NOT_VERIFIED', 'error': str(error), 'assistant_message': 'I could not verify the player. No success is claimed.'})
    return Handler

def read_devtools_port(active, browser, retries=100, delay=.1):
    last_error = None
    for _ in range(retries):
        if browser.poll() is not None: raise RuntimeError('Owned browser exited')
        try:
            lines = active.read_text(encoding='utf-8').splitlines()
            if len(lines)>=2 and lines[0].isdigit() and 0<int(lines[0])<65536 and lines[1].startswith('/devtools/'):
                return int(lines[0])
        except (OSError,ValueError) as error: last_error=error
        time.sleep(delay)
    raise RuntimeError('Owned browser DevTools port was not readable before timeout') from last_error

def run_owned(root, port=8765, headless=False, walkthrough=False, initial_fault=False):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy(); env['VERIFIED_ALEXA_STATE_DIR'] = str(root)
    runtime_id = uuid.uuid4().hex
    env['VERIFIED_ALEXA_RUNTIME_ID'] = runtime_id
    env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1])
    mcp_port = free_port(); env['VERIFIED_ALEXA_MCP_PORT'] = str(mcp_port)
    log = (root / 'mcp-server.log').open('w', encoding='utf-8')
    server = subprocess.Popen([sys.executable, '-m', 'verified_alexa.mcp_server'], env=env, stdout=log, stderr=log)
    browser = None; http_server = None; profile = None; temporary_profile = False
    try:
        endpoint = f'http://127.0.0.1:{mcp_port}/mcp'
        client = None
        for _ in range(60):
            if server.poll() is not None:
                raise RuntimeError('MCP server exited; inspect mcp-server.log')
            try:
                client = MCPClient(endpoint, root / 'MCP_HTTP_TRANSCRIPT.json'); break
            except OSError:
                time.sleep(.15)
        if client is None:
            raise RuntimeError('MCP startup timed out')
        window_token = uuid.uuid4().hex
        http_server = ThreadingHTTPServer(('127.0.0.1', port), make_handler(root, client, window_token))
        threading.Thread(target=http_server.serve_forever, daemon=True).start()
        ui_url = f'http://127.0.0.1:{http_server.server_address[1]}/'
        browser_url = ui_url + ('?walkthrough=1' if walkthrough else '')
        profile = root / ('browser-profile-' + uuid.uuid4().hex[:10])
        # Chrome's profile databases still have Windows path-length limits even
        # when Python can read a deeply extracted project. This is a dedicated
        # disposable profile; durable goals/bookmarks remain in the owned root.
        if os.name == 'nt' and len(str(profile)) > 200:
            profile=Path(tempfile.mkdtemp(prefix='verified-owned-'))
            temporary_profile=True
            atomic_save(profile/'VERIFIED_ALEXA_OWNED.json',{'runtime_id':runtime_id,'state_dir':str(root)})
        args = [chrome_path(), '--user-data-dir=' + str(profile), '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                '--no-first-run', '--no-default-browser-check', '--autoplay-policy=no-user-gesture-required',
                '--window-size=1440,1000', '--force-device-scale-factor=1']
        if headless:
            args += ['--headless=new', '--hide-scrollbars', browser_url]
        else:
            args += ['--app=' + browser_url]
        browser = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        active = profile / 'DevToolsActivePort'
        cdp_endpoint = 'http://127.0.0.1:' + str(read_devtools_port(active,browser))
        page = None
        player_deadline=time.monotonic()+20
        for _ in range(80):
            if time.monotonic()>=player_deadline:break
            pages = json.load(urlopen(cdp_endpoint + '/json/list', timeout=2))
            page = next((p for p in pages if p['type'] == 'page' and p['url'].startswith(ui_url)), None)
            if page: break
            time.sleep(.1)
        if not page: raise RuntimeError('Owned player page not found')
        from .cdp import BrowserConnection
        player_ready = False
        player_deadline=time.monotonic()+20
        for _ in range(80):
            if time.monotonic()>=player_deadline:break
            try:
                pages = json.load(urlopen(cdp_endpoint + '/json/list', timeout=2))
                page = next(p for p in pages if p['type'] == 'page' and p['url'].startswith(ui_url))
                with BrowserConnection(cdp_endpoint, page['id']) as connection:
                    connection.socket.settimeout(1)
                    player_ready = connection.evaluate("Boolean(document.querySelector('#player'))")
                if player_ready: break
            except (OSError, RuntimeError, StopIteration, WebSocketException):
                pass
            time.sleep(.1)
        if not player_ready: raise RuntimeError('Owned player asset did not load')
        launch = {'ui_url': ui_url, 'mcp_url': endpoint, 'cdp_endpoint': cdp_endpoint, 'browser_target_id': page['id'],
                  'browser_pid': browser.pid, 'mcp_pid': server.pid, 'headless': headless,
                  'runtime_id':runtime_id,
                  'browser_profile':str(profile),'temporary_browser_profile':temporary_profile,
                  'scope': 'private demo-owned browser/profile; local test media; no production runtime'}
        from .media_runtime import initialize_player
        initialize_player(root,launch)
        if initial_fault:
            config_file = config_path(root,launch['target_id'])
            config = json.loads(config_file.read_text(encoding='utf-8')); config['route']='stale'; config['revision']+=1
            atomic_save(config_file,config)
        launch['initial_fault'] = initial_fault
        with BrowserConnection(cdp_endpoint,page['id']) as connection:
            # Startup navigation can invalidate DOM IDs between getDocument and
            # querySelector. Retry before publishing any target authority; never
            # use this path to bless a later replacement DOM node.
            identity = None
            identity_deadline=time.monotonic()+12
            for _ in range(30):
                if time.monotonic()>=identity_deadline:break
                try:
                    candidate=connection.player_identity()
                    if candidate['player_document_url'] != browser_url:
                        raise RuntimeError('Owned player document is not the launched UI')
                    identity=candidate; break
                except (RuntimeError,WebSocketException):
                    time.sleep(.1)
            if identity is None:raise RuntimeError('Native player identity did not stabilize before launch')
            launch.update(identity)
            atomic_save(root / 'launch.json', launch)
            atomic_save(root / 'RUNTIME_READY.json', {'runtime_id':runtime_id,'ready':True})
            connection.command('Page.enable')
            source = 'window.__verifiedOwnedSession='+json.dumps(window_token)+';window.name='+json.dumps('verified-owned-player:'+window_token)+';'
            connection.command('Page.addScriptToEvaluateOnNewDocument',{'source':source})
            connection.evaluate(source+'window.refreshOwnership?.();')
            # CDP's new-document script is session-associated; keep this owner session alive across reloads.
            yield launch
    finally:
        if http_server: http_server.shutdown(); http_server.server_close()
        if browser and browser.poll() is None:
            browser.terminate()
            try: browser.wait(timeout=5)
            except subprocess.TimeoutExpired: browser.kill(); browser.wait(timeout=5)
        server.terminate()
        try: server.wait(timeout=5)
        except subprocess.TimeoutExpired: server.kill(); server.wait(timeout=5)
        log.close()
        if temporary_profile and profile:
            try:
                resolved=profile.resolve();temporary_root=Path(tempfile.gettempdir()).resolve()
                marker=json.loads((resolved/'VERIFIED_ALEXA_OWNED.json').read_text(encoding='utf8'))
                # Never recurse outside this explicitly created owned temp dir.
                if resolved.parent==temporary_root and resolved.name.startswith('verified-owned-') and not profile.is_symlink() and not profile.is_junction() and marker['runtime_id']==runtime_id:
                    shutil.rmtree(resolved)
            except (OSError,ValueError,KeyError):
                pass  # Busy owned cache may remain; no verification claim rests on cleanup.

def main():
    parser = argparse.ArgumentParser(description='Verified Alexa local playback demo')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--state-dir', default=str(Path.cwd() / 'evidence' / 'live_state'))
    parser.add_argument('--headless', action='store_true', help='Smoke verification only; not a visible consumer demo')
    parser.add_argument('--walkthrough', action='store_true', help='Disclosed automated UI walkthrough using actual MCP calls')
    parser.add_argument('--initial-fault', action='store_true', help='Explicit demo-owned stale route at startup; never resets missions on repair')
    args = parser.parse_args()
    runtime = run_owned(args.state_dir, args.port, args.headless, args.walkthrough,args.initial_fault)
    try:
        launch = next(runtime)
        print(json.dumps(launch), flush=True)
        while not (Path(args.state_dir) / 'stop-owned-demo').exists():
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        runtime.close()

if __name__ == '__main__':
    main()

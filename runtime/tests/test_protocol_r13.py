"""Real protocol errors and restart, with fresh clients and raw HTTP capture."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from verified_alexa.mcp_client import MCPClient
from verified_alexa.media_runtime import atomic_save
from verified_alexa.simulator import run_owned


def test_protocol_errors_restart_and_fresh_process(tmp_path):
    app = run_owned(tmp_path, port=0, headless=True)
    replacement = None
    launch = next(app)
    errors = []
    def raw(body):
        request = Request(launch['mcp_url'], data=body,
                          headers={'Content-Type':'application/json',
                                   'Accept':'application/json, text/event-stream',
                                   'MCP-Protocol-Version':'2025-11-25'}, method='POST')
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            record = {'request':body.decode(), 'http_status':response.status,
                      'raw_response':response.read().decode()}
        errors.append(record)
        return record
    try:
        client = MCPClient(launch['mcp_url'], tmp_path/'PROTOCOL_TRANSCRIPT.json')
        assert client.entries[0]['response']['result']['protocolVersion'] == '2025-11-25'
        assert client.entries[1]['http_status'] == 202
        assert any(tool['name']=='execute_verified_goal' for tool in client.tools)
        malformed = raw(b'{bad-json')
        assert malformed['http_status'] >= 400 or 'error' in json.loads(malformed['raw_response'])
        unknown = raw(json.dumps({'jsonrpc':'2.0','id':90,'method':'tools/call',
                                 'params':{'name':'no_such_tool','arguments':{}}}).encode())
        unknown_result = json.loads(unknown['raw_response'])
        assert 'error' in unknown_result or unknown_result.get('result',{}).get('isError') is True
        first = client.call('execute_verified_goal', {'goal':'Play clip A'})
        assert first['final_status'] == 'VERIFIED_DONE'
        client.call('inject_playback_fault', {'mission_id':first['mission_id']})
        if os.name == 'nt':
            subprocess.run(['taskkill','/PID',str(launch['mcp_pid']),'/F'], capture_output=True, check=True)
        else:
            import signal
            os.kill(launch['mcp_pid'], signal.SIGTERM)
        env = os.environ.copy()
        env['VERIFIED_ALEXA_STATE_DIR'] = str(tmp_path)
        env['VERIFIED_ALEXA_MCP_PORT'] = launch['mcp_url'].split(':')[-1].split('/')[0]
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[1]/'src')
        with (tmp_path/'RESTART_SERVER.log').open('w', encoding='utf-8') as log:
            replacement = subprocess.Popen([sys.executable,'-m','verified_alexa.mcp_server'],
                                           env=env, stdout=log, stderr=log)
            fresh = None
            for _ in range(60):
                try:
                    fresh = MCPClient(launch['mcp_url'], tmp_path/'RESTART_TRANSCRIPT.json')
                    break
                except OSError:
                    time.sleep(.15)
            assert fresh is not None
            after = fresh.call('recheck_playback', {'mission_id':first['mission_id']})
            assert after['final_status'] == 'NOT_VERIFIED'
            assert after['evidence']['http_status'] == 503
            code = ('import json,sys;from verified_alexa.mcp_client import MCPClient;'
                    'print(json.dumps(MCPClient(sys.argv[1]).call("recheck_playback",'
                    '{"mission_id":sys.argv[2]})))')
            child = subprocess.run([sys.executable,'-c',code,launch['mcp_url'],first['mission_id']],
                                   env=env, capture_output=True, check=True, timeout=30)
            independent = json.loads(child.stdout)
            assert independent['final_status'] == 'NOT_VERIFIED'
            assert independent['evidence']['challenge'] != after['evidence']['challenge']
            atomic_save(tmp_path/'RESTART_ACCEPTANCE.json', {'original':first,'restarted':after,
                        'new_os_process':independent, 'http_errors':errors})
    finally:
        if replacement is not None:
            replacement.terminate()
            replacement.wait(timeout=10)
        app.close()

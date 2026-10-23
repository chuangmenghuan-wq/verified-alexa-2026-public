"""HTTP voice adapter delegates authorization to shared MCP tools.
Real expiry, supersession/revoke effects run in test_voice_authority_r14 and core tests.
"""
import json,threading
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from verified_alexa import simulator

class Client:
    def __init__(self):self.calls=[];self.fail_confirm=False
    def call(self,name,arguments):
        self.calls.append((name,arguments))
        if name=='preview_request':return {'preview_id':'shared-core-token','confirmation_required':True,
            'concrete_goal':{'operation':'play','content_id':'clip-a','target_id':'owned'},'expires_at':'2099-01-01T00:00:00+00:00'}
        if name=='confirm_request':
            if self.fail_confirm:return {'final_status':'PREVIEW_REJECTED','reason':'expired or stale'}
            return {'mission_id':'original','final_status':'NOT_VERIFIED'}
        if name=='revoke_request':return {'final_status':'REVOKED','preview_id':arguments['preview_id']}
        raise AssertionError('Unexpected authority bypass '+name)

def run_http(tmp_path,client):
    server=ThreadingHTTPServer(('127.0.0.1',0),simulator.make_handler(tmp_path,client))
    threading.Thread(target=server.serve_forever,daemon=True).start()
    def post(path,data):
        try:r=urlopen(Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'}))
        except HTTPError as e:r=e
        with r:return r.status,json.load(r)
    return server,post

def test_confirmation_forwards_only_server_token_and_original_recognition(tmp_path):
    client=Client();server,post=run_http(tmp_path,client)
    try:
        _,p=post('/api/voice/text-preview',{'text':'Yes, fix it','mission_id':'original','new_mission':True})
        assert client.calls==[('preview_request',{'goal':'Yes, fix it','mission_id':'original','new_mission':True})]
        status,r=post('/api/voice/confirm',{'confirmation_id':p['confirmation_id'],'mission_id':'attacker','text':'Play Clip B'})
        assert status==200 and r['result']['mission_id']=='original' and r['recognition']['text']=='Yes, fix it'
        assert client.calls[-1]==('confirm_request',{'preview_id':'shared-core-token'})
        _,r=post('/api/voice/cancel',{'confirmation_id':p['confirmation_id']})
        assert r['final_status']=='REVOKED' and client.calls[-1]==('revoke_request',{'preview_id':'shared-core-token'})
    finally:server.shutdown();server.server_close()

def test_core_expired_or_stale_result_cannot_be_published_as_success(tmp_path):
    client=Client();client.fail_confirm=True;server,post=run_http(tmp_path,client)
    try:
        _,p=post('/api/voice/text-preview',{'text':'Yes, fix it','mission_id':'original'})
        status,r=post('/api/voice/confirm',{'confirmation_id':p['confirmation_id']})
        assert status==400 and r['final_status']=='NOT_VERIFIED' and 'expired or stale' in r['error']
        assert client.calls[-1][0]=='confirm_request'
    finally:server.shutdown();server.server_close()

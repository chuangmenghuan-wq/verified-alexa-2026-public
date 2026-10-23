"""A saved mission must resolve the actual replacement owned runtime."""
import hashlib,json,time
import pytest
from verified_alexa.simulator import run_owned
from verified_alexa.mcp_client import MCPClient

@pytest.mark.parametrize('restart_delay',[0,.2,.7])
def test_same_mission_repair_after_entire_owned_restart(tmp_path,restart_delay):
    app=run_owned(tmp_path/'state',port=0,headless=True); before=next(app)
    try:
        client=MCPClient(before['mcp_url'],tmp_path/'BEFORE_MCP.json')
        original=client.call('execute_verified_goal',{'goal':'Play clip A'})
        assert original['final_status']=='VERIFIED_DONE', original
        mid=original['mission_id']
        historical=client.call('get_execution_receipt',{'run_id':mid})
    finally:app.close()
    time.sleep(restart_delay)
    app=run_owned(tmp_path/'state',port=0,headless=True); after=next(app)
    try:
        client=MCPClient(after['mcp_url'],tmp_path/'AFTER_MCP.json')
        fresh=client.call('recheck_playback',{'mission_id':mid})
        repaired=client.call('repair_playback',{'mission_id':mid})
        original_receipt=client.call('get_execution_receipt',{'run_id':mid,'attempt_id':original['attempt_id']})
        evidence={'before':before,'after':after,'original':original,'historical':historical,
                  'fresh':fresh,'repair':repaired,'historical_after':original_receipt,'restart_delay':restart_delay}
        (tmp_path/'RESTART_EVIDENCE.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        assert before['target_id']==after['target_id']
        assert before['browser_target_id']!=after['browser_target_id']
        assert repaired['final_status']=='VERIFIED_DONE', repaired
        assert repaired['mission_id']==mid and repaired['target_id']==original['target_id']
        assert repaired['attempt_id']!=original['attempt_id']
        assert repaired['binding']['browser_target_id']==after['browser_target_id']
        assert original_receipt['receipt']['receipt_sha256']==historical['receipt']['receipt_sha256']
        assert original_receipt['historical'] and original_receipt['current_status'] is None
    finally:app.close()

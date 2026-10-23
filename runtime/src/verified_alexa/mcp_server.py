"""Threaded MCP tools: short durable authority locks; observers never block Stop."""
import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from mcp.server.mcpserver import MCPServer
from .consumer_media_mission import LivingRoomPlaybackMission
from .goal_contract import parse_request
from .intent import rejection
from .operator import VerifiedOperator
from .media_runtime import atomic_save, state_root

mcp=MCPServer('Verified Alexa Bounded Local Playback')
missions=LivingRoomPlaybackMission()

def events_sink():
    events=[]
    def sink(event):
        events.append(event);atomic_save(state_root()/'events.json',{'events':events})
    return sink

@mcp.tool()
async def execute_verified_goal(goal:str,allow_recovery:bool=True,mission_id:str='',new_mission:bool=False)->dict:
    request=parse_request(goal)
    mid='' if new_mission and request['operation'] in ('play','pause','stop','check') else mission_id
    return await asyncio.to_thread(missions.request,mid,goal,allow_recovery,events_sink())

@mcp.tool()
async def preview_request(goal:str='',mission_id:str='',new_mission:bool=False,plan:dict|None=None)->dict:
    return await asyncio.to_thread(missions.preview_request,goal,mission_id,new_mission,plan)

@mcp.tool()
async def confirm_request(preview_id:str)->dict:
    return await asyncio.to_thread(missions.confirm_request,preview_id)

@mcp.tool()
async def revoke_request(preview_id:str)->dict:
    return await asyncio.to_thread(missions.revoke_request,preview_id)

@mcp.tool()
async def preview_playback_plan(content_id:str,start_seconds:float=-1,end_seconds:float=45,auto_repairs:int=1)->dict:
    return await asyncio.to_thread(missions.preview_playback_plan,content_id,start_seconds,end_seconds,auto_repairs)

@mcp.tool()
async def confirm_playback_plan(plan:dict,expected_revision:int)->dict:
    return await asyncio.to_thread(missions.confirm_playback_plan,plan,expected_revision)

@mcp.tool()
async def get_target_context()->dict:
    result=await asyncio.to_thread(missions.get_target_context)
    result['media']=(await list_media())['media']
    return result

@mcp.tool()
async def cancel_playback_plan(mission_id:str='',expected_revision:int|None=None,plan_id:str='')->dict:
    return await asyncio.to_thread(missions.cancel_playback_plan,mission_id or plan_id,expected_revision)

@mcp.tool()
async def resume_playback_plan(mission_id:str,expected_revision:int)->dict:
    return await asyncio.to_thread(missions.resume_playback_plan,mission_id,expected_revision)

@mcp.tool()
async def tick_playback_plan(mission_id:str)->dict:
    return await asyncio.to_thread(missions.tick_playback_plan,mission_id)

@mcp.tool()
async def get_plan_status(mission_id:str)->dict:
    return await asyncio.to_thread(missions.get_plan_status,mission_id)

@mcp.tool()
async def get_playback_plan_state(plan_id:str)->dict:
    return await asyncio.to_thread(missions.get_plan_status,plan_id)

@mcp.tool()
async def control_playback_plan(plan_id:str,action:str)->dict:
    if action.casefold() in ('cancel','stop'):
        return await asyncio.to_thread(missions.cancel_playback_plan,plan_id)
    if action.casefold()=='pause':
        return await asyncio.to_thread(missions.run,'Pause playback')
    if action.casefold() in ('resume','continue'):
        return await asyncio.to_thread(missions.preview_request,'Resume playback',plan_id)
    if action.casefold() in ('check','tick'):
        return await asyncio.to_thread(missions.tick_playback_plan,plan_id)
    return {'final_status':'UNSUPPORTED','accepted':False,'current_status':None}

@mcp.tool()
async def inject_plan_fault(plan_id:str)->dict:
    return await asyncio.to_thread(missions.inject_drift,plan_id)

@mcp.tool()
async def recheck_playback(mission_id:str)->dict:
    return await asyncio.to_thread(missions.resume,mission_id)

@mcp.tool()
async def repair_playback(mission_id:str)->dict:
    return await asyncio.to_thread(missions.repair,mission_id,events_sink())

@mcp.tool()
async def decline_repair(mission_id:str='')->dict:
    return await asyncio.to_thread(missions.decline,mission_id)

@mcp.tool()
async def inject_playback_fault(mission_id:str)->dict:
    return await asyncio.to_thread(missions.inject_drift,mission_id)

@mcp.tool()
async def get_execution_receipt(run_id:str,attempt_id:str='')->dict:
    try:receipt=await asyncio.to_thread(missions.get_receipt,run_id,attempt_id or None)
    except (ValueError,KeyError,FileNotFoundError):
        return {'found':False,'integrity_error':'HASH_OR_BINDING_MISMATCH','historical':True,'current_status':None}
    if receipt is None:receipt=await asyncio.to_thread(VerifiedOperator(receipt_dir=state_root()/'legacy-history').get_receipt,run_id)
    return {'found':receipt is not None,'receipt':receipt,'historical':True,'current_status':None,'use':'fresh_observation'}

@mcp.tool()
async def get_live_recovery_status(mission_id:str='')->dict:
    if not mission_id:return {'final_status':'UNAVAILABLE','current_status':None,'reason':'mission_id required; legacy backend disabled'}
    return await asyncio.to_thread(missions.resume,mission_id)

@mcp.tool()
async def list_media()->dict:
    from .media_catalog import list_clips
    return {'media':await asyncio.to_thread(list_clips,state_root()),'scope':'owned local media; no physical TV or content-rights attestation'}

@mcp.tool()
async def import_local_media(staged_path:str,title:str='')->dict:
    from .media_catalog import register_media
    staged=Path(staged_path).resolve();allowed=(state_root()/'uploads').resolve()
    if not staged.is_relative_to(allowed):raise ValueError('Upload must be staged under owned uploads directory')
    record=await asyncio.to_thread(register_media,state_root(),staged,None,title or None)
    return {'accepted':True,'media':{k:v for k,v in record.items() if k!='asset_path'},'current_status':None}

@mcp.tool()
async def get_playback_progress(mission_id:str='',plan_id:str='')->dict:
    result=await asyncio.to_thread(missions.resume,mission_id or plan_id)
    evidence=result.get('evidence') or result.get('final_independent_verification') or {}
    status=result['final_status']
    if status=='VERIFIED_DONE':
        operation=result.get('operation') or result.get('goal_contract',{}).get('operation')
        status='STOPPED_VERIFIED' if result.get('plan') or operation=='stop' else 'PAUSED_VERIFIED' if operation=='pause' else 'PLAYING_VERIFIED'
    observed=evidence.get('observed_at')
    valid_until=(datetime.fromisoformat(observed)+timedelta(seconds=2)).isoformat() if observed else None
    return {'mission_id':result.get('mission_id'),'plan_id':result.get('plan_id'),'progress':{
        'current_status':status if evidence else 'NOT_VERIFIED','observed_at':observed,'valid_until':valid_until,
        'position_seconds':missions._position(evidence),'presentation_thumbnail':evidence.get('presentation_thumbnail'),
        'summary':result.get('assistant_message'),'goal_revision':result.get('goal_revision')},'observation':evidence,'result':result}

@mcp.tool()
async def list_capabilities()->dict:
    return {'supported':['confirmed bounded media segment','persistent bookmark','one durable automatic repair','revocable goals','fresh observation'],
            'unsupported':['physical TV','native Alexa','generic Future Ability execution'], 'scope':'local prototype'}

def monitor_worker():
    while True:
        try:
            root=state_root();launch=json.loads((root/'launch.json').read_text(encoding='utf-8'))
            ready=json.loads((root/'RUNTIME_READY.json').read_text(encoding='utf-8'))
            runtime=os.getenv('VERIFIED_ALEXA_RUNTIME_ID')
            if not runtime or launch.get('runtime_id')!=runtime or ready.get('runtime_id')!=runtime:
                time.sleep(.25);continue
            context=missions.get_target_context();mid=context.get('active_mission_id')
            if mid and not context.get('revoked'):
                with missions.lock:mission=missions._read(mid)
                if mission.get('plan') and mission.get('final_status') not in ('STARTING','VERIFIED_DONE','MONITOR_EXPIRED','NEEDS_AUTHORIZATION'):
                    missions.tick_playback_plan(mid)
        except Exception as error:
            atomic_save(state_root()/'monitor-error.json',{'error':str(error),'at':datetime.now(timezone.utc).isoformat(),'verified':False})
        time.sleep(.25)

def main():
    threading.Thread(target=monitor_worker,daemon=True).start()
    mcp.run(transport='streamable-http',host='127.0.0.1',port=int(os.getenv('VERIFIED_ALEXA_MCP_PORT','8000')),
            streamable_http_path='/mcp',stateless_http=True,json_response=True)

if __name__=='__main__':main()

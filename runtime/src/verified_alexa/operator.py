"""Disabled legacy execution plus integrity-checked historical receipt reader.
Only the actual playback mission module can certify consumer completion.
"""
from pathlib import Path
from datetime import datetime,timezone
import json,uuid,hashlib
from .future_ability_adapter import FutureAbilityEdgeAdapter
from .media_runtime import atomic_save,state_root

class DemoAdapter(FutureAbilityEdgeAdapter):
    """Compatibility name only; the former synthetic completion path is disabled."""

def build_adapter(): return FutureAbilityEdgeAdapter()

def receipt_hash(receipt):
    core={k:v for k,v in receipt.items() if k!='receipt_sha256'}
    return hashlib.sha256(json.dumps(core,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class VerifiedOperator:
    def __init__(self,adapter=None,receipt_dir=None):
        self.adapter=adapter or build_adapter()
        self.receipt_dir=Path(receipt_dir or state_root()/'legacy-history')
        self.receipt_dir.mkdir(parents=True,exist_ok=True)
    def _receipt_path(self,run_id):
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.' for c in run_id) or run_id in ('.','..'):
            raise ValueError('RUN_ID_INVALID')
        return self.receipt_dir/(run_id+'.json')
    def execute_verified_goal(self,goal,allow_recovery=True,wait_seconds=0,poll_seconds=1):
        # Compatibility callers cannot reinstate GLOBAL/synthetic self-report completion.
        r={'run_id':str(uuid.uuid4()),'goal':goal,'final_status':'UNAVAILABLE',
           'backend':'LEGACY_EXECUTION_DISABLED','action':{'tool_status':'SAFE_HOLD'},
           'verification':{'verified':False,'available':False,'reason':'No mission-bound trusted live verifier'},
           'recovery_performed':False,'created_at':datetime.now(timezone.utc).isoformat()}
        r['receipt_sha256']=receipt_hash(r)
        atomic_save(self._receipt_path(r['run_id']),r)
        return r
    def get_receipt(self,run_id):
        p=self._receipt_path(run_id)
        if not p.exists():return None
        try:r=json.loads(p.read_text(encoding='utf8'))
        except (OSError,ValueError):return None
        if r.get('receipt_sha256')!=receipt_hash(r):return None
        return {**r,'historical':True,'current_status':None,'use':'fresh_live_verification_required'}
    def list_capabilities(self):
        return {'supported':['historical receipt read'],'execution':'UNAVAILABLE'}

"""Legacy backend is disabled: no live verifier is included for its upstream.
No production file is opened. PASS markers cannot certify a new customer task.
"""
class FutureAbilityEdgeAdapter:
    def __init__(self, status_path=None, runtime_root=None, binding_dir=None, controller_state_path=None):
        self._mission_binding = None
    def execute(self, goal):
        return {'tool_status': 'SAFE_HOLD', 'available': False, 'reason': 'LEGACY_BACKEND_NO_LIVE_VERIFIER', 'external_state_changed': False}
    def verify(self, goal):
        return {'verified': False, 'available': False, 'completion_scope': 'NONE', 'reason': 'LEGACY_BACKEND_NO_LIVE_VERIFIER', 'mission_binding': None}
    def recover(self, goal):
        return {'recovery_status': 'SAFE_HOLD', 'bounded': True, 'reason': 'LEGACY_BACKEND_NO_LIVE_VERIFIER'}
    def restore_binding(self, binding):
        self._mission_binding = None
    def isolation_readiness(self):
        return {'ready': False, 'reason': 'LEGACY_BACKEND_DISABLED'}

import json
from pathlib import Path

from verified_alexa.consumer_media_mission import LivingRoomPlaybackMission
from verified_alexa.future_ability_adapter import FutureAbilityEdgeAdapter
from verified_alexa.operator import VerifiedOperator


def test_wrong_goal_never_verifies_playback(tmp_path):
    result = LivingRoomPlaybackMission(tmp_path).run('Alexa, turn off the living room TV.')
    assert result['final_status'] != 'VERIFIED_DONE'


def test_old_global_success_cannot_complete_new_goal(tmp_path):
    status = tmp_path / 'status.json'
    status.write_text(json.dumps({'GOAL_STATE': 'VERIFIED_COMPLETE', 'observed_at_utc': '2020-01-01T00:00:00Z', 'LAST_VERIFIED_PROGRESS': {'gap_id': 'dependency-reuse--diagnosis-only', 'semantic_evidence': {'status': 'PASS'}}}))
    adapter = FutureAbilityEdgeAdapter(status_path=status, runtime_root=tmp_path, binding_dir=tmp_path / 'bindings')
    result = VerifiedOperator(adapter, tmp_path / 'receipts').execute_verified_goal('restore service', allow_recovery=False)
    assert result['final_status'] != 'VERIFIED_DONE'


def test_binding_cannot_verify_another_goal(tmp_path):
    status = tmp_path / 'status.json'
    status.write_text(json.dumps({'GOAL_STATE': 'OPEN', 'CURRENT_FRONTIER': 'latency--verified-close-acceptance', 'LAST_VERIFIED_PROGRESS': {'progress_identity': 'new', 'gap_id': 'latency--verified-close-acceptance', 'semantic_evidence': {'status': 'PASS'}}}))
    adapter = FutureAbilityEdgeAdapter(status_path=status, runtime_root=tmp_path, binding_dir=tmp_path / 'bindings')
    adapter.execute('fix latency')
    state = json.loads(status.read_text()); state['LAST_VERIFIED_PROGRESS']['progress_identity'] = 'newer'; status.write_text(json.dumps(state))
    assert adapter.verify('restore service')['verified'] is False


def test_finished_record_is_explicitly_historical(tmp_path):
    from verified_alexa.operator import DemoAdapter
    operator = VerifiedOperator(DemoAdapter(), tmp_path)
    receipt = operator.execute_verified_goal('restore service')
    record = operator.get_receipt(receipt['run_id'])
    assert record.get('historical') is True
    assert record.get('current_status', 'MISSING') is None

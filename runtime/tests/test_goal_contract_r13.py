"""R13 regressions written against the reviewed R12 completion contract first.

These isolate routing/mission persistence. Real media observation is covered by
the consumer integration suite; a unit stub never counts as playback evidence.
"""
import json

import pytest

from verified_alexa.intent import parse_intent
from verified_alexa.consumer_media_mission import LivingRoomPlaybackMission


class ContractExecutor:
    def __init__(self):
        self.actions = []
        self.bindings = []
        self.fail_execute = False

    def bind(self, mid, operation='play', content_id='clip-a'):
        operation = 'play' if operation == 'check' else operation
        binding = {'mission_id': mid, 'intent': 'playback', 'operation': operation,
                   'content_id': content_id or 'clip-a', 'content_sha256': 'a' * 64,
                   'target_id': 'media-' + 'a' * 24,
                   'browser_target_id': 'owned-player', 'cdp_endpoint': 'http://127.0.0.1:1',
                   'media_url': 'http://127.0.0.1:2/media/media-' + 'a' * 24}
        self.bindings.append(binding)
        return binding

    def execute(self, binding):
        self.actions.append(('execute', binding['operation'], binding['content_id']))
        if self.fail_execute:
            raise RuntimeError('Actual executor unavailable')
        return {'status': 'SUCCESS', 'outcome_verified': False}

    def restart(self, binding):
        return self.execute(binding)

    def repair(self, binding):
        self.actions.append(('repair', binding['operation'], binding['content_id']))
        return {'status': 'APPLIED', 'outcome_verified': False}


@pytest.fixture
def contract_mission(tmp_path):
    mission = LivingRoomPlaybackMission(tmp_path)
    mission.executor = ContractExecutor()
    # Unit test seam for routing only. No result here establishes media PASS.
    mission._probe = lambda binding: {'passed': True, 'observed_at': '2026-10-07T00:00:00+00:00',
                                      'binding': binding, 'checks': {'unit_stub': True}}
    return mission


def test_game_goal_is_not_a_supported_calibration_goal():
    assert parse_intent('Make sure the game is playing on the living room TV.') == 'unsupported'


@pytest.mark.parametrize('text,expected', [('Alexa, is it still playing?', 'recheck'),
                                         ('Alexa, yes, fix it.', 'repair')])
def test_wake_word_is_removed_before_followup_routing(text, expected):
    assert parse_intent(text) == expected


@pytest.mark.parametrize('text', ['Play clip A', 'Play clip B', 'Pause playback', 'Stop playback'])
def test_supported_state_change_has_an_explicit_goal(text):
    assert parse_intent(text) == 'playback'


def test_check_only_never_restarts_or_repairs(contract_mission):
    result = contract_mission.run('Check living room playback')
    assert result['final_status'] == 'VERIFIED_DONE'
    assert contract_mission.executor.actions == []


def test_false_never_passed_recheck_does_not_invent_history(contract_mission):
    contract_mission._probe = lambda binding: {'passed': False, 'observed_at': '2026-10-07T00:00:00+00:00',
                                              'binding': binding, 'checks': {'actual_player_running': False}}
    first = contract_mission.run('Check living room playback', allow_recovery=False)
    result = contract_mission.resume(first['mission_id'])
    assert result['final_status'] == 'NOT_VERIFIED'
    assert 'was playing earlier' not in result['assistant_message']


def test_existing_mission_rejects_different_content_goal(contract_mission):
    first = contract_mission.run('Play clip A', mission_id='original')
    before = list(contract_mission.executor.actions)
    conflict = contract_mission.run('Play clip B', mission_id='original')
    assert conflict['final_status'] == 'GOAL_CONFLICT'
    assert contract_mission.executor.actions == before
    assert contract_mission.get_receipt(first['mission_id'])['goal'] == 'Play clip A'


@pytest.mark.parametrize('text,operation', [('Pause playback', 'pause'), ('Stop playback', 'stop')])
def test_repair_keeps_nonplaying_goal(contract_mission, text, operation):
    original = contract_mission.run(text)
    repaired = contract_mission.repair(original['mission_id'])
    assert repaired['mission_id'] == original['mission_id']
    assert repaired['target_id'] == original['target_id']
    assert repaired['binding']['operation'] == operation
    assert contract_mission.executor.actions[-1][1] == operation


def test_decline_never_mutates_original_mission(contract_mission):
    original = contract_mission.run('Play clip A')
    before = list(contract_mission.executor.actions)
    result = contract_mission.run('No, do not repair it', mission_id=original['mission_id'])
    assert result['final_status'] == 'DECLINED'
    assert contract_mission.executor.actions == before


def test_failed_executor_attempts_are_counted_and_bounded(contract_mission):
    contract_mission.executor.fail_execute = True
    first = contract_mission.run('Play clip A')
    assert first['final_status'] == 'NOT_VERIFIED'
    mid = first['mission_id']
    for _ in range(2):
        assert contract_mission.repair(mid)['final_status'] == 'NOT_VERIFIED'
    result = contract_mission.repair(mid)
    assert result['final_status'] == 'NOT_VERIFIED'
    assert result['reason'] == 'ATTEMPT_LIMIT_REACHED'
    assert len(contract_mission.executor.actions) == 3
    assert len(contract_mission._read(mid)['attempts']) == 3


def test_receipt_read_never_returns_current_status(contract_mission):
    receipt = contract_mission.run('Play clip A')
    saved = contract_mission.get_receipt(receipt['mission_id'])
    assert saved['historical'] is True and saved['current_status'] is None
    assert json.loads(contract_mission._path(receipt['mission_id'], '.' + receipt['attempt_id'] + '.receipt.json').read_text(encoding='utf-8'))['goal'] == 'Play clip A'

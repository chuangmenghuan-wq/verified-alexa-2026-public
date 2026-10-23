import pytest
from verified_alexa.intent import parse_intent
@pytest.mark.parametrize('text',['turn off the living room TV','stop playing in the living room','buy a TV','play on the bedroom TV','turn on living room TV and mute it','Make sure the game is playing on the living room TV.'])
def test_unsupported(text): assert parse_intent(text)=='unsupported'
@pytest.mark.parametrize('text',['幫我確認客廳播放','Alexa, check living room playback.','Play clip A','Play clip B','Pause living room playback','Stop playback'])
def test_supported(text): assert parse_intent(text)=='playback'
def test_negated_goal_is_declined_without_playback():
    assert parse_intent('do not play the game')=='decline'
def test_followup_clarifies():
    from verified_alexa.mcp_server import execute_verified_goal
    import asyncio
    assert asyncio.run(execute_verified_goal('Yes, fix it.'))['final_status']=='NEEDS_CLARIFICATION'
def test_named_clip_requests_are_concrete():
    from verified_alexa.intent import parse_request
    assert parse_request('Alexa, play Blue Orbit.')['content_id'] == 'clip-a'
    assert parse_request('Please play Amber Trail')['content_id'] == 'clip-b'

"""Compatibility intent API over the explicit R13 local-player contract."""
from .goal_contract import parse_request

def parse_intent(text):
    operation = parse_request(text)['operation']
    return 'playback' if operation in ('play', 'pause', 'stop', 'check') else operation

def rejection(text):
    request = parse_request(text)
    clarify = request.get('reason') == 'CONTENT_REQUIRED'
    return {'final_status': 'NEEDS_CLARIFICATION' if clarify else 'UNSUPPORTED', 'accepted': False,
            'goal': text, 'request': request, 'current_status': None,
            'assistant_message': 'Which local clip should I play: clip A or clip B?' if clarify else
            'I can play clip A or B, pause, stop, check, or repair this local player. '
            'I cannot verify a sport broadcast or control a physical TV.'}

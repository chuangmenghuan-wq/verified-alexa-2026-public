"""Controlled requests map to explicit, observable local-player predicates.

No request for a sport/game or a physical TV is silently downgraded to merely
observing an arbitrary calibration video. Followups keep the saved predicate.
"""
import re
import math

GOAL_OPERATIONS = frozenset(('play', 'pause', 'stop'))
CONTENT_IDS = frozenset(('clip-a', 'clip-b'))
REQUIRED_OBSERVER_CHECKS = frozenset(('target_identity', 'content_identity',
                                    'native_player_observation', 'goal_state', 'stream_identity',
                                    'element_identity', 'visible_compositor', 'reference_content'))


def normalized_text(text):
    value = re.sub(r'[\s,.!?，。！？]+', ' ', str(text).casefold()).strip()
    return re.sub(r'^alexa\s+', '', value)


def parse_request(text):
    original = text if isinstance(text, str) else str(text)
    value = normalized_text(original)
    request = {'operation': 'unsupported', 'content_id': None, 'original_text': original}
    segment = re.fullmatch(r'play long journey from (bookmark|[0-9]+(?:\.[0-9]+)? seconds) until ([0-9]+(?:\.[0-9]+)?) seconds repair once then ask me', value)
    if segment or value == '從上次位置播放 到45秒停止 中斷先修一次否則問我':
        return {**request, 'operation':'plan', 'content_id':'clip-long',
                'start_seconds':-1 if not segment or segment[1]=='bookmark' else float(segment[1].split()[0]),
                'end_seconds':float(segment[2]) if segment else 45., 'auto_repairs':1}
    if value in ('cancel', '取消', '取消播放約定', 'cancel playback', 'cancel plan', 'cancel the plan', 'cancel playback plan'):
        return {**request, 'operation': 'cancel'}
    if value in ('resume playback', 'resume the plan', 'resume playback plan'):
        return {**request, 'operation': 'resume'}
    if re.search(r"(?:\bdo not\b|\bdon['’]t\b|\bnever\b|不要|不用|別修|別播)", value) or value in (
            'no', 'no thanks', 'no thank you', 'not now', 'cancel', '算了', '取消', '先不要', '不了'):
        return {**request, 'operation': 'decline'}
    if value in ('yes', 'yes fix it', 'yes please', 'fix it', 'repair it', 'repair playback',
                 '好', '好 修它', '好修它', '好 修好它', '修它', '好 幫我修好'):
        return {**request, 'operation': 'repair'}
    if value in ('is it still playing', 'is it playing now', 'check again', 'ask again', 'recheck',
                 '現在還在播嗎', '現在還在播放嗎', '再確認一次', '現在還在播', '還在播嗎'):
        return {**request, 'operation': 'recheck'}
    if value in ('check playback', 'check the player', 'check living room playback',
                 'check the living room playback', 'check living room tv playback',
                 'please check living room playback', '幫我確認客廳播放', '確認客廳播放',
                 '幫我確認客廳正在播放', '確認客廳電視播放', '確認播放', '檢查播放'):
        return {**request, 'operation': 'check'}
    if value in ('pause', 'pause playback', 'pause the video', 'pause the player',
                 'pause living room playback', 'pause the living room video', '暫停', '暫停播放',
                 '暫停影片', '幫我暫停播放'):
        return {**request, 'operation': 'pause'}
    if value in ('stop', 'stop playback', 'stop the video', 'stop the player', 'stop living room playback',
                 'stop the living room video', '停止', '停止播放', '停止影片', '幫我停止播放'):
        return {**request, 'operation': 'stop'}
    for content_id, letter in (('clip-a', 'a'), ('clip-b', 'b')):
        name = 'blue orbit' if content_id == 'clip-a' else 'amber trail'
        if value in (f'play {name}', f'please play {name}', f'播放 {name}',
                     f'play clip {letter}', f'play clip-{letter}', f'play clip{letter}',
                     f'play the clip {letter}', f'please play clip {letter}',
                     f'play clip {letter} in the living room', f'play clip {letter} on the local player',
                     f'播放影片{letter}', f'播放影片 {letter}', f'播放片段{letter}', f'播放片段 {letter}',
                     f'播放 clip {letter}', f'播放clip{letter}', f'播放 clip-{letter}'):
            return {**request, 'operation': 'play', 'content_id': content_id}
    if value in ('play', 'play a video', 'play the video', 'play a clip', '開始播放', '播放影片', '播放'):
        return {**request, 'reason': 'CONTENT_REQUIRED'}
    return {**request, 'reason': 'UNSUPPORTED_GOAL'}


def canonical_goal(binding):
    operation, content_id = binding.get('operation'), binding.get('content_id')
    if operation == 'check':
        operation = binding.get('desired_operation')
    digest = binding.get('content_sha256', '')
    valid_content = isinstance(content_id, str) and re.fullmatch(r'(?:clip-[a-z0-9-]+|media-[a-f0-9]{24})', content_id)
    if operation not in GOAL_OPERATIONS or not valid_content or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
        raise ValueError('Goal requires supported operation, content identity and SHA-256')
    return {'operation': operation, 'content_id': content_id, 'content_sha256': digest}


def request_matches_binding(request, binding):
    goal = canonical_goal(binding)
    return request['operation'] == goal['operation'] and (
        request.get('content_id') is None or request['content_id'] == goal['content_id'])


def goal_description(binding):
    goal = canonical_goal(binding)
    clip = binding.get('content_title') or {'clip-a':'Blue Orbit (clip A)', 'clip-b':'Amber Trail (clip B)'}.get(goal['content_id'], goal['content_id'])
    return {'play': clip + ' is playing', 'pause': clip + ' is paused', 'stop': 'the local player is stopped'}[goal['operation']]


def canonical_plan(plan):
    """Immutable media-time segment contract; no wall-clock completion shortcut."""
    if not isinstance(plan, dict):
        raise ValueError('Playback plan must be an object')
    required = ('target_id','content_id','content_sha256','start_seconds','end_seconds','auto_repairs')
    if any(key not in plan for key in required):
        raise ValueError('Playback plan is incomplete')
    canonical_goal({'operation':'play', **{key:plan[key] for key in ('content_id','content_sha256')}})
    start, end = plan['start_seconds'], plan['end_seconds']
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end)) or not 0 <= start < end <= 120:
        raise ValueError('Require finite media timestamps 0 <= start < end <= 120')
    if type(plan['auto_repairs']) is not int or plan['auto_repairs'] not in (0,1):
        raise ValueError('Automatic repair allowance must be zero or one')
    if not re.fullmatch(r'media-[a-f0-9]{24}', str(plan['target_id'])):
        raise ValueError('Playback plan needs an owned stable target')
    return {'schema':'verified-alexa-media-segment/v1','operation':'play_until',
            'target_id':plan['target_id'],'content_id':plan['content_id'],
            'content_sha256':plan['content_sha256'],'start_seconds':float(start),
            'end_seconds':float(end),'auto_repairs':plan['auto_repairs'],
            'monitor_seconds':120,'time_basis':'absolute media currentTime'}

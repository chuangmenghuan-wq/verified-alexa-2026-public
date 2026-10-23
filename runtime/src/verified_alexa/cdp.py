"""Bounded access to a browser profile owned exclusively by this demo."""
import json
import itertools
import uuid
from urllib.request import urlopen
from urllib.parse import urlparse
import websocket

def local_url(url):
    u = urlparse(url)
    if u.scheme not in ('http', 'ws') or u.hostname != '127.0.0.1':
        raise ValueError('Only the owned loopback browser is permitted')
    return url

class BrowserConnection:
    def __init__(self, endpoint, target_id):
        listing = json.load(urlopen(local_url(endpoint + '/json/list'), timeout=3))
        page = next((p for p in listing if p['id'] == target_id and p['type'] == 'page'), None)
        if page is None:
            raise RuntimeError('Bound player page is unavailable')
        self.socket = websocket.create_connection(local_url(page['webSocketDebuggerUrl']), timeout=4, suppress_origin=True)
        self.ids = itertools.count(1)
    def command(self, method, params=None):
        ident = next(self.ids)
        self.socket.send(json.dumps({'id': ident, 'method': method, 'params': params or {}}))
        while True:
            reply = json.loads(self.socket.recv())
            if reply.get('id') != ident:
                continue
            if 'error' in reply:
                raise RuntimeError(str(reply['error']))
            return reply.get('result', {})
    def evaluate(self, expression, context_id=None):
        params = {'expression': expression, 'returnByValue': True, 'awaitPromise': True}
        if context_id is not None:
            params['contextId'] = context_id
        result = self.command('Runtime.evaluate', params)
        if result.get('exceptionDetails'):
            raise RuntimeError('Player evaluation failed')
        return result['result'].get('value')
    def isolated_context(self):
        """Fresh realm defeats page-world getters, not OS/Chrome/CDP compromise."""
        frame = self.command('Page.getFrameTree')['frameTree']['frame']['id']
        result = self.command('Page.createIsolatedWorld', {
            'frameId': frame, 'worldName': 'verified-observer-' + uuid.uuid4().hex,
            'grantUniveralAccess': False,
        })
        return result['executionContextId']
    def evaluate_native(self, expression):
        return self.evaluate(expression, self.isolated_context())
    def player_identity(self):
        """Trusted launch association; callers must never refresh on DOM swaps."""
        frame = self.command('Page.getFrameTree')['frameTree']['frame']
        root = self.command('DOM.getDocument', {'depth':0})['root']['nodeId']
        nodes = self.command('DOM.querySelectorAll', {'nodeId':root,'selector':'#player'})['nodeIds']
        if len(nodes) != 1:
            raise RuntimeError('The owned document must have exactly one player')
        node = self.command('DOM.describeNode', {'nodeId':nodes[0]})['node']
        if node['nodeName'] != 'VIDEO':
            raise RuntimeError('The owned player must be a native video element')
        return {'player_backend_node_id':node['backendNodeId'],
                'player_loader_id':frame['loaderId'], 'player_document_url':frame['url']}
    def call_on_node(self, backend_node_id, function, context_id):
        remote = self.command('DOM.resolveNode', {'backendNodeId':int(backend_node_id),
                                                  'executionContextId':context_id})['object']
        result = self.command('Runtime.callFunctionOn', {'objectId':remote['objectId'],
            'functionDeclaration':function, 'returnByValue':True, 'awaitPromise':True})
        if result.get('exceptionDetails'):
            raise RuntimeError('Pinned native player observation failed')
        return result['result'].get('value')
    def close(self):
        self.socket.close()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()

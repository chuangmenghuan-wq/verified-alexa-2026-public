"""Standard JSON-RPC MCP 2025-11-25 client; every call is actual HTTP.
Initialize -> initialized -> tools/list precedes tools/call. JSON responses are
valid Streamable HTTP. This server uses JSON mode, so SSE is not negotiated here.
"""
import json
import threading
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from .media_runtime import atomic_save

class MCPClient:
    def __init__(self, url, transcript_path=None):
        self.url = url
        self.path = transcript_path
        self.entries = []
        self.counter = 0
        self.lock = threading.RLock()
        self.protocol = '2025-11-25'
        init = self.request('initialize', {'protocolVersion': self.protocol, 'capabilities': {}, 'clientInfo': {'name': 'Verified Alexa consumer client', 'version': '0.4.0'}})
        if init['result']['protocolVersion'] != self.protocol:
            raise ValueError('Unexpected negotiated protocol')
        self.request('notifications/initialized', notification=True)
        self.tools = self.request('tools/list', {})['result']['tools']

    def request(self, method, params=None, notification=False):
        with self.lock:
            body = {'jsonrpc': '2.0', 'method': method}
            if not notification:
                self.counter += 1
                body['id'] = self.counter
        # A long observer request must not hold up a later Stop/Cancel request.
        # Lock only identity allocation and transcript writes, never HTTP I/O.
        if params is not None:
            body['params'] = params
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': self.protocol}
        req = Request(self.url, data=json.dumps(body).encode(), headers=headers, method='POST')
        started_at = datetime.now(timezone.utc).isoformat()
        with urlopen(req, timeout=45) as response:
            raw = response.read().decode()
            status = response.status
            result = json.loads(raw) if raw else None
            record = {'started_at': started_at, 'received_at': datetime.now(timezone.utc).isoformat(), 'request': body, 'request_headers': headers, 'http_status': status, 'response_headers': dict(response.headers), 'raw_response': raw, 'response': result}
        if result and (result.get('jsonrpc') != '2.0' or result.get('id') != body.get('id')):
            raise ValueError('MCP response identity mismatch')
        if notification and (status != 202 or raw):
            raise ValueError('Invalid notification acknowledgement')
        with self.lock:
            self.entries.append(record)
            self.entries.sort(key=lambda entry: entry['started_at'])
            if self.path:
                atomic_save(self.path, {'protocol': self.protocol, 'transport': 'Streamable HTTP', 'entries': self.entries})
        if result and 'error' in result:
            raise RuntimeError(str(result['error']))
        return result

    def call(self, name, arguments):
        response = self.request('tools/call', {'name': name, 'arguments': arguments})
        value = response['result']
        if value.get('isError'):
            raise RuntimeError(str(value['content']))
        structured = value.get('structuredContent')
        if structured:
            return structured['result'] if set(structured) == {'result'} and isinstance(structured['result'], dict) else structured
        return json.loads(next(item['text'] for item in value['content'] if item['type'] == 'text'))

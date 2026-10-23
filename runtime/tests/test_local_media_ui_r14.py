"""Actual Chromium file-input -> HTTP staging -> MCP import -> plan preview."""
import json,shutil,time
from pathlib import Path
from verified_alexa.simulator import run_owned,ASSETS
from verified_alexa.cdp import BrowserConnection
from test_voice_authority_r14 import start,state

def test_local_file_selection_imports_and_confirms_through_mcp(tmp_path):
    selected=tmp_path/'selected-video.webm'
    shutil.copyfile(ASSETS/'clip-long.webm',selected)
    runtime=run_owned(tmp_path/'runtime',port=0,headless=True);launch=next(runtime)
    e={'scope':'scripted real local file selection/import and real MCP plan; no human usability or arbitrary codec acceptance'}
    try:
        with BrowserConnection(launch['cdp_endpoint'],launch['browser_target_id']) as c:
            start(c)
            doc=c.command('DOM.getDocument',{'depth':0})['root']['nodeId']
            node=c.command('DOM.querySelector',{'nodeId':doc,'selector':'#mediafile'})['nodeId']
            c.command('DOM.setFileInputFiles',{'nodeId':node,'files':[str(selected.resolve())]})
            for _ in range(100):
                if c.evaluate("document.querySelector('#uploadhint').textContent.startsWith('Imported locally') && !document.querySelector('#speechpreview').hidden"):break
                time.sleep(.2)
            e['preview_ui']=c.evaluate("({upload:document.querySelector('#uploadhint').textContent,selected:document.querySelector('#media').value,previewTitle:document.querySelector('#previewtitle').textContent,previewHidden:document.querySelector('#speechpreview').hidden})")
            assert e['preview_ui']['selected'].startswith('media-')
            assert not e['preview_ui']['previewHidden']
            e['confirmation']=c.evaluate('window.confirmVoice()')
            e['final_ui']=state(c)
            e['mcp_transcript']=json.loads((tmp_path/'runtime'/'MCP_HTTP_TRANSCRIPT.json').read_text(encoding='utf8'))
            (tmp_path/'LOCAL_MEDIA_UI.json').write_text(json.dumps(e,ensure_ascii=False,indent=2),encoding='utf8')
            assert e['confirmation']['plan']['content_id']==e['preview_ui']['selected']
            names=[x['request'].get('params',{}).get('name') for x in e['mcp_transcript']['entries']]
            assert 'import_local_media' in names and 'preview_request' in names and 'confirm_request' in names
            assert not e['final_ui']['paused']
    finally:runtime.close()

from pathlib import Path
import pytest
from verified_alexa.media_runtime import atomic_save

def test_transient_windows_sharing_lock_is_retried(tmp_path,monkeypatch):
    real=Path.replace; calls=[]
    def sharing(path,target):
        calls.append(1)
        if len(calls)==1:
            error=PermissionError('Windows read handle still open');error.winerror=32;raise error
        return real(path,target)
    monkeypatch.setattr(Path,'replace',sharing)
    target=tmp_path/'events.json'
    atomic_save(target,{'events':[{'status':'FAIL'}]})
    assert len(calls)==2 and '"FAIL"' in target.read_text()

def test_permanent_lock_is_bounded_and_preserves_previous_file(tmp_path,monkeypatch):
    target=tmp_path/'events.json';target.write_text('old')
    calls=[]
    def sharing(path,dest):
        calls.append(1);error=PermissionError('Windows read handle still open');error.winerror=32;raise error
    monkeypatch.setattr(Path,'replace',sharing)
    with pytest.raises(PermissionError):atomic_save(target,{'events':[]})
    assert len(calls)==8 and target.read_text()=='old'
    assert not list(tmp_path.glob('*.tmp'))

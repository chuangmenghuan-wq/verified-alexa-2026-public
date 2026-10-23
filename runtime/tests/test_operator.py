from verified_alexa.operator import VerifiedOperator,DemoAdapter
def test_legacy_and_synthetic_execution_are_disabled(tmp_path,monkeypatch):
    for backend in ('demo','synthetic','r5_dependency_reuse','future_ability'):
        monkeypatch.setenv('VERIFIED_ALEXA_BACKEND',backend)
        r=VerifiedOperator(DemoAdapter(),tmp_path).execute_verified_goal('restore service')
        assert r['final_status']=='UNAVAILABLE' and r['verification']['verified'] is False
def test_legacy_history_never_current(tmp_path):
    m=VerifiedOperator(receipt_dir=tmp_path)
    r=m.execute_verified_goal('restore service')
    historical=m.get_receipt(r['run_id'])
    assert historical['historical'] is True and historical['current_status'] is None

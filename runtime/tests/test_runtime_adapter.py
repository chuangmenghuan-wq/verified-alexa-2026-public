import json
from verified_alexa.future_ability_adapter import FutureAbilityEdgeAdapter
from verified_alexa.operator import VerifiedOperator
def test_global_complete_always_unavailable(tmp_path):
    p=tmp_path/'status.json'
    p.write_text(json.dumps({'GOAL_STATE':'VERIFIED_COMPLETE','LAST_VERIFIED_PROGRESS':{'semantic_evidence':{'status':'PASS'}}}))
    for goal in ('restore service','fix latency','different goal'):
        a=FutureAbilityEdgeAdapter(status_path=p,runtime_root=tmp_path)
        r=VerifiedOperator(a,tmp_path/'history').execute_verified_goal(goal)
        assert r['final_status']=='UNAVAILABLE' and not r['verification']['verified']
def test_disabled_adapter_does_not_read_production(tmp_path):
    a=FutureAbilityEdgeAdapter(status_path=tmp_path/'missing')
    a.restore_binding({'goal':'another goal','accepted_terminal_status':'PASS'})
    assert not a.verify('new goal')['verified'] and not a.isolation_readiness()['ready']

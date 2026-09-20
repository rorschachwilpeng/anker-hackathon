import pytest

def test_missing_done_is_failure():
    from scripts.evaluate_ch02 import check_case
    with pytest.raises(ValueError):check_case({'tool':None},[('token',{'text':'hi'})],[])

def test_wrong_tool_and_unpaired_results_fail():
    from scripts.evaluate_ch02 import check_case
    events=[('token',{'text':'hi'}),('done',{'finish_reason':'stop'})]
    rows=[{'role':'assistant','tool_calls':[{'id':'c','function':{'name':'query_order','arguments':'{}'}}]},{'role':'assistant','content':'hi'}]
    with pytest.raises(ValueError):check_case({'tool':'query_faq','args':{'keyword':'邮费'},'found':False},events,rows)

def test_expected_faq_miss_rejects_hit():
    from scripts.evaluate_ch02 import check_case
    import json
    events=[('token',{'text':'没有查到'}),('done',{'finish_reason':'stop'})]
    rows=[{'role':'assistant','tool_calls':[{'id':'c','function':{'name':'query_faq','arguments':'{"keyword":"邮费"}'}}]}, {'role':'tool','tool_call_id':'c','content':json.dumps({'ok':True,'data':{'found':True,'items':[{}]}})}, {'role':'assistant','content':'没有查到'}]
    with pytest.raises(ValueError):check_case({'tool':'query_faq','args':{'keyword':'邮费'},'found':False},events,rows)

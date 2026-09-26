"""Evaluate the local chat endpoint AND its persisted tool trace; no hidden retries."""
import asyncio,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import httpx
from app.config import Settings
from app.database import Database
from app.repositories import Repository
from scripts.evaluate import parse_sse,_chat_text

def check_case(case,events,rows):
    answer=_chat_text(events)
    calls=[call for row in rows for call in (row.get('tool_calls') or [])]
    if not rows or rows[-1]['role']!='assistant' or rows[-1].get('content')!=answer:
        raise ValueError('final answer not persisted')
    if case.get('tool') is None:
        if calls:raise ValueError('unexpected tool')
        return {'answer':answer,'tool':None}
    if len(calls)!=1 or calls[0]['function']['name']!=case['tool']:raise ValueError('wrong tool')
    call=calls[0];args=json.loads(call['function']['arguments'])
    for key,value in case.get('args',{}).items():
        if args.get(key)!=value:raise ValueError('wrong argument: '+key)
    results=[row for row in rows if row['role']=='tool' and row['tool_call_id']==call['id']]
    if len(results)!=1:raise ValueError('missing/duplicate tool result')
    result=json.loads(results[0]['content'])
    if result.get('ok') is not True:raise ValueError('tool failed')
    data=result['data']
    if 'found' in case and data.get('found')!=case['found']:raise ValueError('FAQ hit/miss mismatch')
    if case.get('mock'):
        if data.get('source')!='mock' or '模拟' not in answer:raise ValueError('mock disclosure missing')
        if case['tool']=='query_logistics' and any(str(data[k]) not in answer for k in ('order_id','status','location')):raise ValueError('answer not grounded')
    if case.get('found') is False and not any(x in answer for x in ('未查到','没有查到','没查到','未查询到','没有查询到','未找到','没有找到')):raise ValueError('miss not disclosed')
    return {'answer':answer,'tool':case['tool'],'args':args,'result':result}

async def main():
    root=Path(__file__).resolve().parents[1]
    cases=json.loads((root/'evals/ch02-cases.json').read_text())
    settings=Settings();db=Database(settings.database_url.get_secret_value());repo=Repository(db.sessions)
    results=[]
    try:
        async with httpx.AsyncClient(base_url='http://127.0.0.1:8000',timeout=150) as client:
            for case in cases:
                item={'id':case['id'],'passed':False}
                try:
                    r=await client.post('/chat',json={'message':case['input']});r.raise_for_status()
                    events=parse_sse(r.content)
                    sessions=[p['conversation_id'] for e,p in events if e=='session']
                    if len(sessions)!=1:raise ValueError('missing session')
                    rows=await repo.load_messages(sessions[0])
                    item.update(check_case(case,events,rows));item['conversation_id']=sessions[0]
                    if case.get('tool')=='create_ticket':
                        from sqlalchemy import select
                        from app.models import tickets
                        number=item['result']['data']['ticket_no']
                        async with db.sessions() as session:
                            record=(await session.execute(select(tickets).where(tickets.c.ticket_no==number))).mappings().one()
                            if record['conversation_id']!=sessions[0] or number not in item['answer']:raise ValueError('ticket evidence mismatch')
                    if case.get('followup'):
                        follow=await client.post('/chat',json={'message':case['followup'],'conversation_id':sessions[0]});follow.raise_for_status()
                        follow_answer=_chat_text(parse_sse(follow.content))
                        if case['followup_contains'] not in follow_answer:raise ValueError('context recall failed')
                        item['followup_answer']=follow_answer
                    item['passed']=True
                except Exception as exc:item['error']=type(exc).__name__+': '+str(exc)[:200]
                results.append(item)
                print(case['id']+': '+('PASS' if item['passed'] else 'FAIL'),flush=True)
    finally:await db.close()
    report={'total':len(results),'passed':sum(r['passed'] for r in results),'results':results}
    (root/'evals/ch02-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(f"{report['passed']}/{report['total']} passed")
    return int(report['passed']!=report['total'])
if __name__=='__main__':sys.exit(asyncio.run(main()))

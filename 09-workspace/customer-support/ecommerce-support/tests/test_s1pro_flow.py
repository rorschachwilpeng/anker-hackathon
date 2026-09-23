from dataclasses import asdict

from app.s1pro.state import S1ProState


class Repo:
    def __init__(self):
        self.states={};self.messages={};self.tickets={}
    async def new_conversation(self,user_id):
        ident=len(self.states)+1;self.states[ident]=S1ProState();self.messages[ident]=[];return ident
    async def get_conversation(self,ident):return {'id':ident,'user_id':'local-demo'} if ident in self.states else None
    async def load_s1pro_state(self,ident):return S1ProState.from_dict(asdict(self.states[ident]))
    async def save_s1pro_state(self,ident,state):self.states[ident]=S1ProState.from_dict(asdict(state))
    async def reserve_s1pro_ticket(self,ident):
        self.states[ident].ticket_no=self.states[ident].ticket_no or 'Tfixed'
        return self.states[ident].ticket_no
    async def append_message(self,ident,role,content=None,**kwargs):self.messages[ident].append({'role':role,'content':content});return len(self.messages[ident])
    async def load_messages(self,ident):return self.messages[ident]
    async def create_ticket_once(self,number,ident,description,kind):
        self.tickets[number]={'ticket_no':number,'conversation_id':ident,'description':description,'ticket_type':kind,'status':'待处理'}
        return self.tickets[number]


class Understanding:
    async def interpret(self,message,state):
        return {'product_candidate':'T2080' if '扫地机器人' in message else None,
                'symptom_candidate':'吸力变弱' if '不吸' in message else None,
                'feedback':'resolved' if '好了' in message else 'unresolved' if '还是不行' in message else None,
                'ticket_consent':'可以建单' in message,'next_action':'search'}


class Search:
    def __init__(self):self.calls=[]
    async def find_for_model(self,query,model):
        self.calls.append((query,model))
        return [{'id':1,'answer':'先清理侧刷、滚刷、尘盒和过滤器。','product_model':'T2080','source_title':'Official suction help','source_url':'https://service.eufy.com/a'},
                {'id':2,'answer':'再检查刷罩胶条和尘盒泡棉密封。','product_model':'T2080','source_title':'Official suction help','source_url':'https://service.eufy.com/a'}]


async def test_clarify_then_step_feedback_resolution_without_ticket():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    first=await flow.handle('我的 S1 Pro 不吸了')
    assert first['action']=='clarify_product' and search.calls==[]
    second=await flow.handle('是的，就是 eufy 扫地机器人 S1 Pro',first['conversation_id'])
    assert second['action']=='ask_feedback' and second['source']['url']=='https://service.eufy.com/a'
    assert search.calls[0][1]=='T2080'
    done=await flow.handle('清理后好了',first['conversation_id'])
    assert done['action']=='resolved' and repo.tickets=={}


async def test_affirmative_answer_to_explicit_product_question_confirms_model():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    first=await flow.handle('我的 S1 Pro 不吸了')
    answer=await flow.handle('是的',first['conversation_id'])
    assert answer['action']=='ask_feedback'
    assert repo.states[first['conversation_id']].confirmed_model=='T2080'
    assert search.calls[0][1]=='T2080'


async def test_unresolved_steps_require_confirmation_and_reuse_ticket():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();flow=S1ProFlow(repo,Understanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    cid=first['conversation_id']
    assert (await flow.handle('可以建单',cid))['action']!='ticket_created'
    assert (await flow.handle('eufy 扫地机器人 S1 Pro',cid))['action']=='ask_feedback'
    assert (await flow.handle('还是不行',cid))['action']=='ask_feedback'
    assert (await flow.handle('还是不行',cid))['action']=='confirm_ticket'
    created=await flow.handle('可以建单',cid)
    assert created['action']=='ticket_created' and created['ticket_no']=='Tfixed'
    assert '吸力变弱' in repo.tickets['Tfixed']['description']
    assert 'https://service.eufy.com/a' in repo.tickets['Tfixed']['description']
    again=await flow.handle('可以建单',cid)
    assert again['ticket_no']=='Tfixed' and len(repo.tickets)==1


async def test_unrelated_issue_does_not_get_suction_steps_or_search():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    first=await flow.handle('eufy 扫地机器人 S1 Pro 无法充电')
    assert first['action']=='no_answer' and first['source'] is None
    assert first['tool_calls']==0 and search.calls==[] and repo.tickets=={}


async def test_ticket_failure_does_not_claim_success():
    from app.s1pro.flow import S1ProFlow
    class BrokenRepo(Repo):
        async def create_ticket_once(self,*args):
            raise RuntimeError('database unavailable')
    repo=BrokenRepo();flow=S1ProFlow(repo,Understanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    cid=first['conversation_id']
    await flow.handle('是的',cid)
    await flow.handle('还是不行',cid)
    await flow.handle('还是不行',cid)
    failure=await flow.handle('可以建单',cid)
    assert failure['action']=='ticket_failed' and failure['ticket_no'] is None
    assert repo.tickets=={}

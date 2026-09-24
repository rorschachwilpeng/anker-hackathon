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


def observation(candidate='T2080', visible_text=None, visible_clues=None, uncertainties=None):
    from app.s1pro.vision import VisualObservation
    return VisualObservation(
        candidate_model=candidate,
        visible_text=visible_text or ['T2080'],
        visible_clues=visible_clues or ['robot vacuum label'],
        uncertainties=uncertainties or [],
    )


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


async def test_product_affirmation_does_not_depend_on_another_model_call():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();flow=S1ProFlow(repo,Understanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    class UnavailableUnderstanding:
        async def interpret(self,message,state):
            raise RuntimeError('model unavailable')
    flow.understanding=UnavailableUnderstanding()
    answer=await flow.handle('是的',first['conversation_id'])
    assert answer['action']=='ask_feedback'
    assert answer['source']['url']=='https://service.eufy.com/a'


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


async def test_natural_consent_creates_ticket_via_understanding():
    from app.s1pro.flow import S1ProFlow
    class ConsentUnderstanding(Understanding):
        async def interpret(self,message,state):
            result=await super().interpret(message,state)
            result['ticket_consent']=('建' in message) or ('可以' in message)
            return result
    repo=Repo();flow=S1ProFlow(repo,ConsentUnderstanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    cid=first['conversation_id']
    await flow.handle('是的',cid)
    await flow.handle('还是不行',cid)
    await flow.handle('还是不行',cid)
    created=await flow.handle('好的，建吧',cid)
    assert created['action']=='ticket_created' and created['ticket_no']=='Tfixed'
    assert '吸力变弱' in repo.tickets['Tfixed']['description']


async def test_ambiguous_reply_during_ticket_confirmation_creates_no_ticket():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();flow=S1ProFlow(repo,Understanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    cid=first['conversation_id']
    await flow.handle('是的',cid)
    await flow.handle('还是不行',cid)
    await flow.handle('还是不行',cid)
    vague=await flow.handle('嗯',cid)
    assert vague['action']=='confirm_ticket' and repo.tickets=={}
    assert repo.states[cid].ticket_no is None


async def test_ticket_consent_understanding_failure_degrades_without_ticket():
    from app.s1pro.flow import S1ProFlow
    class UnavailableUnderstanding:
        async def interpret(self,message,state):
            raise RuntimeError('model unavailable')
    repo=Repo();flow=S1ProFlow(repo,Understanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    cid=first['conversation_id']
    await flow.handle('是的',cid)
    await flow.handle('还是不行',cid)
    await flow.handle('还是不行',cid)
    flow.understanding=UnavailableUnderstanding()
    degraded=await flow.handle('好的，建吧',cid)
    assert degraded['action']=='degraded' and repo.tickets=={}
    assert repo.states[cid].ticket_no is None


async def test_unrelated_issue_does_not_get_suction_steps_or_search():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    first=await flow.handle('eufy 扫地机器人 S1 Pro 无法充电')
    assert first['action']=='no_answer'
    assert first['tool_calls']==0 and search.calls==[] and repo.tickets=={}
    assert first['source']['url'].endswith('What-information-does-eufy-need-to-resolve-a-technical-issue-with-my-S1-Pro')
    assert repo.states[first['conversation_id']].awaiting is None


async def test_after_no_answer_user_can_redescribe_suction_symptom():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    first=await flow.handle('eufy 扫地机器人 S1 Pro 无法充电')
    assert first['action']=='no_answer'
    retry=await flow.handle('其实是吸力变弱了，不吸了',first['conversation_id'])
    assert retry['action']=='ask_feedback'
    assert search.calls and search.calls[0][1]=='T2080'
    assert retry['source']['url']=='https://service.eufy.com/a'


async def test_after_no_answer_consent_creates_ticket():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();flow=S1ProFlow(repo,Understanding(),Search())
    first=await flow.handle('eufy 扫地机器人 S1 Pro 无法充电')
    assert first['action']=='no_answer'
    created=await flow.handle('可以建单',first['conversation_id'])
    assert created['action']=='ticket_created' and created['ticket_no']=='Tfixed'
    assert '待确认' in repo.tickets['Tfixed']['description']
    again=await flow.handle('可以建单',first['conversation_id'])
    assert again['ticket_no']=='Tfixed' and len(repo.tickets)==1


async def test_no_consent_without_confirmation_after_no_answer():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();flow=S1ProFlow(repo,Understanding(),Search())
    first=await flow.handle('eufy 扫地机器人 S1 Pro 无法充电')
    assert first['action']=='no_answer' and repo.tickets=={}


async def test_unresolved_without_keywords_advances_to_next_step():
    from app.s1pro.flow import S1ProFlow
    class NuancedUnderstanding(Understanding):
        async def interpret(self,message,state):
            result=await super().interpret(message,state)
            if '没什么变化' in message:result['feedback']='unresolved'
            return result
    repo=Repo();flow=S1ProFlow(repo,NuancedUnderstanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    cid=first['conversation_id']
    await flow.handle('是的',cid)
    follow=await flow.handle('试过了，没什么变化',cid)
    assert follow['action']=='ask_feedback'
    assert '胶条' in follow['reply']
    assert follow['reply']!='请告诉我刚才的步骤是否让吸力恢复？'


async def test_resolved_via_model_without_keywords():
    from app.s1pro.flow import S1ProFlow
    class ResolvedUnderstanding(Understanding):
        async def interpret(self,message,state):
            result=await super().interpret(message,state)
            if '搞定' in message:result['feedback']='resolved'
            return result
    repo=Repo();flow=S1ProFlow(repo,ResolvedUnderstanding(),Search())
    first=await flow.handle('我的 S1 Pro 不吸了')
    cid=first['conversation_id']
    await flow.handle('是的',cid)
    done=await flow.handle('搞定了',cid)
    assert done['action']=='resolved' and repo.tickets=={}


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


async def test_visual_t2080_candidate_waits_for_confirmation_before_search():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    first=await flow.handle('我的 S1 Pro 不吸了',observation=observation())
    state=repo.states[first['conversation_id']]
    assert first['action']=='clarify_product'
    assert state.confirmed_model is None and state.pending_model_candidate=='T2080'
    assert search.calls==[]

    confirmed=await flow.handle('是的',first['conversation_id'])
    assert confirmed['action']=='ask_feedback'
    assert repo.states[first['conversation_id']].confirmed_model=='T2080'
    assert search.calls[0][1]=='T2080'


async def test_s1_pro_alias_in_visible_model_code_is_not_a_conflict():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    first=await flow.handle(
        '我的 S1 Pro 不吸了',
        observation=observation('T2080',visible_text=['Model: S1 Pro'],visible_clues=['robot vacuum label']),
    )

    assert first['action']=='clarify_product'
    assert first['reply']=='请确认：您说的是 eufy Robot Vacuum Omni S1 Pro 扫地机器人吗？'
    assert repo.states[first['conversation_id']].pending_model_candidate=='T2080'
    assert search.calls==[]


async def test_other_visual_candidate_and_yes_cannot_confirm_t2080():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    first=await flow.handle(
        '我的 S1 Pro 不吸了',
        observation=observation('other',visible_text=['S1 Pro'],visible_clues=['non-robot appliance']),
    )
    assert first['action']=='clarify_product'
    assert repo.states[first['conversation_id']].pending_model_candidate is None
    assert search.calls==[]

    answer=await flow.handle('是的',first['conversation_id'])
    assert answer['action']=='clarify_product'
    assert repo.states[first['conversation_id']].confirmed_model is None
    assert search.calls==[]


async def test_conflicting_visual_and_explicit_text_never_searches_this_turn():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    first=await flow.handle(
        'eufy Robot Vacuum Omni S1 Pro 扫地机器人吸力变弱',
        observation=observation('other',visible_text=['S1 Pro'],visible_clues=['non-robot appliance']),
    )

    assert first['action']=='clarify_product'
    assert repo.states[first['conversation_id']].confirmed_model is None
    assert repo.states[first['conversation_id']].pending_model_candidate is None
    assert search.calls==[]


async def test_visible_non_target_product_clues_conflict_with_explicit_target_text():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    visual=observation(
        'unknown',
        visible_text=['eufy SECURITY','Indoor Camera','Model: S330','NOT A VACUUM'],
        visible_clues=['security camera product label','model S330'],
    )

    first=await flow.handle('eufy Robot Vacuum Omni S1 Pro 扫地机器人不吸了',observation=visual)
    state=repo.states[first['conversation_id']]

    assert first['action']=='clarify_product'
    assert state.confirmed_model is None
    assert state.pending_model_candidate is None
    assert search.calls==[]


async def test_unknown_visual_candidate_is_not_presented_as_t2080():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    visual=observation('unknown',visible_text=['模糊文字'],visible_clues=['型号无法辨认'])

    first=await flow.handle('我的 S1 Pro 不吸了',observation=visual)
    state=repo.states[first['conversation_id']]

    assert first['action']=='clarify_product'
    assert '无法可靠确认' in first['reply']
    assert '请确认：您说的是 eufy Robot Vacuum Omni S1 Pro' not in first['reply']
    assert state.confirmed_model is None and state.pending_model_candidate is None
    assert search.calls==[]


async def test_image_only_asks_symptom_then_product_before_search():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    first=await flow.handle('',observation=observation())
    state=repo.states[first['conversation_id']]
    assert first['action']=='ask_symptom'
    assert state.awaiting=='symptom'
    assert state.confirmed_model is None and state.pending_model_candidate=='T2080'
    assert repo.messages[first['conversation_id']][0]['content']=='[用户上传图片]'
    assert set(state.visual_observation)=={'candidate_model','visible_text','visible_clues','uncertainties'}
    assert search.calls==[]

    symptom=await flow.handle('不吸了',first['conversation_id'])
    assert symptom['action']=='clarify_product'
    assert search.calls==[]

    confirmed=await flow.handle('是的',first['conversation_id'])
    assert confirmed['action']=='ask_feedback'
    assert search.calls[0][1]=='T2080'


async def test_image_only_reply_references_visible_clues():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    first=await flow.handle('',observation=observation(visible_text=['Model: T2080','S1 Pro'],visible_clues=['robot vacuum label']))
    assert first['action']=='ask_symptom'
    assert 'Model: T2080' in first['reply'] and '像是' in first['reply']
    assert '已识别' not in first['reply']
    state=repo.states[first['conversation_id']]
    assert state.pending_model_candidate=='T2080' and state.confirmed_model is None
    assert search.calls==[]


async def test_image_only_unknown_candidate_keeps_generic_symptom_request():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)
    first=await flow.handle('',observation=observation('unknown',visible_text=['模糊文字'],visible_clues=['无法辨认']))
    assert first['action']=='ask_symptom'
    assert first['reply']=='请用文字告诉我产品型号和遇到的故障现象。'
    assert search.calls==[]


async def test_image_failure_keeps_text_path_and_previous_state_fields():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    first=await flow.handle('我的 S1 Pro 不吸了',image_failed=True)
    assert first['action']=='clarify_product'
    assert '图片暂时没能读出来' in first['reply']
    assert repo.states[first['conversation_id']].visual_observation is None
    assert search.calls==[]

    confirmed=await flow.handle('是的',first['conversation_id'])
    assert confirmed['action']=='ask_feedback'
    assert search.calls[0][1]=='T2080'


async def test_failed_image_without_text_requests_symptom_and_stores_no_image_data():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    result=await flow.handle('',image_failed=True)
    state=repo.states[result['conversation_id']]

    assert result['action']=='ask_symptom'
    assert '图片暂时没能读出来' in result['reply']
    assert '[用户上传图片]' in repo.messages[result['conversation_id']][0]['content']
    assert state.visual_observation is None and state.pending_model_candidate is None
    assert search.calls==[]


async def test_image_failure_preserves_previous_visual_observation_and_candidate():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();search=Search();flow=S1ProFlow(repo,Understanding(),search)

    first=await flow.handle('',observation=observation())
    previous=repo.states[first['conversation_id']].visual_observation.copy()
    continued=await flow.handle('不吸了',first['conversation_id'],image_failed=True)

    state=repo.states[first['conversation_id']]
    assert continued['action']=='clarify_product'
    assert '图片暂时没能读出来' in continued['reply']
    assert state.visual_observation==previous
    assert state.pending_model_candidate=='T2080'
    assert search.calls==[]


async def test_ticket_summary_marks_visual_clues_unverified():
    from app.s1pro.flow import S1ProFlow
    repo=Repo();flow=S1ProFlow(repo,Understanding(),Search())
    visual=observation(
        visible_text=['T2080'],
        visible_clues=['灰色机器人外观'],
        uncertainties=['无法确认设备当前状态'],
    )

    first=await flow.handle('我的 S1 Pro 不吸了',observation=visual)
    cid=first['conversation_id']
    await flow.handle('是的',cid)
    await flow.handle('还是不行',cid)
    await flow.handle('还是不行',cid)
    created=await flow.handle('可以建单',cid)

    description=repo.tickets[created['ticket_no']]['description']
    assert '图片提示（未核实）' in description
    assert '灰色机器人外观' in description
    assert '无法确认设备当前状态' in description
    assert 'base64' not in description

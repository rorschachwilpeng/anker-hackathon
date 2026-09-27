import re

from app.tools.business import build_s1pro_tools
from app.tools.executor import ToolExecutor
from app.s1pro import messages as copy
from app.s1pro.vision import VisualObservation


def _mentions_s1_pro(message):
    return re.search(r's1[\s-]*pro',message,re.IGNORECASE) is not None


def _explicit_product(message):
    lowered=message.casefold()
    if any(term in lowered for term in ('吸乳器','吸奶器','breast pump')):
        return False
    return (('t2080' in lowered and '扫地机器人' in message)
            or (_mentions_s1_pro(message) and 'eufy' in lowered
                and ('扫地机器人' in message or 'robot vacuum' in lowered)))


def _explicit_consent(message):
    return any(phrase in message for phrase in ('可以建单','同意建单','确认创建工单','请创建工单'))


def _affirm_product(message):
    return message.strip('。！!，, ').casefold() in ('是的', '对', '对的', '是', '确认', '没错', 'yes')


def _confirms_t2080_vacuum(message):
    lowered=message.casefold()
    return ('t2080' in lowered and '扫地机器人' in message
            and not any(term in message for term in ('吸乳器','吸奶器'))
            and any(word in message for word in ('对','就是','确认')))


def _confirms_visible_vacuum(message,observation):
    return (observation is not None and observation.candidate_model=='T2080'
            and '扫地机器人' in message and any(word in message for word in ('是','就是','对'))
            and '吸乳器' not in message and '吸奶器' not in message)


def _visual_conflicts_with_t2080(observation):
    if observation.candidate_model == 'other':
        return True
    visible_content=' '.join(observation.visible_text+observation.visible_clues).casefold()
    if any(term in visible_content for term in ('camera','摄像头','doorbell','门铃','not a vacuum','非扫地机器人')):
        return True
    model_codes=re.findall(r'\bmodel(?:\s*(?:number|no\.?))?\s*[:#]?\s*([a-z0-9-]+)',visible_content)
    target_models={'t2080','s1','s1-pro','s1pro'}
    return any(code.casefold() not in target_models for code in model_codes)


class S1ProFlow:
    def __init__(self,repo,understanding,knowledge_search):
        self.repo=repo;self.understanding=understanding;self.knowledge_search=knowledge_search

    async def handle(
        self,
        message,
        conversation_id=None,
        *,
        observation: VisualObservation | None = None,
        image_failed: bool = False,
        events=None,
    ):
        async def emit(payload):
            if events is not None:
                await events(payload)

        if conversation_id is None:
            conversation_id=await self.repo.new_conversation('local-demo')
        else:
            row=await self.repo.get_conversation(conversation_id)
            if not row or row['user_id']!='local-demo':
                raise LookupError('conversation_not_found')
        state=await self.repo.load_s1pro_state(conversation_id)
        had_awaiting_symptom=state.awaiting=='symptom'
        saved_message=message if message.strip() else '[用户上传图片]' if observation is not None or image_failed else message
        await self.repo.append_message(conversation_id,'user',saved_message)
        if observation is not None:
            state.visual_observation={
                'candidate_model':observation.candidate_model,
                'visible_text':list(observation.visible_text),
                'visible_clues':list(observation.visible_clues),
                'uncertainties':list(observation.uncertainties),
            }
        tool_calls=0
        image_failure_notice=copy.IMAGE_FAILURE_NOTICE if image_failed else ''
        urgent_notice=''

        async def respond(action,reply,source=None,ticket_no=None,ticket_url=None):
            if urgent_notice:
                reply=f'{urgent_notice}{reply}'
            await self.repo.save_s1pro_state(conversation_id,state)
            await self.repo.append_message(conversation_id,'assistant',reply)
            progress={'product':'eufy S1 Pro (T2080)' if state.confirmed_model else None,
                      'symptom':state.symptom,
                      'suggested':len(state.suggested_steps),
                      'tried':len(state.completed_steps),
                      'sources':[item['title'] for item in state.sources],
                      'awaiting':state.awaiting,
                      'urgent':state.urgent,
                      'ticket_no':state.ticket_no}
            return {'conversation_id':conversation_id,'action':action,'reply':reply,
                    'source':source,'ticket_no':ticket_no,'tool_calls':tool_calls,
                    'progress':progress,'ticket_url':ticket_url,
                    'notice':image_failure_notice or None}

        def capture_urgency(understood):
            nonlocal urgent_notice
            if understood.get('urgency')=='urgent':
                reason=understood.get('urgency_reason')
                if reason and not state.urgency_reason:
                    state.urgency_reason=str(reason)[:50]
                if not state.urgent:
                    state.urgent=True
                    urgent_notice=copy.URGENT_NOTICE

        def handoff_summary():
            suggested='、'.join(state.suggested_steps) or '无'
            tried='、'.join(state.completed_steps) or '尚未确认'
            sources='、'.join(f'{item["title"]}（{item["url"]}）' for item in state.sources) or '无'
            unresolved=('用户反馈已恢复' if state.feedback=='已恢复正常' else
                        '吸力仍未恢复，待人工核实' if state.feedback else '处理结果待确认')
            return (f'产品：eufy S1 Pro 扫地机器人（T2080）；症状：{state.symptom or "待确认"}；'
                    f'已建议步骤：{suggested}；已尝试步骤：{tried}；'
                    f'用户反馈：{state.feedback or "未提供"}；资料来源：{sources}；'
                    f'未解决事项：{unresolved}')

        async def create_ticket():
            number=await self.repo.reserve_s1pro_ticket(conversation_id)
            state.ticket_no=number
            history=await self.repo.load_messages(conversation_id)
            original=next((row['content'] for row in history if row['role']=='user'),message)
            visual_cues=[]
            if state.visual_observation:
                visual_cues=(state.visual_observation.get('visible_text',[])
                             +state.visual_observation.get('visible_clues',[])
                             +state.visual_observation.get('uncertainties',[]))
            visual_summary=(f'图片提示（未核实）：{"；".join(visual_cues) or "无可读线索"}；'
                            if state.visual_observation else '')
            if state.urgency_reason:
                urgency_line=f'有（用户明确表示着急：{state.urgency_reason}）'
                urgency_clause=f'用户自述{state.urgency_reason}，'
            elif state.urgent:
                urgency_line='有（用户明确表示着急）'
                urgency_clause='用户明确表示着急，'
            else:
                urgency_line='无'
                urgency_clause=''
            handoff=f'交接摘要：{urgency_clause}{handoff_summary()}。'
            description=(handoff+
                         f'产品型号：T2080；原始诉求：{original}；症状：{state.symptom or "待确认"}；'
                         f'用户急迫表达：{urgency_line}；'
                         f'已建议步骤：{"、".join(state.suggested_steps) or "无"}；'
                         f'已尝试步骤：{"、".join(state.completed_steps) or "未反馈"}；'
                         f'用户反馈：{state.feedback or "未提供"}；'
                         f'{visual_summary}'
                         f'资料来源：{"、".join(source["url"] for source in state.sources) or "无"}；'
                         '未解决事项：吸力问题待人工核实。')
            try:
                await self.repo.create_ticket_once(number,conversation_id,description[:2000],'售后')
            except Exception:
                return await respond('ticket_failed',copy.TICKET_FAILED)
            state.awaiting=None
            return await respond('ticket_created',copy.ticket_created(number),
                                 ticket_no=number,ticket_url=f'/s1pro/tickets/{number}')

        if state.ticket_no and state.awaiting is None:
            return await respond('ticket_created',copy.ticket_terminal(state.ticket_no),
                                 ticket_no=state.ticket_no,ticket_url=f'/s1pro/tickets/{state.ticket_no}')

        if state.awaiting=='ticket_confirmation':
            if not _explicit_consent(message):
                return await respond('confirm_ticket',copy.pick(copy.CONFIRM_TICKET_ASKS,conversation_id))
            return await create_ticket()

        affirmative_confirmation=(
            state.awaiting=='product'
            and ((state.pending_model_candidate=='T2080'
                  and (_affirm_product(message) or _confirms_t2080_vacuum(message)))
                 or _confirms_visible_vacuum(message,observation))
        )
        if affirmative_confirmation:
            understood={}
        elif message.strip():
            await emit({'type':'stage','stage':'understanding'})
            try:
                understood=await self.understanding.interpret(message,state)
            except Exception:
                if any(term in message for term in ('吸力变弱','吸力恢复','还是没用','转人工')):
                    understood={}
                else:
                    return await respond('degraded',copy.DEGRADED_UNDERSTANDING)
            capture_urgency(understood)
        else:
            understood={}

        if understood.get('gratitude') and state.feedback=='已恢复正常':
            return await respond('resolved',copy.pick(copy.GRATITUDE_ACKS,conversation_id))

        if state.symptom is None:
            state.symptom=understood.get('symptom_candidate') or ('吸力变弱' if any(term in message for term in ('吸力变弱','不吸')) else None)

        if not message.strip() and (observation is not None or image_failed) and state.symptom is None:
            state.pending_model_candidate=(
                'T2080' if observation is not None and observation.candidate_model=='T2080' else None
            )
            state.awaiting='symptom'
            if observation is not None and observation.candidate_model=='T2080':
                seen='、'.join((observation.visible_text+observation.visible_clues)[:3])
                if seen:
                    return await respond('ask_symptom',copy.ask_symptom_with_clues(seen))
            return await respond('ask_symptom',copy.pick(copy.ASK_SYMPTOM_IMAGE_ONLYS,conversation_id))

        explicit_product=_explicit_product(message)
        text_candidate=understood.get('product_candidate')
        visual_conflict=_visual_conflicts_with_t2080(observation) if observation is not None else False
        image_conflict=(
            observation is not None
            and (
                (visual_conflict and explicit_product)
                or (observation.candidate_model=='T2080' and text_candidate not in (None,'T2080'))
            )
        )
        if image_conflict:
            state.confirmed_model=None
            state.pending_model_candidate=None
            state.awaiting='product'
            return await respond('clarify_product',copy.CLARIFY_PRODUCT_IMAGE_TEXT_CONFLICT)

        if observation is not None and visual_conflict:
            state.confirmed_model=None
            state.pending_model_candidate=None
            state.awaiting='product'
            return await respond('clarify_product',copy.CLARIFY_PRODUCT_IMAGE_NOT_TARGET)

        if observation is not None and observation.candidate_model=='unknown' and state.confirmed_model is None:
            state.pending_model_candidate=None
            state.awaiting='product'
            return await respond('clarify_product',copy.CLARIFY_PRODUCT_IMAGE_UNKNOWN)

        if ('吸乳器' in message or '吸奶器' in message or 'breast pump' in message.casefold()) and not explicit_product:
            state.confirmed_model=None
            state.pending_model_candidate=None
            state.awaiting='product'
            return await respond('clarify_product',copy.CLARIFY_PRODUCT_NOT_VACUUM)

        if (state.confirmed_model is None and observation is None and state.visual_observation is None
                and _mentions_s1_pro(message) and '扫地机器人' not in message and 't2080' not in message.casefold()):
            state.pending_model_candidate=None
            state.awaiting='product'
            return await respond('clarify_product',copy.CLARIFY_PRODUCT_AMBIGUOUS)

        if observation is not None and observation.candidate_model=='T2080' and state.confirmed_model is None:
            state.pending_model_candidate='T2080'
        elif observation is not None and state.confirmed_model is None:
            state.pending_model_candidate=None

        if state.confirmed_model is None:
            if not (explicit_product or affirmative_confirmation):
                if state.pending_model_candidate is None:
                    previous_candidate=(state.visual_observation or {}).get('candidate_model')
                    if state.visual_observation is None or previous_candidate=='T2080':
                        state.pending_model_candidate='T2080'
                state.awaiting='product'
                if state.pending_model_candidate=='T2080':
                    reply=(copy.CLARIFY_PRODUCT_AMBIGUOUS if _mentions_s1_pro(message) and '扫地机器人' not in message
                           else copy.pick(copy.CLARIFY_PRODUCT_T2080S,conversation_id))
                elif state.visual_observation and state.visual_observation.get('candidate_model')=='unknown':
                    reply=copy.CLARIFY_PRODUCT_IMAGE_UNKNOWN
                else:
                    reply=copy.CLARIFY_PRODUCT_GENERIC
                return await respond('clarify_product',reply)
            state.confirmed_model='T2080'
            state.pending_model_candidate=None
            state.awaiting=None

        if state.symptom is None:
            state.symptom=understood.get('symptom_candidate')
        if state.symptom is None and (state.visual_observation is not None or had_awaiting_symptom):
            state.awaiting='symptom'
            return await respond('ask_symptom',copy.pick(copy.ASK_SYMPTOMS,conversation_id))
        if state.symptom!='吸力变弱':
            state.awaiting=None
            return await respond(
                'no_answer',copy.pick(copy.NO_ANSWERS,conversation_id),
                source={'title':'eufy：解决技术问题需要提供的信息',
                        'url':'https://service.eufy.com/eu/article-description/What-information-does-eufy-need-to-resolve-a-technical-issue-with-my-S1-Pro'},
            )

        human_request=understood.get('human_request') or any(term in message for term in ('转人工','人工客服','真人客服'))
        if state.awaiting=='feedback':
            feedback=understood.get('feedback')
            unresolved=feedback=='unresolved' or any(word in message for word in ('不行','没用','无效','仍然','还是','没恢复'))
            resolved=not unresolved and (feedback=='resolved' or any(word in message for word in ('好了','恢复','正常','解决')))
            if resolved or unresolved:
                if state.suggested_steps:
                    latest=state.suggested_steps[-1]
                    if latest not in state.completed_steps:state.completed_steps.append(latest)
            if resolved:
                state.feedback='已恢复正常';state.awaiting=None
                reply=(copy.resolved_with_reason(state.urgency_reason) if state.urgency_reason
                       else copy.pick(copy.RESOLVEDS,conversation_id))
                return await respond('resolved',reply,
                                     source=state.sources[-1] if state.sources else None)
            if not unresolved and not human_request:
                return await respond('ask_feedback',copy.pick(copy.ASK_FEEDBACK_RETRIES,conversation_id+len(state.completed_steps)))
            if unresolved:state.feedback=message
            if understood.get('frustration') and len(state.completed_steps)>=2 and not human_request:
                state.awaiting='ticket_confirmation'
                return await respond('confirm_ticket',copy.confirm_ticket_with_summary(copy.CONFIRM_TICKET_FRUSTRATION,handoff_summary()))

        if state.ticket_no is None and (human_request or understood.get('ticket_consent')):
            state.awaiting='ticket_confirmation'
            return await respond('confirm_ticket',copy.confirm_ticket_human_request(handoff_summary(),conversation_id))

        tools=build_s1pro_tools(self.knowledge_search)
        await emit({'type':'stage','stage':'searching'})
        executor=ToolExecutor(tools,timeout_seconds=20)
        result=None
        tool_calls=1
        async for event in executor.execute({'id':'s1pro-search','name':'search_support_knowledge',
                                             'args':{'query':'S1 Pro 吸力变弱 清理侧刷滚刷尘盒过滤器 检查胶条密封',
                                                     'confirmed_model':'T2080'}}):
            if 'result' in event.data:result=event.data['result']
        if result is None or not result['ok']:
            state.awaiting='ticket_confirmation'
            return await respond('degraded',copy.confirm_ticket_with_summary(copy.DEGRADED_SEARCH,handoff_summary()))
        items=result['data']['items']
        steps=[item for item in items if any(word in item['answer'] for word in ('侧刷','滚刷','胶条','密封'))]
        steps.sort(key=lambda item:0 if '侧刷' in item['answer'] else 1)
        next_step=next((item for item in steps if item['answer'] not in state.suggested_steps),None)
        if next_step is None:
            state.awaiting='ticket_confirmation'
            return await respond('confirm_ticket',copy.confirm_ticket_with_summary(
                copy.pick(copy.CONFIRM_TICKET_STEPS_EXHAUSTEDS,conversation_id),handoff_summary()))
        first_step=not state.suggested_steps
        state.suggested_steps.append(next_step['answer'])
        state.awaiting='feedback'
        source={'title':next_step['source_title'],'url':next_step['source_url']}
        if source not in state.sources:state.sources.append(source)
        await emit({'type':'search_done','title':next_step['source_title'],'url':next_step['source_url']})
        return await respond('ask_feedback',copy.suggest_step(next_step['answer'],first_step,seed=conversation_id+len(state.suggested_steps)),source=source)

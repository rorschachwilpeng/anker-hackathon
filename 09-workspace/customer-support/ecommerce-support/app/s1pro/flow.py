import re

from app.tools.business import build_s1pro_tools
from app.tools.executor import ToolExecutor
from app.s1pro import messages as copy
from app.s1pro.vision import VisualObservation


def _explicit_product(message):
    lowered=message.casefold()
    return 's1 pro' in lowered and 'eufy' in lowered and ('扫地机器人' in message or 'robot vacuum' in lowered)


def _explicit_consent(message):
    return any(phrase in message for phrase in ('可以建单','同意建单','确认创建工单','请创建工单'))


def _affirm_product(message):
    return message.strip('。！!，, ').casefold() in ('是的', '对', '对的', '是', '确认', '没错', 'yes')


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
            if understood.get('urgency')=='urgent' and not state.urgent:
                state.urgent=True
                urgent_notice=copy.URGENT_NOTICE

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
            description=(f'产品型号：T2080；原始诉求：{original}；症状：{state.symptom or "待确认"}；'
                         f'用户急迫表达：{"有（用户明确表示着急）" if state.urgent else "无"}；'
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
            consent=_explicit_consent(message)
            if not consent and message.strip():
                await emit({'type':'stage','stage':'understanding'})
                try:
                    understood=await self.understanding.interpret(message,state)
                except Exception:
                    return await respond('degraded',copy.DEGRADED_UNDERSTANDING)
                capture_urgency(understood)
                consent=understood.get('ticket_consent') is True or understood.get('human_request') is True
            if not consent:
                return await respond('confirm_ticket',copy.CONFIRM_TICKET_ASK)
            return await create_ticket()

        affirmative_confirmation=(
            state.awaiting=='product'
            and state.pending_model_candidate=='T2080'
            and _affirm_product(message)
        )
        if affirmative_confirmation:
            understood={}
        elif message.strip():
            await emit({'type':'stage','stage':'understanding'})
            try:
                understood=await self.understanding.interpret(message,state)
            except Exception:
                return await respond('degraded',copy.DEGRADED_UNDERSTANDING)
            capture_urgency(understood)
        else:
            understood={}

        if state.symptom is None:
            state.symptom=understood.get('symptom_candidate')

        if not message.strip() and (observation is not None or image_failed) and state.symptom is None:
            state.pending_model_candidate=(
                'T2080' if observation is not None and observation.candidate_model=='T2080' else None
            )
            state.awaiting='symptom'
            if observation is not None and observation.candidate_model=='T2080':
                seen='、'.join((observation.visible_text+observation.visible_clues)[:3])
                if seen:
                    return await respond('ask_symptom',copy.ask_symptom_with_clues(seen))
            return await respond('ask_symptom',copy.ASK_SYMPTOM_IMAGE_ONLY)

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
                    reply=copy.CLARIFY_PRODUCT_T2080
                elif state.visual_observation and state.visual_observation.get('candidate_model')=='unknown':
                    reply=copy.CLARIFY_PRODUCT_IMAGE_UNKNOWN
                else:
                    reply=copy.CLARIFY_PRODUCT_GENERIC
                return await respond('clarify_product',reply)
            state.confirmed_model='T2080'
            state.pending_model_candidate=None
            state.awaiting=None

        if state.ticket_no is None and understood.get('human_request'):
            state.awaiting='ticket_confirmation'
            return await respond('confirm_ticket',copy.CONFIRM_TICKET_HUMAN_REQUEST)

        if state.awaiting is None and state.ticket_no is None and understood.get('ticket_consent'):
            return await create_ticket()

        if state.symptom is None:
            state.symptom=understood.get('symptom_candidate')
        if state.symptom is None and (state.visual_observation is not None or had_awaiting_symptom):
            state.awaiting='symptom'
            return await respond('ask_symptom',copy.ASK_SYMPTOM)
        if state.symptom!='吸力变弱':
            state.awaiting=None
            return await respond(
                'no_answer',copy.NO_ANSWER,
                source={'title':'eufy：解决技术问题需要提供的信息',
                        'url':'https://service.eufy.com/eu/article-description/What-information-does-eufy-need-to-resolve-a-technical-issue-with-my-S1-Pro'},
            )

        if state.awaiting=='feedback':
            feedback=understood.get('feedback')
            if feedback=='resolved' or (feedback is None and any(word in message for word in ('好了','恢复','正常','解决'))):
                state.feedback='已恢复正常';state.awaiting=None
                return await respond('resolved',copy.RESOLVED,
                                     source=state.sources[-1] if state.sources else None)
            if feedback!='unresolved' and not any(word in message for word in ('不行','没用','无效','仍然','还是')):
                return await respond('ask_feedback',copy.ASK_FEEDBACK_RETRY)
            state.feedback=message
            if state.suggested_steps:
                latest=state.suggested_steps[-1]
                if latest not in state.completed_steps:state.completed_steps.append(latest)

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
            return await respond('degraded',copy.DEGRADED_SEARCH)
        items=result['data']['items']
        steps=[item for item in items if any(word in item['answer'] for word in ('侧刷','滚刷','胶条','密封'))]
        steps.sort(key=lambda item:0 if '侧刷' in item['answer'] else 1)
        next_step=next((item for item in steps if item['answer'] not in state.suggested_steps),None)
        if next_step is None:
            state.awaiting='ticket_confirmation'
            return await respond('confirm_ticket',copy.CONFIRM_TICKET_STEPS_EXHAUSTED)
        first_step=not state.suggested_steps
        state.suggested_steps.append(next_step['answer'])
        state.awaiting='feedback'
        source={'title':next_step['source_title'],'url':next_step['source_url']}
        if source not in state.sources:state.sources.append(source)
        await emit({'type':'search_done','title':next_step['source_title'],'url':next_step['source_url']})
        return await respond('ask_feedback',copy.suggest_step(next_step['answer'],first_step),source=source)

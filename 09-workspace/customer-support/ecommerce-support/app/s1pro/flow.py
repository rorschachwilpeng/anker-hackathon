import re

from app.tools.business import build_s1pro_tools
from app.tools.executor import ToolExecutor
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
    ):
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
        image_failure_notice='图片暂时没能读出来，先按文字继续。' if image_failed else ''

        async def respond(action,reply,source=None,ticket_no=None,ticket_url=None):
            if image_failure_notice:
                reply=f'{image_failure_notice}{reply}'
            await self.repo.save_s1pro_state(conversation_id,state)
            await self.repo.append_message(conversation_id,'assistant',reply)
            progress={'product':'eufy S1 Pro (T2080)' if state.confirmed_model else None,
                      'symptom':state.symptom,
                      'suggested':len(state.suggested_steps),
                      'tried':len(state.completed_steps),
                      'sources':[item['title'] for item in state.sources],
                      'awaiting':state.awaiting,
                      'ticket_no':state.ticket_no}
            return {'conversation_id':conversation_id,'action':action,'reply':reply,
                    'source':source,'ticket_no':ticket_no,'tool_calls':tool_calls,
                    'progress':progress,'ticket_url':ticket_url}

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
                         f'已建议步骤：{"、".join(state.suggested_steps) or "无"}；'
                         f'已尝试步骤：{"、".join(state.completed_steps) or "未反馈"}；'
                         f'用户反馈：{state.feedback or "未提供"}；'
                         f'{visual_summary}'
                         f'资料来源：{"、".join(source["url"] for source in state.sources) or "无"}；'
                         '未解决事项：吸力问题待人工核实。')
            try:
                await self.repo.create_ticket_once(number,conversation_id,description[:2000],'售后')
            except Exception:
                return await respond('ticket_failed','工单尚未创建成功，请稍后重试；已保留当前排障信息。')
            state.awaiting=None
            return await respond('ticket_created',
                                 f'已创建售后工单 {number}（本地演示记录，未提交给安克客服），排障摘要已随单保存。',
                                 ticket_no=number,ticket_url=f'/s1pro/tickets/{number}')

        if state.ticket_no and state.awaiting is None:
            return await respond('ticket_created',
                                 f'工单 {state.ticket_no} 已创建（本地演示记录），请等待人工跟进。',
                                 ticket_no=state.ticket_no,ticket_url=f'/s1pro/tickets/{state.ticket_no}')

        if state.awaiting=='ticket_confirmation':
            consent=_explicit_consent(message)
            if not consent and message.strip():
                try:
                    understood=await self.understanding.interpret(message,state)
                except Exception:
                    return await respond('degraded','我暂时无法可靠理解这条信息，请再明确描述产品或反馈。')
                consent=understood.get('ticket_consent') is True
            if not consent:
                return await respond('confirm_ticket','需要人工跟进吗？如同意，请明确回复“可以建单”。')
            return await create_ticket()

        affirmative_confirmation=(
            state.awaiting=='product'
            and state.pending_model_candidate=='T2080'
            and _affirm_product(message)
        )
        if affirmative_confirmation:
            understood={}
        elif message.strip():
            try:
                understood=await self.understanding.interpret(message,state)
            except Exception:
                return await respond('degraded','我暂时无法可靠理解这条信息，请再明确描述产品或反馈。')
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
                    return await respond(
                        'ask_symptom',
                        f'照片里能看到「{seen}」，像是 eufy S1 Pro（T2080）扫地机器人。'
                        '如果是这款产品，请回复确认；另外请告诉我遇到了什么故障现象。',
                    )
            return await respond('ask_symptom','请用文字告诉我产品型号和遇到的故障现象。')

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
            return await respond(
                'clarify_product',
                '图片线索与文字描述不一致，请用文字完整确认产品型号；确认前我不会查询型号专属排障资料。',
            )

        if observation is not None and visual_conflict:
            state.confirmed_model=None
            state.pending_model_candidate=None
            state.awaiting='product'
            return await respond(
                'clarify_product',
                '我无法从图片确认这是目标扫地机器人，请用文字说明完整产品型号；确认前我不会查询型号专属排障资料。',
            )

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
                    reply='请确认：您说的是 eufy Robot Vacuum Omni S1 Pro 扫地机器人吗？'
                elif state.visual_observation and state.visual_observation.get('candidate_model')=='unknown':
                    reply='图片无法可靠确认产品型号，请补拍清晰标签或用文字说明完整产品型号和类型；确认前我不会查询型号专属排障资料。'
                else:
                    reply='请用文字说明完整产品型号和类型；确认前我不会查询型号专属排障资料。'
                return await respond('clarify_product',reply)
            state.confirmed_model='T2080'
            state.pending_model_candidate=None
            state.awaiting=None

        if state.awaiting is None and state.ticket_no is None and understood.get('ticket_consent'):
            return await create_ticket()

        if state.symptom is None:
            state.symptom=understood.get('symptom_candidate')
        if state.symptom is None and (state.visual_observation is not None or had_awaiting_symptom):
            state.awaiting='symptom'
            return await respond('ask_symptom','请告诉我目前遇到的故障现象。')
        if state.symptom!='吸力变弱':
            state.awaiting=None
            return await respond(
                'no_answer',
                '我目前只核实了“吸力变弱”这一类问题的官方排障资料，您描述的情况没有可靠依据，不能凭猜测指导操作。'
                '您可以补充说明故障现象或拍照给我看；也可以按官方指引准备信息联系 eufy 支持（见下方链接）；'
                '需要我创建工单交给人工跟进的话，回复“可以建单”。',
                source={'title':'eufy：解决技术问题需要提供的信息',
                        'url':'https://service.eufy.com/eu/article-description/What-information-does-eufy-need-to-resolve-a-technical-issue-with-my-S1-Pro'},
            )

        if state.awaiting=='feedback':
            feedback=understood.get('feedback')
            if feedback=='resolved' or (feedback is None and any(word in message for word in ('好了','恢复','正常','解决'))):
                state.feedback='已恢复正常';state.awaiting=None
                return await respond('resolved',
                                     '很高兴吸力恢复正常。建议每 1-2 周清理一次滚刷、尘盒和过滤器，可以减少复发（依据见下方来源）。'
                                     '如果问题再次出现，直接告诉我"吸力又不行了"，我们会接着这次的记录继续。',
                                     source=state.sources[-1] if state.sources else None)
            if feedback!='unresolved' and not any(word in message for word in ('不行','没用','无效','仍然','还是')):
                return await respond('ask_feedback','请告诉我刚才的步骤是否让吸力恢复？')
            state.feedback=message
            if state.suggested_steps:
                latest=state.suggested_steps[-1]
                if latest not in state.completed_steps:state.completed_steps.append(latest)

        tools=build_s1pro_tools(self.knowledge_search)
        executor=ToolExecutor(tools,timeout_seconds=20)
        result=None
        tool_calls=1
        async for event in executor.execute({'id':'s1pro-search','name':'search_support_knowledge',
                                             'args':{'query':'S1 Pro 吸力变弱 清理侧刷滚刷尘盒过滤器 检查胶条密封',
                                                     'confirmed_model':'T2080'}}):
            if 'result' in event.data:result=event.data['result']
        if result is None or not result['ok']:
            state.awaiting='ticket_confirmation'
            return await respond('degraded','目前无法核实官方排障依据。需要人工跟进的话，请回复“可以建单”。')
        items=result['data']['items']
        steps=[item for item in items if any(word in item['answer'] for word in ('侧刷','滚刷','胶条','密封'))]
        steps.sort(key=lambda item:0 if '侧刷' in item['answer'] else 1)
        next_step=next((item for item in steps if item['answer'] not in state.suggested_steps),None)
        if next_step is None:
            state.awaiting='ticket_confirmation'
            return await respond('confirm_ticket','官方步骤已尝试但问题仍未解决。需要我创建工单交给人工跟进吗？请明确回复“可以建单”。')
        state.suggested_steps.append(next_step['answer'])
        state.awaiting='feedback'
        source={'title':next_step['source_title'],'url':next_step['source_url']}
        if source not in state.sources:state.sources.append(source)
        return await respond('ask_feedback',f'先试一步：{next_step["answer"]} 完成后请告诉我吸力是否恢复。',source=source)

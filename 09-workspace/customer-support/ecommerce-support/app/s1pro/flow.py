from app.tools.business import build_s1pro_tools
from app.tools.executor import ToolExecutor


def _explicit_product(message):
    lowered=message.casefold()
    return 's1 pro' in lowered and 'eufy' in lowered and ('扫地机器人' in message or 'robot vacuum' in lowered)


def _explicit_consent(message):
    return any(phrase in message for phrase in ('可以建单','同意建单','确认创建工单','请创建工单'))


class S1ProFlow:
    def __init__(self,repo,understanding,knowledge_search):
        self.repo=repo;self.understanding=understanding;self.knowledge_search=knowledge_search

    async def handle(self,message,conversation_id=None):
        if conversation_id is None:
            conversation_id=await self.repo.new_conversation('local-demo')
        else:
            row=await self.repo.get_conversation(conversation_id)
            if not row or row['user_id']!='local-demo':
                raise LookupError('conversation_not_found')
        state=await self.repo.load_s1pro_state(conversation_id)
        await self.repo.append_message(conversation_id,'user',message)

        async def respond(action,reply,source=None,ticket_no=None):
            await self.repo.save_s1pro_state(conversation_id,state)
            await self.repo.append_message(conversation_id,'assistant',reply)
            return {'conversation_id':conversation_id,'action':action,'reply':reply,
                    'source':source,'ticket_no':ticket_no}

        if state.ticket_no and state.awaiting is None:
            return await respond('ticket_created',f'工单 {state.ticket_no} 已创建，请等待人工跟进。',ticket_no=state.ticket_no)

        try:
            understood=await self.understanding.interpret(message,state)
        except Exception:
            return await respond('degraded','我暂时无法可靠理解这条信息，请再明确描述产品或反馈。')

        if state.awaiting=='ticket_confirmation':
            if not (_explicit_consent(message) and understood.get('ticket_consent')):
                return await respond('confirm_ticket','需要人工跟进吗？如同意，请明确回复“可以建单”。')
            number=await self.repo.reserve_s1pro_ticket(conversation_id)
            state.ticket_no=number
            history=await self.repo.load_messages(conversation_id)
            original=next((row['content'] for row in history if row['role']=='user'),message)
            description=(f'产品型号：T2080；原始诉求：{original}；症状：{state.symptom or "待确认"}；'
                         f'已建议步骤：{"、".join(state.suggested_steps) or "无"}；'
                         f'已尝试步骤：{"、".join(state.completed_steps) or "未反馈"}；'
                         f'用户反馈：{state.feedback or "未提供"}；未解决事项：吸力问题待人工核实。')
            try:
                await self.repo.create_ticket_once(number,conversation_id,description[:2000],'售后')
            except Exception:
                return await respond('degraded','工单尚未创建成功，请稍后重试；已保留当前排障信息。')
            state.awaiting=None
            return await respond('ticket_created',f'已创建售后工单 {number}，人工可查看排障摘要。',ticket_no=number)

        if state.confirmed_model is None:
            if not (_explicit_product(message) and understood.get('product_candidate')=='T2080'):
                state.awaiting='product'
                state.symptom=state.symptom or understood.get('symptom_candidate')
                return await respond('clarify_product','请确认：您说的是 eufy Robot Vacuum Omni S1 Pro 扫地机器人吗？')
            state.confirmed_model='T2080'
            state.symptom=state.symptom or understood.get('symptom_candidate') or '吸力变弱'

        if state.awaiting=='feedback':
            if understood.get('feedback')=='resolved' and any(word in message for word in ('好了','恢复','正常','解决')):
                state.feedback='已恢复正常';state.awaiting=None
                return await respond('resolved','很高兴吸力已恢复。本次排查已记录，无需创建工单。')
            if understood.get('feedback')!='unresolved' or not any(word in message for word in ('不行','没用','无效','仍然','还是')):
                return await respond('ask_feedback','请告诉我刚才的步骤是否让吸力恢复？')
            state.feedback=message
            if state.suggested_steps:
                latest=state.suggested_steps[-1]
                if latest not in state.completed_steps:state.completed_steps.append(latest)

        tools=build_s1pro_tools(self.knowledge_search)
        executor=ToolExecutor(tools,timeout_seconds=20)
        result=None
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
        return await respond('ask_feedback',f'先试一步：{next_step["answer"]} 完成后请告诉我吸力是否恢复。',source=source)

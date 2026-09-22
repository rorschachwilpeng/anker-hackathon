from collections.abc import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


MESSAGE_OVERHEAD = 4


class ContextBudgetExceeded(ValueError):
    """The system prompt and current user input cannot fit the configured budget."""


def _message_cost(message: BaseMessage) -> int:
    content = message.content if isinstance(message.content, str) else message.text
    return len(content.encode("utf-8")) + MESSAGE_OVERHEAD


def trim_context(
    system: str,
    history: Sequence[BaseMessage],
    current: str,
    budget: int,
) -> list[BaseMessage]:
    """Estimate conservatively with UTF-8 bytes and remove oldest whole turns."""
    if len(history) % 2:
        raise ValueError("history must contain complete user/assistant pairs")
    for index, message in enumerate(history):
        expected = HumanMessage if index % 2 == 0 else AIMessage
        if not isinstance(message, expected):
            raise ValueError("history roles must alternate user and assistant")

    system_message = SystemMessage(system)
    current_message = HumanMessage(current)
    required = _message_cost(system_message) + _message_cost(current_message)
    if required > budget:
        raise ContextBudgetExceeded()

    kept = list(history)
    total = required + sum(_message_cost(message) for message in kept)
    while total > budget and kept:
        removed = kept[:2]
        del kept[:2]
        total -= sum(_message_cost(message) for message in removed)

    return [system_message, *kept, current_message]


def message_bytes(message):
    import json
    return len(json.dumps(message.model_dump(exclude_none=True),ensure_ascii=False,default=str).encode())+MESSAGE_OVERHEAD


def complete_turns(rows):
    """Only replay rounds with a final answer and fully paired tool calls."""
    import json
    from langchain_core.messages import ToolMessage
    groups=[]; current=[]
    for row in rows:
        if row['role']=='user':
            if current:groups.append(current)
            current=[]
        if current or row['role']=='user':current.append(row)
    if current:groups.append(current)
    turns=[]
    for group in groups:
        if group[-1]['role']!='assistant' or group[-1].get('tool_calls') or not group[-1].get('content'):continue
        pending=set(); messages=[]; valid=True
        try:
            for row in group:
                role=row['role']
                if role=='user':messages.append(HumanMessage(row['content']))
                elif role=='assistant':
                    calls=[]
                    for c in row.get('tool_calls') or []:
                        if c['id'] in pending:raise ValueError('duplicate id')
                        pending.add(c['id'])
                        calls.append({'id':c['id'],'name':c['function']['name'],'args':json.loads(c['function']['arguments']),'type':'tool_call'})
                    messages.append(AIMessage(row.get('content') or '',tool_calls=calls))
                else:
                    ident=row['tool_call_id']
                    if ident not in pending:raise ValueError('orphan tool result')
                    pending.remove(ident)
                    messages.append(ToolMessage(content=row['content'],tool_call_id=ident))
        except (KeyError,ValueError,TypeError):valid=False
        if valid and not pending:turns.append(messages)
    return turns


def trim_turns(system,turns,current_messages,budget,tool_schema_bytes=0):
    required=[SystemMessage(system),*current_messages]
    cost=sum(message_bytes(m) for m in required)+tool_schema_bytes
    if cost>budget:raise ContextBudgetExceeded()
    kept=[]
    for turn in reversed(turns):
        size=sum(message_bytes(m) for m in turn)
        if cost+size>budget:break
        kept[:0]=turn;cost+=size
    return [required[0],*kept,*current_messages]

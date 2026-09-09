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

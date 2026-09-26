from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.context import ContextBudgetExceeded, trim_context


def test_exact_ascii_budget_keeps_all_complete_pairs() -> None:
    history = [HumanMessage("u"), AIMessage("a"), HumanMessage("v"), AIMessage("b")]

    messages = trim_context("S", history, "Q", budget=30)

    assert messages == [SystemMessage("S"), *history, HumanMessage("Q")]


def test_one_unit_under_ascii_boundary_drops_oldest_whole_pair() -> None:
    history = [HumanMessage("u"), AIMessage("a"), HumanMessage("v"), AIMessage("b")]

    messages = trim_context("S", history, "Q", budget=29)

    assert messages == [SystemMessage("S"), HumanMessage("v"), AIMessage("b"), HumanMessage("Q")]


def test_chinese_utf8_boundary_counts_each_byte_conservatively() -> None:
    history = [HumanMessage("你"), AIMessage("好"), HumanMessage("换"), AIMessage("货")]

    exact = trim_context("S", history, "Q", budget=38)
    trimmed = trim_context("S", history, "Q", budget=37)

    assert len(exact) == 6
    assert trimmed == [SystemMessage("S"), HumanMessage("换"), AIMessage("货"), HumanMessage("Q")]


def test_system_and_current_are_never_removed() -> None:
    history = [HumanMessage("old"), AIMessage("reply")]

    messages = trim_context("S", history, "Q", budget=10)

    assert messages == [SystemMessage("S"), HumanMessage("Q")]


def test_required_system_and_current_over_budget_is_rejected() -> None:
    with pytest.raises(ContextBudgetExceeded):
        trim_context("S", [], "Q", budget=9)


def test_context_trimming_does_not_mutate_input_history() -> None:
    history = [HumanMessage("old"), AIMessage("reply")]
    original = deepcopy(history)

    trim_context("S", history, "Q", budget=10)

    assert history == original

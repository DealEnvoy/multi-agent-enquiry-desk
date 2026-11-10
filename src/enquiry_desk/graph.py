"""Wire the supervisor and the workers into one LangGraph."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, RetryPolicy

from enquiry_desk.errors import TransientModelError
from enquiry_desk.models import AgentModel
from enquiry_desk.nodes import (
    DeskRuntime,
    approval_node,
    handle_agent_error,
    make_drafter,
    make_finalize,
    make_research,
    make_reviewer,
    make_router,
    make_supervisor,
)
from enquiry_desk.state import EnquiryState
from enquiry_desk.tools import Handler, make_registry
from enquiry_desk.workflow import (
    DEFAULT_JSON_REPAIRS,
    DEFAULT_MAX_REVIEW_ROUNDS,
    DEFAULT_MODEL_ATTEMPTS,
    DEFAULT_TOOL_ATTEMPTS,
    DEFAULT_TOOL_ROUNDS,
)

ToolWrapper = Callable[[Handler], Handler]


def build_graph(
    model: AgentModel,
    *,
    log_dir: Path | str | None = None,
    checkpointer=None,
    max_review_rounds: int = DEFAULT_MAX_REVIEW_ROUNDS,
    max_json_repairs: int = DEFAULT_JSON_REPAIRS,
    max_tool_rounds: int = DEFAULT_TOOL_ROUNDS,
    max_tool_attempts: int = DEFAULT_TOOL_ATTEMPTS,
    tool_wrappers: dict[str, ToolWrapper] | None = None,
):
    """Compile the desk.

    A checkpointer is required because approval uses interrupt(). The default
    saver is in memory, so a pause lives only inside this process.
    """
    directory = Path(log_dir) if log_dir is not None else None
    runtime = DeskRuntime(
        model=model,
        tools=make_registry(directory, wrappers=tool_wrappers, max_attempts=max_tool_attempts),
        max_json_repairs=max_json_repairs,
        max_tool_rounds=max_tool_rounds,
    )
    graph = StateGraph(EnquiryState)
    retry = RetryPolicy(
        max_attempts=DEFAULT_MODEL_ATTEMPTS,
        initial_interval=0,
        backoff_factor=1,
        max_interval=0,
        jitter=False,
        retry_on=TransientModelError,
    )
    graph.add_node("supervisor", make_supervisor(max_review_rounds))
    workers = (
        ("router", make_router(runtime)),
        ("research", make_research(runtime)),
        ("drafter", make_drafter(runtime)),
        ("reviewer", make_reviewer(runtime)),
    )
    for name, node in workers:
        graph.add_node(name, node, retry_policy=retry, error_handler=handle_agent_error)
    graph.add_node("approval", approval_node)
    graph.add_node("finalize", make_finalize(runtime))

    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor",
        lambda state: state["next_agent"],
        {
            "router": "router",
            "research": "research",
            "drafter": "drafter",
            "reviewer": "reviewer",
            "approval": "approval",
            "finalize": "finalize",
        },
    )
    for name, _node in workers:
        graph.add_edge(name, "supervisor")
    graph.add_edge("approval", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


def thread_config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}


def invoke_until_pause(graph, state: EnquiryState, *, thread_id: str):
    config = thread_config(thread_id)
    graph.invoke(state, config)
    return config, graph.get_state(config)


def resume(graph, config: dict[str, Any], decision: dict[str, str]):
    graph.invoke(Command(resume=decision), config)
    return graph.get_state(config)

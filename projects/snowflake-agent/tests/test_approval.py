"""承認フロー(human-in-the-loop)のテスト。

LLM も Snowflake も使わず、agent ノードだけ偽物に差し替えたグラフで
interrupt(一時停止)→ Command(resume=...)(再開)の一連の動きを検証する。
approve_node と route_after_agent は本物を使う。
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command

from snowflake_agent.agent import approve_node, route_after_agent


@tool
def run_query(sql: str) -> str:
    """テスト用のダミー実行ツール。"""
    return f"RESULT of {sql}"


def _build_test_app(fake_agent):
    graph = StateGraph(MessagesState)
    graph.add_node("agent", fake_agent)
    graph.add_node("approve", approve_node)
    graph.add_node("tools", ToolNode([run_query]))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", route_after_agent, {"approve": "approve", "tools": "tools", END: END}
    )
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=MemorySaver())


def _fake_agent_factory(counter: dict):
    """1 回目は run_query を要求し、2 回目は最終回答を返す偽 agent。"""

    def fake_agent(state: MessagesState) -> dict:
        counter["calls"] += 1
        if counter["calls"] == 1:
            message = AIMessage(
                content="",
                tool_calls=[{"id": "t1", "name": "run_query", "args": {"sql": "SELECT 1"}}],
            )
        else:
            message = AIMessage(content="最終回答です")
        return {"messages": [message]}

    return fake_agent


def _run_until_interrupt(app, config):
    updates = list(
        app.stream({"messages": [HumanMessage("質問")]}, config, stream_mode="updates")
    )
    interrupts = [u["__interrupt__"][0] for u in updates if "__interrupt__" in u]
    assert len(interrupts) == 1, "run_query の前に一時停止すること"
    return interrupts[0]


def test_interrupt_carries_sql_for_review():
    counter = {"calls": 0}
    app = _build_test_app(_fake_agent_factory(counter))
    config = {"configurable": {"thread_id": "t1"}}

    intr = _run_until_interrupt(app, config)
    # 一時停止時点で、人間がレビューすべき SQL が渡ってくる
    assert intr.value["sqls"] == ["SELECT 1"]
    # まだツールは実行されていない(承認待ち)
    messages = app.get_state(config).values["messages"]
    assert not any(isinstance(m, ToolMessage) for m in messages)


def test_approved_resumes_and_executes_tool():
    counter = {"calls": 0}
    app = _build_test_app(_fake_agent_factory(counter))
    config = {"configurable": {"thread_id": "t2"}}
    _run_until_interrupt(app, config)

    # 承認して再開 → ツールが実行され、agent に戻って最終回答まで進む
    list(app.stream(Command(resume={"approved": True}), config, stream_mode="updates"))
    messages = app.get_state(config).values["messages"]
    tool_results = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_results) == 1
    assert "RESULT of SELECT 1" in str(tool_results[0].content)
    assert messages[-1].content == "最終回答です"


def test_denied_returns_reason_to_agent_without_executing():
    counter = {"calls": 0}
    app = _build_test_app(_fake_agent_factory(counter))
    config = {"configurable": {"thread_id": "t3"}}
    _run_until_interrupt(app, config)

    # 拒否して再開 → ツールは実行されず、拒否理由が ToolMessage として agent に渡る
    list(
        app.stream(
            Command(resume={"approved": False, "reason": "コストが心配"}),
            config,
            stream_mode="updates",
        )
    )
    messages = app.get_state(config).values["messages"]
    tool_results = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_results) == 1
    assert "許可しませんでした" in str(tool_results[0].content)
    assert "コストが心配" in str(tool_results[0].content)
    assert "RESULT" not in str(tool_results[0].content)
    # 拒否後も agent が再度呼ばれ、回答までたどり着く
    assert counter["calls"] == 2
    assert messages[-1].content == "最終回答です"


def test_metadata_tools_skip_approval():
    """list_tables 等のメタデータ調査は承認不要で tools へ直行すること。"""
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[{"id": "t1", "name": "list_tables", "args": {}}],
            )
        ]
    }
    assert route_after_agent(state) == "tools"

    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[{"id": "t1", "name": "run_query", "args": {"sql": "SELECT 1"}}],
        )
    ]
    assert route_after_agent(state) == "approve"

    state["messages"] = [AIMessage(content="最終回答")]
    assert route_after_agent(state) == END

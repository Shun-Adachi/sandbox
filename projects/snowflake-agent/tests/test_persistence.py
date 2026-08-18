"""会話履歴の永続化(SQLite チェックポインタ)のテスト。

LLM を使わない 1 ノードのグラフで、
「アプリを作り直しても(= プロセス再起動を模擬)、同じ SQLite ファイル +
同じ thread_id なら会話の続きから再開できる」ことを検証する。
"""

import sqlite3

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, MessagesState, StateGraph


def _build_app(db_path):
    def echo_agent(state: MessagesState) -> dict:
        n = len(state["messages"])
        return {"messages": [AIMessage(f"応答{n}")]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", echo_agent)
    graph.add_edge(START, "agent")
    graph.add_edge("agent", END)
    saver = SqliteSaver(sqlite3.connect(db_path, check_same_thread=False))
    return graph.compile(checkpointer=saver)


def test_conversation_survives_restart(tmp_path):
    db = tmp_path / "conv.sqlite"
    config = {"configurable": {"thread_id": "t1"}}

    # 1 回目の「プロセス」: 質問を 1 つ処理
    app1 = _build_app(db)
    app1.invoke({"messages": [HumanMessage("最初の質問")]}, config)

    # 2 回目の「プロセス」: アプリを作り直しても状態が残っている
    app2 = _build_app(db)
    restored = app2.get_state(config).values["messages"]
    assert [type(m).__name__ for m in restored] == ["HumanMessage", "AIMessage"]
    assert restored[0].content == "最初の質問"

    # 続きの質問は履歴に追記される(会話が伸びる)
    app2.invoke({"messages": [HumanMessage("追加の質問")]}, config)
    messages = app2.get_state(config).values["messages"]
    assert len(messages) == 4
    assert messages[2].content == "追加の質問"


def test_threads_are_isolated(tmp_path):
    db = tmp_path / "conv.sqlite"
    app = _build_app(db)
    app.invoke({"messages": [HumanMessage("会話A")]}, {"configurable": {"thread_id": "a"}})
    app.invoke({"messages": [HumanMessage("会話B")]}, {"configurable": {"thread_id": "b"}})

    a = app.get_state({"configurable": {"thread_id": "a"}}).values["messages"]
    b = app.get_state({"configurable": {"thread_id": "b"}}).values["messages"]
    assert a[0].content == "会話A" and len(a) == 2
    assert b[0].content == "会話B" and len(b) == 2

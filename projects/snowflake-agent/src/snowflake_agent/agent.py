"""LangGraph による Agent ループ(ReAct 型)。

このファイルがこの PoC の中核。LangGraph の「グラフ」でエージェントを組み立てる。

グラフの形(ノード = 処理、エッジ = 次にどこへ進むか):

    START ──→ [agent] ──(ツールを使いたい)──→ [tools] ──┐
                 │  ↑                                    │
                 │  └────────────────────────────────────┘
                 └──(ツール不要 = 最終回答)──→ END

- [agent] ノード: Claude を 1 回呼ぶ。Claude は「ツールを使う」か「回答を書く」かを自分で決める
- [tools] ノード: Claude が要求したツール(SQL 実行など)を実際に動かし、結果を返す
- この 2 つを行き来するのが「ReAct ループ」(Reason=考える + Act=行動する の繰り返し)
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from .config import Settings
from .tools import make_tools

# システムプロンプト: Agent の役割と進め方を定義する。
# ここにテーブル定義は書かない。「必要ならツールで調べる」よう仕向けるのがこの PoC の設計。
SYSTEM_PROMPT = """\
あなたは Snowflake 上のデータを分析するアナリストです。
対象は {database}.{schema} スキーマです。

進め方:
1. テーブル構成が不明なら list_tables / get_table_schema で必要な分だけ調査する
2. run_query で SELECT を実行する(書き込みは不可。LIMIT は自動で強制される)
3. 結果の数字を根拠に、質問への分析結果を日本語で答える

回答には、結論・根拠となる数字・使用した SQL を含めること。
クエリがエラーになったらエラーメッセージを読んで修正して再試行すること。
質問がデータで答えられない場合は、その旨と代わりに分かることを答えること。
"""


def build_agent(settings: Settings) -> CompiledStateGraph:
    """グラフを組み立てて、実行可能な形にコンパイルして返す。"""

    # ツール 3 つ(list_tables / get_table_schema / run_query)を用意する
    tools = make_tools(settings)

    # Claude のクライアント。bind_tools() で「こういうツールが使えます」と
    # ツールの名前・説明・引数スキーマを Claude に教える。
    # 以降、Claude は応答の中で「このツールをこの引数で呼びたい」(tool_calls)を返せるようになる。
    llm = ChatAnthropic(model=settings.model_id, max_tokens=4096)
    llm_with_tools = llm.bind_tools(tools)

    system = SystemMessage(
        SYSTEM_PROMPT.format(database=settings.database, schema=settings.schema)
    )

    # --- ノード 1: agent(Claude を 1 回呼ぶ) -------------------------------
    # LangGraph では「状態(state)」がグラフ内を流れる。MessagesState は
    # {"messages": [これまでの会話メッセージのリスト]} という形の状態で、
    # ノードが {"messages": [新しいメッセージ]} を返すと、上書きではなく
    # 既存リストへの「追記」になる(MessagesState に定義された合成ルール)。
    def agent_node(state: MessagesState) -> dict:
        # システムプロンプト + これまでの会話全部を渡して Claude を呼ぶ。
        # 返ってくる response は AIMessage で、テキストだけのことも、
        # tool_calls(ツールを使いたいという要求)を含むこともある。
        response = llm_with_tools.invoke([system, *state["messages"]])
        return {"messages": [response]}

    # --- グラフの組み立て ------------------------------------------------------
    graph = StateGraph(MessagesState)

    # ノード登録。ToolNode は LangGraph 組み込みの部品で、
    # 「直前の AIMessage の tool_calls を見て、該当する Python 関数を実行し、
    #  結果を ToolMessage として messages に追加する」ところまでやってくれる。
    graph.add_node("agent", agent_node)
    graph.add_node("tools", ToolNode(tools))

    # エッジ(進行ルール)の定義:
    # 1. 開始したらまず agent へ
    graph.add_edge(START, "agent")
    # 2. agent の後は分岐。tools_condition も組み込み部品で、
    #    「最後のメッセージに tool_calls があれば "tools" へ、なければ END へ」と振り分ける。
    #    つまり "Claude がツールを使うのをやめた時" がループの終了条件。
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    # 3. ツールを実行したら、その結果を持って必ず agent に戻る(ここがループ)
    graph.add_edge("tools", "agent")

    # compile() で実行可能なオブジェクトになる。呼び出し側は
    # app.invoke(...) や app.stream(...) で動かせる(このプロジェクトでは cli.py が呼ぶ)。
    return graph.compile()

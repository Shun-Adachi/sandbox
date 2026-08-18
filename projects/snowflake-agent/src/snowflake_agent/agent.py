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

承認モード(approval=True)ではグラフが 1 ノード増える:

    START ──→ [agent] ──(run_query したい)──→ [approve] ──(許可)──→ [tools] ──┐
                 │  ↑                             │                           │
                 │  └──────(拒否: 理由を返す)──────┘←──────────────────────────┘
                 └──(ツール不要)──→ END

[approve] は interrupt() でグラフの実行を「一時停止」し、人間の判断を待つ。
これは LangGraph のチェックポイント機能(状態の保存・再開)の上に成り立っている。
"""

from __future__ import annotations

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.types import Command, interrupt

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


# --- 承認フロー用のノードとルータ(モジュールレベルに置いてテスト可能にしている) ----


def route_after_agent(state: MessagesState) -> str:
    """agent ノードの後の行き先を決める(承認モード用のルータ)。

    - ツール要求なし → END(最終回答)
    - run_query を含む → "approve"(人間の承認を挟む)
    - メタデータ調査だけ(list_tables / get_table_schema)→ "tools"(承認不要で実行)
    """
    calls = getattr(state["messages"][-1], "tool_calls", None) or []
    if not calls:
        return END
    if any(call["name"] == "run_query" for call in calls):
        return "approve"
    return "tools"


def approve_node(state: MessagesState) -> Command:
    """SQL 実行前に人間の承認を求めるノード。

    interrupt() を呼ぶと、グラフはこのノードの途中で「一時停止」する。
    - 停止時: interrupt() の引数(ここでは SQL 一覧)が呼び出し側に渡る
    - 再開時: 呼び出し側が Command(resume=値) を渡すと、その値が
      interrupt() の戻り値としてここに返ってきて、続きから実行される

    戻り値の Command は「次にどのノードへ行くか」を動的に指定する仕組み。
    固定のエッジでは書けない「結果に応じた分岐 + 状態更新」を 1 つで表せる。
    """
    last = state["messages"][-1]
    sqls = [
        call["args"].get("sql", "") for call in last.tool_calls if call["name"] == "run_query"
    ]

    # ここで一時停止。人間の判断(approved / reason)が返ってくるまで進まない
    decision = interrupt({"sqls": sqls})

    if decision.get("approved"):
        return Command(goto="tools")  # 許可 → 通常どおりツールを実行

    # 拒否 → ツールは実行しない。ただし LLM の API 仕様上、tool_calls には
    # 必ず対応する ToolMessage(結果)を返す必要があるため、
    # 「拒否された」という結果を合成して agent に差し戻す。
    reason = decision.get("reason") or "理由の指定なし"
    denied = [
        ToolMessage(
            content=(
                f"ユーザーがこの実行を許可しませんでした(理由: {reason})。"
                "理由を踏まえて方針を変えるか、実行せずに答えられる範囲で回答してください。"
            ),
            tool_call_id=call["id"],
            name=call["name"],
        )
        for call in last.tool_calls
    ]
    return Command(goto="agent", update={"messages": denied})


def build_agent(
    settings: Settings, approval: bool = False, checkpointer=None
) -> CompiledStateGraph:
    """グラフを組み立てて、実行可能な形にコンパイルして返す。

    approval=True にすると run_query の実行前に人間の承認を挟む(human-in-the-loop)。

    checkpointer は会話状態の保存先。None ならメモリ保存(プロセスが終わると消える)。
    SqliteSaver 等を渡すと会話がファイル/DB に永続化され、プロセスをまたいで
    同じ thread_id で会話を再開できる(cli.py は SQLite を渡している)。
    グラフのコードは保存先が何であれ 1 行も変わらない — 差し替え可能な設計になっている。
    """

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

    if approval:
        # --- 承認モード: agent と tools の間に approve ノードを挟む ---------------
        # 「拡張はノードとエッジの追加で表現できる」の実例。agent ノードや
        # ツール実装には一切手を入れず、グラフの配線だけが変わっている。
        graph.add_node("approve", approve_node)
        graph.add_conditional_edges(
            "agent", route_after_agent, {"approve": "approve", "tools": "tools", END: END}
        )
        # approve → tools / agent は approve_node が返す Command が動的に決めるので
        # 固定エッジは書かない
        graph.add_edge("tools", "agent")
    else:
        # --- 通常モード --------------------------------------------------------
        # 2. agent の後は分岐。tools_condition は組み込み部品で、
        #    「最後のメッセージに tool_calls があれば "tools" へ、なければ END へ」と振り分ける。
        #    つまり "Claude がツールを使うのをやめた時" がループの終了条件。
        graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
        # 3. ツールを実行したら、その結果を持って必ず agent に戻る(ここがループ)
        graph.add_edge("tools", "agent")

    # compile() で実行可能なオブジェクトになる。呼び出し側は
    # app.invoke(...) や app.stream(...) で動かせる(このプロジェクトでは cli.py が呼ぶ)。
    #
    # チェックポインタ(状態の保存先)を渡している理由は 2 つ:
    # - interrupt()(承認モードの一時停止・再開)に必須
    # - 会話の継続に使う。同じ thread_id で再度 stream() すると、保存済みの
    #   会話履歴の続きとして新しい質問が処理される(= 追質問に文脈が通じる)
    return graph.compile(checkpointer=checkpointer or MemorySaver())

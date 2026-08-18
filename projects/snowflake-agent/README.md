# snowflake-agent

## 概要

自然言語の質問を受けて Snowflake に SQL を発行し、結果を分析して日本語で返す Agent の PoC。
Claude API + LangGraph で「スキーマ調査 → SQL 生成・実行 → 結果の分析・要約」のエージェントループを実装する。

**状況: 完了**(計画は [docs/plan.md](docs/plan.md)、実環境での実行記録は [docs/demo-runs.md](docs/demo-runs.md))

## 技術スタック

- Python 3.11+
- Claude API(`claude-opus-5`、`langchain-anthropic` 経由)
- LangGraph(エージェントループ・状態管理)
- Snowflake(`snowflake-connector-python`、サンプルデータ `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`)
- sqlglot(SQL バリデーション)

## 動かし方

```bash
cd projects/snowflake-agent

python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

cp .env.example .env   # ANTHROPIC_API_KEY と Snowflake 接続情報を記入

.venv/bin/snowflake-agent --check   # Snowflake 疎通チェック
.venv/bin/snowflake-agent "顧客セグメント別の売上上位5件を分析して"

# SQL 実行前に人間の承認を挟むモード(human-in-the-loop)
.venv/bin/snowflake-agent --approve "注文を一度もしていない顧客は何人いますか?"

# 対象 DB / スキーマを切り替える(自作 DB の分析など。.env の値を上書き)
.venv/bin/snowflake-agent --schema TPCH_SF10 "注文件数は?"
.venv/bin/snowflake-agent --database MY_DB --schema PUBLIC "売上を集計して"
```

回答の後は**追質問を受け付ける**(会話履歴が保持されるので「では 2 位は?」のような
文脈前提の質問が通る)。終了は Enter のみ、または `exit`。

会話履歴は SQLite(`data/conversations.sqlite`、git 管理外)に永続化される。
CLI を終了しても消えず、続きから再開できる:

```bash
.venv/bin/snowflake-agent --threads                          # 保存済みの会話一覧
.venv/bin/snowflake-agent --show <会話ID>                     # 保存された会話の中身を表示
.venv/bin/snowflake-agent --thread <会話ID> "それは前年比では?"  # 続きから再開
```

実行すると AI とのやり取り全文(システムプロンプト・ツール呼び出し・ツール結果・
トークン使用量)が `runs/`(git 管理外)に Markdown で保存される(`--no-log` で無効化)。

テスト(Snowflake 接続不要):

```bash
.venv/bin/python -m pytest
```

前提: Snowflake アカウント(トライアル可)。サンプルデータ `SNOWFLAKE_SAMPLE_DATA` は
全アカウントに共有されているため、追加のデータ投入は不要。

## 工夫した点・設計判断

- **LangGraph 採用の理由**: Claude API と組み合わせる場合、OpenAI Agents SDK は LiteLLM 等の
  互換レイヤーが必要になる。LangGraph は `langchain-anthropic` で直結でき、グラフ(ノード/エッジ)で
  エージェントの制御フローを明示できるため、設計を示す PoC に向く。
- **SQL 実行の安全弁は許可リスト方式**: sqlglot でパースし、SELECT 単文のみ許可。
  構文木を走査して書き込み・DDL ノードを遮断し、LIMIT を強制(未指定なら付与、超過なら丸め)。
  タイムアウトはセッションパラメータで DB 側にも強制する。パースできない SQL は実行しない。
- **ツールは 3 つに絞る**: `list_tables` / `get_table_schema` / `run_query`。
  スキーマをプロンプトに全部埋め込まず、Agent に必要な分だけ調査させる。
- **実行トランスクリプトを毎回保存**: やり取り全文とターンごとのトークン使用量を `runs/` に
  記録し、Agent の判断過程とコストを後から検証できるようにした。
- **承認フローはグラフの拡張として実装**: `--approve` は agent と tools の間に承認ノードを
  1 つ足しただけで、Agent 本体やツール実装は無変更。LangGraph の interrupt(一時停止)+
  チェックポイント(状態保存)+ Command(動的遷移)を使用。拒否時は理由を ToolMessage として
  Agent に返し、方針転換させる(実行例: docs/demo-runs.md の実行 4)。
- **会話の継続・永続化もチェックポイントで実現**: 同じ thread_id で新しい質問を投入するだけで
  会話履歴の続きとして処理される(マルチターン)。保存先を SQLite チェックポインタにした
  ことで、プロセスを終了しても `--thread` で会話を再開できる。グラフ側のコードは
  保存先(メモリ / SQLite / 本番なら Postgres)を知らず、compile() の引数 1 つで差し替わる。

## 成果物

実環境(Snowflake トライアル)での実行記録 3 本を [docs/demo-runs.md](docs/demo-runs.md) に収録:

1. **市場セグメント別分析** — スキーマ調査 → SQL 5 本で仮説を広げ、「首位の理由は単価でなく顧客数」まで分析。データの限界(合成データの均一性、金額カラムの定義)にも言及
2. **国別売上分析** — 指示なしで LINEITEM ベースの実収売上定義に切り替え、5 テーブル JOIN・CTE・ウィンドウ関数を使用
3. **破壊的依頼への振る舞い** — 「全部削除して」に対し DELETE を試みず代替案を提示。加えてガード層が DELETE/DROP/複文を決定的に拒否することを単体デモで確認

4. **承認フロー(human-in-the-loop)** — `--approve` で SQL 実行前に一時停止し人間がレビュー。拒否理由を伝えると Agent が方針転換し、既知情報だけで概算回答(実測との誤差 0.04%)まで出した

安全性は 4 層(人間の承認(任意) / モデルの判断 / sqlglot の許可リスト検証 / 共有 DB の読み取り専用)で担保。
単体テスト 26 件(SQL ガード・トランスクリプト・承認フローの interrupt/再開)はすべてパス。

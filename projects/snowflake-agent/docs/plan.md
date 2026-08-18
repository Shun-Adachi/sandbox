# snowflake-agent 開発計画

作成日: 2026-08-17

## ゴール

「Snowflake に問い合わせて分析結果を返す Agent」の PoC を GitHub 公開できる品質で作る。
完成条件はリポジトリ共通ルール通り「クローン後、README のコマンドだけで動くこと」。

## スコープ

- 自然言語の質問 → Agent がスキーマを調査 → SELECT を生成・実行 → 結果を分析して日本語で回答
- CLI で 1 コマンド実行(`uv run python -m snowflake_agent "<質問>"`)
- 対象データ: `SNOWFLAKE_SAMPLE_DATA.TPCH_SF1`(全 Snowflake アカウントに標準共有。トライアルで再現可)

### スコープ外(PoC では作らない)

- Web UI / API サーバ化
- 会話履歴の永続化・マルチターンセッション管理
- 書き込み系操作(明示的に遮断する)

## 技術選定

| 項目 | 選定 | 理由 |
| --- | --- | --- |
| Agent フレームワーク | LangGraph | Claude API と `langchain-anthropic` で直結できる。OpenAI Agents SDK は Claude 利用に LiteLLM 等のシムが必要で構成が濁る。StateGraph で制御フローを明示でき、公開 PoC として設計を見せやすい |
| LLM | `claude-opus-5` | 現行の推奨モデル。`.env` の `MODEL_ID` で切替可能にする |
| DB 接続 | `snowflake-connector-python` | 公式コネクタ。PoC に ORM は不要 |
| パッケージ管理 | venv + pip + pyproject.toml | 既存テーマ(llm-api / prompt-eval)と同じ方式に合わせる。環境はテーマディレクトリ内で完結 |
| SQL バリデーション | sqlglot(または軽量な自前チェック) | SELECT 以外・複文を実行前に拒否する |

## アーキテクチャ

LangGraph の StateGraph によるツール呼び出しループ(ReAct 型):

```
[user question]
      ↓
  ┌─ agent ノード(Claude + tools バインド)
  │     ↓ tool_use あり?
  │   yes → tools ノード(実行結果を state に追加)──┐
  │     ↑                                            │
  │     └────────────────────────────────────────────┘
  └──→ no → 最終回答(分析結果 + 根拠にした SQL)
```

### ツール(3 つに絞る)

| ツール | 内容 |
| --- | --- |
| `list_tables` | 対象スキーマのテーブル一覧と行数 |
| `get_table_schema` | 指定テーブルのカラム定義(型・コメント) |
| `run_query` | SELECT 文を実行し結果を返す |

スキーマ全体をシステムプロンプトに埋め込まず、Agent に必要な分だけ調査させる
(実案件のように数百テーブルある環境を想定した設計を PoC でも示す)。

### 安全弁(run_query)

- sqlglot でパースし、SELECT 単文のみ許可(DML/DDL/複文/`INTO` は拒否)
- `LIMIT` 未指定なら強制付与(既定 100 行)、結果行数の上限
- クエリタイムアウト(既定 30 秒)
- README で read-only ロールでの接続を推奨

## マイルストーン

1. **M1: 疎通** — pyproject / .env.example 整備、Snowflake 接続と `SNOWFLAKE_SAMPLE_DATA` へのクエリ疎通
2. **M2: ツール実装** — 3 ツール + SQL バリデーション(ここは単体テストを書く)
3. **M3: Agent ループ** — LangGraph で StateGraph 構築、CLI エントリポイント、途中経過の表示(どの SQL を実行したか)
4. **M4: 仕上げ** — 実行例 3〜5 問を docs/ に記録、README を結果ベースに書き換え、ルート README の状況更新

各マイルストーンの作業ログは `.claude/tickets/2026-08-17-snowflake-agent.md` に記録する。

## リスク・確認事項

- **Snowflake トライアルの期限**(30 日)— デモ実行ログを早めに docs/ へ残し、期限切れ後も成果が示せる状態にする
- **API コスト** — opus は単価が高め。開発中の試行はプロンプトを短く保ち、必要なら `MODEL_ID` で切替
- **秘匿情報** — 接続情報・API キーは `.env`(git 管理外)のみ。アカウント識別子も README には書かない

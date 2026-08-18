# Sandbox

技術検証用のサンドボックス集です。
テーマごとに `projects/<テーマ名>/` ディレクトリを分けて管理しています。
各テーマは原則独立して動作し、それぞれの README のコマンドだけで動かせることを完成条件としています。
例外として他テーマを参照するテーマは、その README に依存先と参照の仕組み・影響範囲を明示しています
(例: prompt-eval は llm-api の HTTP API を評価対象とする)。

## Projects

| テーマ | 技術スタック | 状況 | 成果物 |
| --- | --- | --- | --- |
| [llm-api](projects/llm-api/) | Python, FastAPI, OpenAI API | 完了 | 構造化抽出 + ストリーミング RAG Q&A の 2 エンドポイント |
| [llm-batch-pipeline](projects/llm-batch-pipeline/) | Python, OpenAI API | 未着手 | - |
| [prompt-eval](projects/prompt-eval/) | Python, OpenAI API | 完了 | llm-api のプロンプト 3 版 × 24 ケースを採点するハーネス。実測 46%→62%→71%。人手評価 72 件で LLM-as-judge をキャリブレーション(κ 0.38→0.92、gpt-4o 版を採用) |
| [dify-workflow](projects/dify-workflow/) | Dify, Docker | 完了 | RAG・構造化抽出・エージェントの 3 ワークフロー(DSL) |
| [ts-llm-client](projects/ts-llm-client/) | TypeScript, Node.js | 未着手 | - |
| [copilot-compare](projects/copilot-compare/) | Python, GitHub Copilot, Claude Code | 進行中 | 同一課題をエージェント間で比較する採点ハーネス + 課題 2 件。Copilot 2 本 / Claude Code 1 本を計測済み |
| [snowflake-agent](projects/snowflake-agent/) | Python, Claude API, LangGraph, Snowflake | 完了 | 自然言語の質問から Snowflake に SQL を発行し分析結果を返す Agent。実環境デモ 3 本 + SQL ガード(許可リスト方式、テスト 21 件) |

## ディレクトリ構成(テーマ共通)

```
projects/<テーマ名>/
├── README.md     # 概要・技術スタック・動かし方・設計判断・成果物
├── src/          # 実装
├── docs/         # 設計メモ・スクリーンショット
└── (pyproject.toml / package.json / Dockerfile など環境は各テーマで完結)
```

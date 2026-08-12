# Sandbox

技術検証用のサンドボックス集です。
テーマごとに `projects/<テーマ名>/` ディレクトリを分けて管理しています。
各テーマは独立して動作し、それぞれの README のコマンドだけで動かせることを完成条件としています。

## Projects

| テーマ | 技術スタック | 状況 | 成果物 |
| --- | --- | --- | --- |
| [llm-api](projects/llm-api/) | Python, FastAPI, OpenAI API | 完了 | 構造化抽出 + ストリーミング RAG Q&A の 2 エンドポイント |
| [llm-batch-pipeline](projects/llm-batch-pipeline/) | Python, OpenAI API | 未着手 | - |
| [prompt-eval](projects/prompt-eval/) | Python, OpenAI API | 未着手 | - |
| [dify-workflow](projects/dify-workflow/) | Dify, Docker | 完了 | RAG・構造化抽出・エージェントの 3 ワークフロー(DSL) |
| [ts-llm-client](projects/ts-llm-client/) | TypeScript, Node.js | 未着手 | - |

## ディレクトリ構成(テーマ共通)

```
projects/<テーマ名>/
├── README.md     # 概要・技術スタック・動かし方・設計判断・成果物
├── src/          # 実装
├── docs/         # 設計メモ・スクリーンショット
└── (pyproject.toml / package.json / Dockerfile など環境は各テーマで完結)
```

# llm-api

## 概要

OpenAI API を使った AI 連携 API(FastAPI)。dify-workflow テーマで Dify 上に組んだ
2 つのワークフローを、コードで再現・拡張したもの。構造化抽出とストリーミング RAG Q&A の
2 エンドポイントを提供する。

プロンプトはコードに埋め込まず、版付きの YAML として外に出してある。
API はリクエストごとに版を選べるので、prompt-eval テーマから
「版 × 評価ケース」のマトリクスをそのまま回せる。

## 技術スタック

- Python 3.13 / FastAPI / Uvicorn
- OpenAI API(gpt-4o-mini / text-embedding-3-small)
- Pydantic v2(structured outputs のスキーマ兼レスポンス型)
- numpy(インメモリのベクトル検索)
- pytest(27 件、OpenAI を呼ばずに動く)

## 動かし方

```bash
cd projects/llm-api

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env        # OPENAI_API_KEY を書く
.venv/bin/uvicorn llm_api.main:app --app-dir src --port 8100
```

テストは OpenAI を呼ばないので、API キーが無くても通る。

```bash
.venv/bin/python -m pytest -q
```

### エンドポイント

| メソッド | パス | 内容 |
| --- | --- | --- |
| GET | `/healthz` | 疎通確認。OpenAI は呼ばない |
| GET | `/v1/prompts` | 利用可能なプロンプト版の一覧 |
| POST | `/v1/extract` | 問い合わせテキストの構造化抽出 |
| POST | `/v1/qa` | FAQ に対する RAG Q&A(`"stream": true` で SSE) |

```bash
# 構造化抽出
curl -s -X POST localhost:8100/v1/extract -H 'Content-Type: application/json' -d '{
  "text": "株式会社サンプル商事の田中と申します。昨日からTaskFlowにログインできず、全社で業務が止まっています。",
  "prompt_version": "v1"
}'

# RAG Q&A(ストリーミング)
curl -N -X POST localhost:8100/v1/qa -H 'Content-Type: application/json' \
  -d '{"question":"Proプランはいくらですか?","stream":true}'
```

`/v1/extract` の応答:

```json
{
  "ok": true,
  "data": { "name": "田中", "company": "株式会社サンプル商事",
            "category": "不具合", "urgency": "高",
            "summary": "TaskFlowにログインできず、業務が停止しています。" },
  "warnings": [],
  "prompt_version": "v1", "model": "gpt-4o-mini-2024-07-18",
  "usage": { "prompt_tokens": 634, "completion_tokens": 43, "total_tokens": 677 },
  "latency_ms": 1878
}
```

## Dify 版との対応

| このテーマ | dify-workflow 側 | コードにしたときの違い |
| --- | --- | --- |
| `POST /v1/extract` | doc-extract ワークフロー | 検証をコードノードから structured outputs + 業務ルール検証に置き換え |
| `POST /v1/qa` | rag-qa チャットフロー | Weaviate をやめて numpy のインメモリ検索に。SSE ストリーミングを追加 |

## 工夫した点・設計判断

- **プロンプトを版付きの外部ファイルにした**。`src/prompts/<用途>/<版>.yaml` に
  モデル・temperature・system・user_template をまとめ、リクエストの `prompt_version` で選ぶ。
  プロンプトをコードに埋めると、精度評価のたびにコード変更とデプロイが要る。
  評価の対象になるものを設定として外に出すのが、あとで効いてくる境界だと考えた。
  `extract` には v1(Dify と同じ指示文)・v2(判断基準を明文化)・v3(v2 + 例示)の
  3 版があり、prompt-eval テーマで突き合わせる。過去の版は上書きせずに残している。
  「どう直したら良くなったか」の履歴自体が、精度改善の根拠になるため。

- **形の保証と意味の検証を分けた**。キー欠落・列挙値の逸脱は structured outputs が
  構造的に防ぐので、Dify 版のコードノードにあったキー検証はもう要らない。
  代わりに「要約 50 字以内」のような形では表せない業務ルールだけを検証し、
  **エラーではなく `warnings` として返す**。バッチ処理の後段が
  「使えるが要確認」を仕分けできるようにするため。Dify 版の IF/ELSE による
  成功 / 失敗の二値分岐より、後段が扱いやすい形になった。

- **ベクトル DB を立てなかった**。FAQ 13 チャンク程度なら numpy のコサイン類似度で足り、
  「クローン後 README のコマンドだけで動く」という完成条件を満たせる。
  チャンクは Dify 版と同じく `###` 見出しで 1 チャンク = 1 Q&A に切っている。
  固定長で切ると質問と回答が別チャンクに割れて検索が当たらなくなるため。
  埋め込みは FAQ の内容とモデル名のハッシュをキーにディスクへキャッシュし、
  起動のたびに再取得しないようにした。

- **リトライを自前で書かなかった**。OpenAI SDK 内蔵のリトライは 429 / 5xx / 接続断に
  指数バックオフで対応し、`Retry-After` ヘッダーも尊重する。自前実装は
  ヘッダー無視やジッター無しになりがちで、レート制限をかえって悪化させる。
  このアプリが足すのは、タイムアウトとリトライ回数の一元管理だけにした。

- **上流の失敗を、呼び出し側が判断できるステータスに割り当てた**。
  レート制限は 429、タイムアウトは 504、接続不能は 502、サーバー側のキー未設定は 503。
  すべて 500 にすると「待って再試行すべき」か「入力を直すべき」かが呼び出し側に伝わらない。
  5xx の detail は上流のエラー本文を含みうるのでクライアントには返さず、ログにだけ残す。

- **ストリーミング中の失敗を error イベントで通知する**。SSE は本文を流し始めた後に
  HTTP ステータスを変えられないので、途中の失敗は `event: error` として本文に流す。
  クライアントは `done` が来なければ異常終了と判断できる。
  引用(citations)は本文より先に送り、UI が回答を描き始める前に出典を出せるようにした。

## 検証で分かったこと

- Dify 版と同じ「ログイン不可クレーム」で `category=不具合` / `urgency=高` と、同じ結果を再現できた。

- **緊急度の判定は、基準を文章で書くより例を 1 つ見せるほうが効いた。**
  緊急度を 3 ケース(業務停止 / 回避策あり・期限は来週 / 導入前の質問)で比べた結果:

  | 版 | 内容 | 正解数 |
  | --- | --- | --- |
  | v1 | Dify と同じ指示文。基準は「業務停止・データ消失は高」のみ | 1 / 3 |
  | v2 | 高・中・低の基準を文章で明文化 | 2 / 3 |
  | v3 | v2 + 例示 3 件 + 「高にしない条件」を否定形で明示 | 3 / 3 |

  v2 で基準を足しても「回避策があり期限も先」のケースは高のままで、
  不具合であること自体に緊急度が引きずられていた。例示を入れた v3 で解消した。
  ただし 3 ケースでの比較なので、傾向の確認に留まる。件数を増やした評価は prompt-eval で行った
  (24 ケースで全項目一致 46%→62%→71%。v2→v3 で urgency が微退行するなど、
  3 ケースでは見えなかった副作用も検出。詳細は `projects/prompt-eval/`)。

- **スコア閾値 0.3 は日本語ではほとんど効かない**。FAQ に無い質問(Slack 連携の可否)でも
  無関係なチャンクが 0.41 前後のスコアで残るため、閾値による足切りは発動せず、
  実際にフォールバックを効かせているのはプロンプト側の指示だった。
  閾値をいくつにすべきかは prompt-eval で測る。

## 成果物

- `src/llm_api/` — API 実装
- `src/prompts/` — 版付きプロンプト(extract v1 / v2 / v3、qa v1)
- `src/sample-data/faq-taskflow.md` — RAG 用サンプル FAQ(dify-workflow と同じもの)
- `tests/` — pytest 26 件

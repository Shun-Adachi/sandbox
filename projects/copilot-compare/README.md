# copilot-compare

## 概要

同じ課題を GitHub Copilot と Claude Code に解かせて、結果を突き合わせるための採点ハーネス。

課題は仕様書 1 枚（`spec.md`）と、エージェントには見せない隠しテストの組で定義してある。
両者に同じ仕様と同じリポジトリ指示を与え、実装だけを差し替えてテストを当てる。
「どちらが賢いか」ではなく、**同じ仕様書からどこを取りこぼすか**を見るのが目的。

課題は `llm-api` テーマで実際に踏んだ箇所から採った。
`t1` は `/v1/qa` が返す SSE のデコード、`t2` は上流エラーのリトライ判断で、
どちらも境界条件が多く、仕様書の読み落としが合否に出やすい。

## 技術スタック

- Python 3.13 / pytest（採点は junit-xml を読んで集計）
- GitHub Copilot（VS Code 拡張・エージェントモード）
- Claude Code
- リポジトリ指示: `.github/copilot-instructions.md` / `.github/instructions/*.instructions.md` / `CLAUDE.md`

## 動かし方

```bash
cd projects/copilot-compare

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# ハーネスの自己検証（参照実装が全通し、starter が 0 点になることを確認）
.venv/bin/python src/score.py --reference
```

API キーは不要。ここまでは Copilot が無くても動く。

### 1 回分を回す

```bash
# 作業ディレクトリを用意する
.venv/bin/python src/new_run.py --agent copilot --task t1-sse-parser

# → エージェントに spec.md と PROTOCOL.md だけを渡して実装させる
# → 終わったら runs/copilot/t1-sse-parser/run.json に実測値を書く

.venv/bin/python src/score.py --reference
```

採点結果は `docs/results.md` と `docs/results.json` に出る。
手順の詳細と、条件を揃えるためのルールは [PROTOCOL.md](PROTOCOL.md) を参照。

### 課題

| 課題 | 内容 | テスト数 |
| --- | --- | --- |
| `t1-sse-parser` | SSE の逐次パーサ。チャンク境界・改行コード・BOM の扱い | 33 |
| `t2-retry-policy` | 指数バックオフ + `Retry-After`（秒数 / HTTP-date）の解釈 | 50 |

## Copilot を使えるようにする

VS Code の拡張（`github.copilot` / `github.copilot-chat`）を入れて GitHub アカウントで
サインインする。`.vscode/extensions.json` に推奨として入れてあるので、
このリポジトリを開くと VS Code が導入を促す。

Copilot Free でもエージェントモードは使える（補完 2,000 回 / プレミアムリクエスト 50 回・月）。
このテーマの課題 2 つなら、その枠内で収まる。
なお GitHub は 2026-06-01 にプレミアムリクエストを AI Credits によるトークン課金へ切り替えているため、
枠の数え方は契約時点の表示を確認すること。

## 工夫した点・設計判断

- **合否を機械で決められる課題にした**。「良いコードを書けたか」は評価者の主観に落ちるので、
  仕様を隠しテストに翻訳できる題材だけを選んだ。SSE パーサもリトライ判断も、
  外部 I/O が無く、境界条件が仕様書に書き切れて、正解が一意に決まる。
  時間依存を避けるため、リトライ側は sleep せず「次に何秒待つか」を返す純粋な関数にし、
  ジッターと現在時刻は引数で注入する形にした。

- **両方のエージェントに同じ指示を読ませた**。VS Code の `chat.useClaudeMdFile` を有効にして、
  Copilot にもルートの `CLAUDE.md` を読ませている。
  比較したいのはエージェントの差であって、指示文の差ではないため。
  テーマ固有のルール（隠しテストを読まない等）は
  `.github/instructions/copilot-compare.instructions.md` に `applyTo` 付きで置き、
  `projects/copilot-compare/**` を触るときだけ効くようにした。
  内容は `PROTOCOL.md` と同じで、Claude Code 側は `PROTOCOL.md` を直接読む。

- **starter に空のシグネチャを置いた**。公開インターフェースを仕様書の文章だけで伝えると、
  引数名や戻り値の型がずれてテストがインポートすら通らず、
  「仕様を読み違えた」のか「呼び出し規約を外した」のかが混ざる。
  シグネチャを固定して、測りたい中身の差だけが出るようにした。

- **失敗したテストをエージェントに返さない**。採点は作業完了後の 1 回だけ。
  テスト結果を渡すと総当たりで通せてしまい、仕様書の読解力ではなく
  試行回数の勝負になる。実務でも、仕様書しか無い状態での初回実装の質が問題になる。

- **合格数以外に行数・所要時間・往復回数も残す**。同点になったときに差が見えないため。
  ただし後ろ 2 つは自己申告で、厳密な指標ではない。`run.json` に手で記録する。

- **隠しテストを技術的に隠せていないことは、隠さず書いた**。リポジトリが公開されている以上、
  エージェントは `tasks/*/tests/` を読めてしまう。ルールで禁じ、会話ログで確認し、
  `run.json` の `used_spec_only` に記録する運用にした。
  ハーネスの限界を README に書かないと、後から見た数字を過大評価することになる。

## 検証で分かったこと

（実行後に記入する）

## 成果物

- `src/` — 採点ハーネス（`new_run.py` で作業場所を用意、`score.py` で採点）
- `tasks/` — 課題 2 件（仕様書 / starter / 隠しテスト 83 件 / 参照実装）
- `runs/` — 各エージェントの実装と `run.json`
- `docs/results.md` — 採点結果（`score.py` が生成。手で編集しない）
- `PROTOCOL.md` — 条件を揃えるための手順

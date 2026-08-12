# 比較の進め方

エージェントを変えても条件が変わらないようにするための手順。
Claude Code と Copilot の両方に、**この文書と `tasks/<課題>/spec.md` だけ**を渡す。

## 1 回分の流れ

```bash
python src/new_run.py --agent <エージェント名> --task <課題名>
```

`runs/<エージェント名>/<課題名>/` に starter が置かれる。ここが唯一の作業場所。

エージェントへの最初の指示は、毎回この 1 文にする。

> `projects/copilot-compare/tasks/<課題名>/spec.md` の仕様を満たす実装を
> `projects/copilot-compare/runs/<エージェント名>/<課題名>/` に書いてください。
> `projects/copilot-compare/PROTOCOL.md` のルールに従ってください。

終わったら `runs/<エージェント名>/<課題名>/run.json` に実測値を記入し、採点する。

```bash
python src/score.py --reference
```

## エージェントが守るルール

1. **`tasks/<課題>/tests/` と `tasks/<課題>/reference/` を読まない。**
   採点用の隠しテストと模範解答であり、これを見ると比較が成立しない。
2. 書き換えてよいのは `runs/<エージェント名>/<課題名>/` の中だけ。
   `tasks/` 配下と他のエージェントの `runs/` は触らない。
3. 追加の依存を入れない。標準ライブラリだけで実装する。
4. 自分でテストを書くのは自由だが、同じディレクトリに置き、
   採点対象のモジュール名（`sse_parser.py` など）と衝突させない。

> リポジトリが公開されているため、1. はエージェントを技術的に縛れない。
> 破られていないかは、採点前に会話ログを見て確認し、
> `run.json` の `used_spec_only` に記録する。ここは正直さに依存した運用になっている。

## 人が守るルール

- **仕様の追加説明をしない。** spec.md が曖昧で詰まったら、それも比較結果のうち。
  質問されたら「spec.md の通りです」と返す。
- **失敗したテストを教えない。** 採点は 1 回だけ、作業が終わったあとに走らせる。
- 何往復したか、どこで詰まったかを `run.json` の `notes` に残す。
  合格数だけでは、同じ点数に至るまでの手数の差が見えない。

## run.json

```json
{
  "agent": "copilot",
  "model": "claude-sonnet-4.6",
  "mode": "agent",
  "minutes": 12,
  "turns": 3,
  "used_spec_only": true,
  "notes": "チャンク境界の \\r\\n を最初は取りこぼした"
}
```

| キー | 内容 |
| --- | --- |
| `model` | 実際に使ったモデル名 |
| `mode` | `agent` / `ask` / `edit` / `cli` など、使った動作モード |
| `minutes` | 最初の指示から実装完了までの実測分 |
| `turns` | 人が指示を出した回数（最初の 1 回を含む） |
| `used_spec_only` | `tests/` と `reference/` を見ずに済んだか |
| `notes` | 詰まった箇所・気づいたこと |

`minutes` と `turns` は自己申告で、厳密な指標ではない。
合格数の差が小さいときに「どちらが手間だったか」を思い出すための記録として使う。

## 課題を足すとき

```
tasks/<課題名>/
├── spec.md           # エージェントに渡す唯一の仕様
├── starter/          # 空のシグネチャだけ。new_run.py がここを複製する
├── tests/            # 採点用。エージェントには見せない
└── reference/        # 参照実装。ハーネスの自己検証用
```

`src/score.py --reference` が参照実装で全通しすること、
starter が 0 点になることの両方を確かめてから、課題として使う。

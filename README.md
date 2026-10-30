# lol-top-analysis

Personal data-analysis scripts for a Japanese YouTube channel that teaches League of Legends
fundamentals to Silver/Gold players (top lane). It collects ranked solo queue matches on JP1
via League-V4 and Match-V5, compares top laners' habits across rank tiers, and outputs
aggregate statistics only. Player names and Riot IDs are never published.

The API key is read from the `RIOT_API_KEY` environment variable and is never stored in this repository.

---

## 概要（日本語）

LoL トップレーン ランク帯比較分析。

Riot API からランク帯ごとのソロランク試合を集め、トップレーナーの行動を比較する。
「シルバー→ゴールド」解説動画のテーマ選びと根拠データ作りが目的。

## 準備

1. https://developer.riotgames.com で API キーを取得（開発用キーは24時間で失効）
2. 環境変数 `RIOT_API_KEY` に設定
3. `pip install -r requirements.txt`

## 実行

```bash
./run_all.sh                          # 既定: JPサーバー, DIAMOND/EMERALD/GOLD/SILVER 各250試合
./run_all.sh --matches-per-tier 100   # 試しに少なめで
```

個別に動かす場合:

| スクリプト | 役割 | 出力 |
|---|---|---|
| `collect.py` | 試合とタイムラインを取得（中断しても再開可） | `data/index.csv`, `data/cache/` |
| `extract.py` | トップレーナー1人=1行の指標を計算 | `data/top_laners.csv`, `data/deaths.csv` |
| `analyze.py` | ランク帯比較・勝敗との関連・チャンピオン集計 | `output/report.md` と PNG |

所要時間の目安: 開発用キーは2分100リクエストまで。1試合=2リクエストなので 1000試合で約40分。
メタ（チャンピオン）分析だけなら `--no-timeline` で約半分になる。

## 出す指標

CS（10分・14分・20分以降）、対面とのCS/ゴールド/経験値差、14分までのデス（ガンク関与・敵陣側）、
20分以降の敵陣側デス、ワード設置数、コントロールワード、視界スコア、プレート、ソロキル、
オブジェクト参加率（グラブ/ヘラルド含む）、キル関与率。

## 注意

- ランク帯は「試合を拾ったプレイヤーのランク」で分類している。
- 対面との差分は同じ試合の2人で打ち消し合うため、ランク帯比較ではなく勝敗分析でだけ使う。
- 「勝敗との関連」は相関であり因果ではない。
- 動画でプレイヤー名を出さないこと。

## 動作確認（ダミーデータ）

```bash
python3 tests/make_fixture.py /tmp/fake/data
python3 extract.py --data-dir /tmp/fake/data
python3 analyze.py --data-dir /tmp/fake/data --out /tmp/fake/out
```
ダミーデータの数値は架空のもので、分析結果としては使えない。

## Legal

lol-top-analysis isn't endorsed by Riot Games and doesn't reflect the views or opinions of Riot Games
or anyone officially involved in producing or managing Riot Games properties. Riot Games, and all
associated properties are trademarks or registered trademarks of Riot Games, Inc.

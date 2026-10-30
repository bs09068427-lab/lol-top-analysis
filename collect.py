"""ランク帯ごとにソロランクの試合を集め、data/cache と data/index.csv に保存する。

中断しても再実行すれば続きから再開する（取得済みの試合はキャッシュを使う）。
"""
import argparse
import csv
import random
import time
from pathlib import Path

from riot_client import RiotClient

DIVISIONS = ["I", "II", "III", "IV"]
INDEX_FIELDS = ["match_id", "tier", "division", "patch"]


def patch_of(match):
    return ".".join(match["info"]["gameVersion"].split(".")[:2])


def load_index(path):
    if not path.exists():
        return []
    with path.open() as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--platform", default="jp1")
    ap.add_argument("--region", default="asia")
    ap.add_argument("--tiers", default="DIAMOND,EMERALD,GOLD,SILVER")
    ap.add_argument("--matches-per-tier", type=int, default=250)
    ap.add_argument("--matches-per-player", type=int, default=5,
                    help="1人から拾う試合数。少ないほどプレイヤーの偏りが減る")
    ap.add_argument("--patch", default=None, help="例: 15.21。省略時は最初に取れた試合のパッチに固定")
    ap.add_argument("--days", type=int, default=14, help="何日前までの試合を対象にするか")
    ap.add_argument("--no-timeline", action="store_true",
                    help="タイムラインを取らない（メタ分析だけなら半分のリクエストで済む）")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    index_path = data_dir / "index.csv"
    index = load_index(index_path)
    seen = {row["match_id"] for row in index}
    patch = args.patch or (index[0]["patch"] if index else None)

    client = RiotClient(platform=args.platform, region=args.region,
                        cache_dir=data_dir / "cache")
    start_time = time.time() - args.days * 86400
    rng = random.Random(args.seed)

    new_file = not index_path.exists()
    with index_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=INDEX_FIELDS)
        if new_file:
            writer.writeheader()

        for tier in args.tiers.split(","):
            have = sum(1 for row in index if row["tier"] == tier)
            print(f"[{tier}] 既存 {have} 試合")
            # 各ディビジョンからプレイヤーを均等に混ぜる
            players = []
            for div in DIVISIONS:
                for entry in client.league_entries(tier, div, page=1):
                    if entry.get("puuid"):
                        players.append((entry["puuid"], div))
            rng.shuffle(players)

            for puuid, div in players:
                if have >= args.matches_per_tier:
                    break
                ids = client.match_ids(puuid, count=args.matches_per_player,
                                       start_time=start_time)
                for mid in ids:
                    if mid in seen or have >= args.matches_per_tier:
                        continue
                    seen.add(mid)
                    match = client.match(mid)
                    if not match:
                        continue
                    p = patch_of(match)
                    if patch is None:
                        patch = p
                        print(f"パッチを {patch} に固定します")
                    if p != patch:
                        continue
                    if not args.no_timeline and not client.timeline(mid):
                        continue
                    row = {"match_id": mid, "tier": tier, "division": div, "patch": p}
                    writer.writerow(row)
                    f.flush()
                    index.append(row)
                    have += 1
                    if have % 25 == 0:
                        print(f"[{tier}] {have}/{args.matches_per_tier}")
            print(f"[{tier}] 完了: {have} 試合")


if __name__ == "__main__":
    main()

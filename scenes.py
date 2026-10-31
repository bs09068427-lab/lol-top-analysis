"""動画用に録画すべき場面を試合データから探し、output/scenes.md に書き出す。

テーマ:
  A. 上位帯: 序盤に対面を倒し、そのままプレートに変えた場面
  B. シルバー: 20分以降、味方から離れた敵陣側で捕まった場面
  C. 上位帯 vs シルバー: 10分時点のCSが特に高い／低いレーン（CSの取り方の比較用）

出力にはプレイヤーのRiot IDが含まれる。リプレイを探すためだけに使い、動画では必ず伏せること。
"""
import argparse
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from extract import zone

PLATE_WINDOW_MS = 120_000
ISOLATED_DISTANCE = 4000


def to_sec(text):
    m, s = text.split(":")
    return int(m) * 60 + int(s)


def fmt_time(ms):
    s = max(0, int(ms // 1000))
    return f"{s // 60}:{s % 60:02d}"


def riot_id(p):
    name, tag = p.get("riotIdGameName"), p.get("riotIdTagline")
    return f"{name}#{tag}" if name else "(不明)"


def side(team_id):
    return "青（左下）" if team_id == 100 else "赤（右上）"


def nearest_ally_distance(frames, ts, pid, team_pids, pos):
    i = min(round(ts / 60000), len(frames) - 1)
    pfs = frames[i]["participantFrames"]
    dists = [math.hypot(pfs[str(a)]["position"]["x"] - pos["x"], pfs[str(a)]["position"]["y"] - pos["y"])
             for a in team_pids if a != pid and str(a) in pfs and "position" in pfs[str(a)]]
    return min(dists) if dists else None


def scan(match, timeline, tier):
    info = match["info"]
    parts = {p["participantId"]: p for p in info["participants"]}
    tops = [p for p in parts.values() if p.get("teamPosition") == "TOP"]
    if len(tops) != 2 or tops[0]["teamId"] == tops[1]["teamId"]:
        return []
    frames = timeline["info"]["frames"]
    events = [e for fr in frames for e in fr.get("events", [])]
    base = {"match_id": match["metadata"]["matchId"], "tier": tier,
            "date": datetime.fromtimestamp(info["gameCreation"] / 1000, timezone.utc).strftime("%m/%d")}
    found = []
    for p in tops:
        pid, team = p["participantId"], p["teamId"]
        opp = tops[1] if p is tops[0] else tops[0]
        team_pids = [q for q, v in parts.items() if v["teamId"] == team]
        who = {**base, "player": riot_id(p), "player_name": p.get("riotIdGameName", ""), "champion": p["championName"], "opponent": opp["championName"],
               "side": side(team), "win": "勝ち" if p["win"] else "負け"}

        # A: 14分までに対面をキル → 2分以内に自分でプレート
        for e in events:
            if (e["type"] == "CHAMPION_KILL" and e.get("killerId") == pid
                    and e.get("victimId") == opp["participantId"] and e["timestamp"] < 14 * 60000):
                plates = [x for x in events if x["type"] == "TURRET_PLATE_DESTROYED"
                          and x.get("killerId") == pid and x.get("laneType") == "TOP_LANE"
                          and 0 < x["timestamp"] - e["timestamp"] <= PLATE_WINDOW_MS]
                if plates:
                    solo = not e.get("assistingParticipantIds")
                    found.append({**who, "theme": "A", "time": e["timestamp"],
                                  "start": fmt_time(e["timestamp"] - 40000),
                                  "end": fmt_time(plates[-1]["timestamp"] + 5000),
                                  "note": f"{'ソロキル' if solo else 'キル'}→プレート{len(plates)}枚",
                                  "score": len(plates) + solo})

        # B: 20分以降、敵陣側で味方から離れて死亡
        for e in events:
            if e["type"] != "CHAMPION_KILL" or e.get("victimId") != pid or e["timestamp"] < 20 * 60000:
                continue
            pos = e.get("position", {})
            if zone(team, pos.get("x", 0), pos.get("y", 0)) != "enemy":
                continue
            d = nearest_ally_distance(frames, e["timestamp"], pid, team_pids, pos)
            if d is None or d < ISOLATED_DISTANCE:
                continue
            n_killers = 1 + len(e.get("assistingParticipantIds", []))
            found.append({**who, "theme": "B", "time": e["timestamp"],
                          "start": fmt_time(e["timestamp"] - 45000), "end": fmt_time(e["timestamp"] + 5000),
                          "note": f"{n_killers}人に倒される／一番近い味方まで約{int(d)}",
                          "score": n_killers})

        # C: 10分CS
        if len(frames) > 10:
            pf = frames[10]["participantFrames"][str(pid)]
            cs10 = pf["minionsKilled"] + pf["jungleMinionsKilled"]
            found.append({**who, "theme": "C", "time": 0, "start": "1:30", "end": "10:00",
                          "note": f"10分CS {cs10}", "score": cs10})
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="output")
    ap.add_argument("--per-theme", type=int, default=4)
    args = ap.parse_args()
    data_dir, out = Path(args.data_dir), Path(args.out)

    with (data_dir / "index.csv").open() as f:
        index = list(csv.DictReader(f))
    rows = []
    for item in index:
        mp = data_dir / "cache" / "match" / f"{item['match_id']}.json"
        tp = data_dir / "cache" / "timeline" / f"{item['match_id']}.json"
        if mp.exists() and tp.exists():
            match = json.loads(mp.read_text())
            if match["info"]["gameDuration"] >= 15 * 60:
                rows += scan(match, json.loads(tp.read_text()), item["tier"])

    high = {"DIAMOND", "EMERALD"}
    # 新しい試合ほどリプレイが残っている可能性が高いので、同点なら新しい順
    def pick(theme, tiers, reverse=True):
        cand = [r for r in rows if r["theme"] == theme and r["tier"] in tiers]
        cand.sort(key=lambda r: (r["score"], r["match_id"]), reverse=reverse)
        seen, res = set(), []
        for r in cand:
            if r["match_id"] not in seen:
                seen.add(r["match_id"])
                res.append(r)
        return res[:args.per_theme]

    sections = [
        ("A. 上位帯：序盤に対面を倒し、そのままプレートに変える", pick("A", high)),
        ("B. シルバー：20分以降、味方から離れた敵陣側で捕まる", pick("B", {"SILVER"})),
        ("C-1. 上位帯：10分CSが特に多いレーン", pick("C", high)),
        ("C-2. シルバー：10分CSが特に少ないレーン", pick("C", {"SILVER"}, reverse=False)),
    ]
    lines = ["# 録画する場面リスト", "",
             "- Riot ID はリプレイを探すためだけに使い、動画では必ず伏せてください。",
             "- 時刻はゲーム内時間です。数秒ずれることがあるので、前後に余裕を持って録画してください。",
             "- リプレイは同じパッチの試合しか開けないため、パッチが変わる前に録画してください。", ""]
    n = 0
    for title, items in sections:
        lines += [f"## {title}", "", "| # | ランク | 日付 | プレイヤー | チャンピオン（対面） | 陣営 | 結果 | 録画範囲 | 内容 | 試合ID |",
                  "|---|---|---|---|---|---|---|---|---|---|"]
        for r in items:
            n += 1
            lines.append(f"| {n} | {r['tier']} | {r['date']} | {r['player']} | {r['champion']}（{r['opponent']}） | "
                         f"{r['side']} | {r['win']} | {r['start']}〜{r['end']} | {r['note']} | {r['match_id']} |")
        lines.append("")
    (out / "scenes.md").write_text("\n".join(lines))

    # 録画スクリプト (tools/record_replays.py) 用。カメラを合わせるためにゲーム内の名前だけ残す
    n = 0
    with (out / "scenes.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["no", "theme", "game_id", "start_sec", "end_sec", "champion", "player_name"])
        for _, items in sections:
            for r in items:
                n += 1
                w.writerow([n, r["theme"], r["match_id"].split("_")[1], to_sec(r["start"]),
                            to_sec(r["end"]), r["champion"], r["player_name"]])
    print(f"{n} 場面を書き出しました: {out / 'scenes.md'}")


if __name__ == "__main__":
    main()

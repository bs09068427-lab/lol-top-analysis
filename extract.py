"""キャッシュ済みの試合から、トップレーナー1人=1行の指標表とデス位置表を作る。

出力:
  data/top_laners.csv  … 指標（CS差・デス・視界・オブジェクト参加など）
  data/deaths.csv      … トップレーナーのデス位置（青側視点に正規化済み）
"""
import argparse
import csv
import json
import math
from pathlib import Path

MAP_SIZE = 14870          # サモナーズリフトの座標の最大値（おおよそ）
RIVER_MARGIN = 1500       # 川とみなす幅（x+y=MAP_SIZE の対角線からの距離）
OBJECTIVES = ("HORDE", "RIFTHERALD", "DRAGON", "BARON_NASHOR", "ATAKHAN")
EARLY_TOP_OBJECTIVES = ("HORDE", "RIFTHERALD")
NEAR_DISTANCE = 3000
CONTROL_WARD_ID = 2055
TELEPORT_ID = 12
MIN_DURATION_SEC = 15 * 60  # リメイクや早期サレンダーを除外


def zone(team_id, x, y):
    """自陣 / 川 / 敵陣 の3分類。青(100)は左下、赤(200)は右上。"""
    d = (x + y) - MAP_SIZE
    if team_id == 200:
        d = -d
    if d > RIVER_MARGIN:
        return "enemy"
    if d < -RIVER_MARGIN:
        return "own"
    return "river"


def normalize(team_id, x, y):
    """赤側の座標を川（x+y=MAP_SIZE）で鏡映し、全員を青側視点にそろえる。
    マップはこの対角線で線対称なので、トップレーンはどちらのチームでも左上に来る。"""
    return (MAP_SIZE - y, MAP_SIZE - x) if team_id == 200 else (x, y)


def frame_stat(frames, minute, pid, key):
    if minute >= len(frames):
        return None
    pf = frames[minute]["participantFrames"].get(str(pid))
    if pf is None:
        return None
    if key == "cs":
        return pf["minionsKilled"] + pf["jungleMinionsKilled"]
    if key == "gold":
        return pf["totalGold"]
    if key == "xp":
        return pf["xp"]
    raise KeyError(key)


def diff(a, b):
    return None if a is None or b is None else a - b


def near(frames, ts, pid, pos):
    if not pos:
        return False
    i = min(round(ts / 60000), len(frames) - 1)
    pf = frames[i]["participantFrames"].get(str(pid))
    if not pf or "position" not in pf:
        return False
    p = pf["position"]
    return math.hypot(p["x"] - pos["x"], p["y"] - pos["y"]) <= NEAR_DISTANCE


def lane_rows(match, timeline, tier, patch):
    info = match["info"]
    if info["gameDuration"] < MIN_DURATION_SEC:
        return [], []
    parts = info["participants"]
    tops = [p for p in parts if p.get("teamPosition") == "TOP"]
    if len(tops) != 2 or tops[0]["teamId"] == tops[1]["teamId"]:
        return [], []
    junglers = {p["teamId"]: p["participantId"] for p in parts
                if p.get("teamPosition") == "JUNGLE"}
    duration_min = info["gameDuration"] / 60
    match_id = match["metadata"]["matchId"]

    frames, events = [], []
    if timeline:
        frames = timeline["info"]["frames"]
        events = [e for fr in frames for e in fr.get("events", [])]

    rows, deaths = [], []
    for p in tops:
        opp = tops[1] if p is tops[0] else tops[0]
        pid, oid, team = p["participantId"], opp["participantId"], p["teamId"]
        enemy_jg = junglers.get(opp["teamId"])
        ch = p.get("challenges", {})
        row = {
            "match_id": match_id, "tier": tier, "patch": patch,
            "champion": p["championName"], "opponent": opp["championName"],
            "win": int(p["win"]), "duration_min": round(duration_min, 2),
            "kills": p["kills"], "deaths": p["deaths"], "assists": p["assists"],
            "deaths_pm": p["deaths"] / duration_min,
            "cs_pm": (p["totalMinionsKilled"] + p["neutralMinionsKilled"]) / duration_min,
            "vision_score_pm": p["visionScore"] / duration_min,
            "control_wards_bought": p.get("visionWardsBoughtInGame"),
            "solo_kills": ch.get("soloKills"),
            "plates": ch.get("turretPlatesTaken"),  # チーム全体のプレート数
            "kill_participation": ch.get("killParticipation"),
            "lane_cs_10_challenge": ch.get("laneMinionsFirst10Minutes"),
            "has_teleport": int(TELEPORT_ID in (p.get("summoner1Id"), p.get("summoner2Id"))),
        }

        if frames:
            for m in (10, 14):
                row[f"cs{m}"] = frame_stat(frames, m, pid, "cs")
                row[f"csd{m}"] = diff(row[f"cs{m}"], frame_stat(frames, m, oid, "cs"))
            row["gd14"] = diff(frame_stat(frames, 14, pid, "gold"), frame_stat(frames, 14, oid, "gold"))
            row["xpd14"] = diff(frame_stat(frames, 14, pid, "xp"), frame_stat(frames, 14, oid, "xp"))
            cs20 = frame_stat(frames, 20, pid, "cs")
            cs_end = frame_stat(frames, len(frames) - 1, pid, "cs")
            row["cs_pm_after20"] = ((cs_end - cs20) / (len(frames) - 1 - 20)
                                    if cs20 is not None and len(frames) - 1 > 25 else None)

            pre14 = gank = pre14_enemy = late_enemy = 0
            for e in events:
                if e["type"] != "CHAMPION_KILL" or e.get("victimId") != pid:
                    continue
                ts, pos = e["timestamp"], e.get("position", {})
                killers = {e.get("killerId")} | set(e.get("assistingParticipantIds", []))
                z = zone(team, pos.get("x", 0), pos.get("y", 0))
                is_gank = enemy_jg in killers
                minute = ts / 60000
                if minute < 14:
                    pre14 += 1
                    gank += is_gank
                    pre14_enemy += z == "enemy"
                elif minute >= 20 and z == "enemy":
                    late_enemy += 1
                nx, ny = normalize(team, pos.get("x", 0), pos.get("y", 0))
                deaths.append({"match_id": match_id, "tier": tier, "champion": p["championName"],
                               "minute": round(minute, 2), "x": nx, "y": ny, "zone": z,
                               "gank": int(is_gank), "win": int(p["win"])})
            row.update(deaths_pre14=pre14, gank_deaths_pre14=gank,
                       deaths_pre14_enemy_half=pre14_enemy, late_deaths_enemy_half=late_enemy)

            row["wards_pre14"] = sum(
                1 for e in events
                if e["type"] == "WARD_PLACED" and e.get("creatorId") == pid
                and e.get("wardType") != "UNDEFINED" and e["timestamp"] < 14 * 60000)

            team_objs = joined = early_objs = early_joined = 0
            for e in events:
                if e["type"] != "ELITE_MONSTER_KILL" or e.get("killerTeamId") != team:
                    continue
                if e.get("monsterType") not in OBJECTIVES:
                    continue
                took_part = (e.get("killerId") == pid
                             or pid in e.get("assistingParticipantIds", [])
                             or near(frames, e["timestamp"], pid, e.get("position")))
                team_objs += 1
                joined += took_part
                if e["monsterType"] in EARLY_TOP_OBJECTIVES:
                    early_objs += 1
                    early_joined += took_part
            row["objective_participation"] = joined / team_objs if team_objs else None
            row["early_top_objective_participation"] = early_joined / early_objs if early_objs else None
        rows.append(row)
    return rows, deaths


def write_csv(path, rows):
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    cache = data_dir / "cache"

    with (data_dir / "index.csv").open() as f:
        index = list(csv.DictReader(f))

    rows, deaths = [], []
    for item in index:
        mp = cache / "match" / f"{item['match_id']}.json"
        tp = cache / "timeline" / f"{item['match_id']}.json"
        if not mp.exists():
            continue
        match = json.loads(mp.read_text())
        timeline = json.loads(tp.read_text()) if tp.exists() else None
        r, d = lane_rows(match, timeline, item["tier"], item["patch"])
        rows += r
        deaths += d

    write_csv(data_dir / "top_laners.csv", rows)
    write_csv(data_dir / "deaths.csv", deaths)
    print(f"トップレーナー {len(rows)} 行 / デス {len(deaths)} 件を書き出しました")


if __name__ == "__main__":
    main()

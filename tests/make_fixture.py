"""動作確認専用のダミーデータを作る（実データではない）。

使い方: python tests/make_fixture.py /path/to/fake_data
"""
import csv
import json
import random
import sys
from pathlib import Path

ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
CHAMPS = ["Garen", "Darius", "Malphite", "Sett", "Fiora", "Jax", "Camille", "Ornn", "Renekton", "Aatrox"]
SKILL = {"DIAMOND": 1.0, "EMERALD": 0.8, "GOLD": 0.5, "SILVER": 0.3}


def make(match_id, tier, rng):
    s = SKILL[tier]
    minutes = rng.randint(22, 38)
    blue_win = rng.random() < 0.5
    parts = []
    for i in range(10):
        team = 100 if i < 5 else 200
        parts.append({
            "participantId": i + 1, "teamId": team, "teamPosition": ROLES[i % 5],
            "championName": rng.choice(CHAMPS), "win": blue_win == (team == 100),
            "kills": rng.randint(0, 10), "deaths": rng.randint(0, int(10 - 5 * s)), "assists": rng.randint(0, 10),
            "totalMinionsKilled": int(minutes * rng.gauss(5 + 2.5 * s, 0.8)), "neutralMinionsKilled": rng.randint(0, 20),
            "visionScore": int(minutes * rng.gauss(0.5 + 0.4 * s, 0.1)), "visionWardsBoughtInGame": rng.randint(0, int(1 + 4 * s)),
            "summoner1Id": 4, "summoner2Id": 12 if rng.random() < 0.8 else 14,
            "challenges": {"soloKills": rng.randint(0, 3), "turretPlatesTaken": rng.randint(0, 4),
                           "killParticipation": rng.random()},
        })
    frames, cs = [], {p: 0 for p in range(1, 11)}
    for m in range(minutes + 1):
        pf, events = {}, []
        for p in range(1, 11):
            cs[p] += max(0, int(rng.gauss(5 + 2.5 * s, 1.5))) if m else 0
            pf[str(p)] = {"minionsKilled": cs[p], "jungleMinionsKilled": 0, "totalGold": 500 + 400 * m + cs[p] * 20,
                          "xp": 400 * m, "position": {"x": rng.randint(0, 14870), "y": rng.randint(0, 14870)}}
        for top in (1, 6):
            if m and rng.random() < 0.25 - 0.12 * s:
                events.append({"type": "CHAMPION_KILL", "timestamp": m * 60000 - 1000, "victimId": top,
                               "killerId": 7 if top == 1 else 2, "assistingParticipantIds": [6 if top == 1 else 1] if rng.random() < 0.5 else [],
                               "position": {"x": rng.randint(500, 9000), "y": rng.randint(6000, 14000)}})
            if m < 14 and rng.random() < 0.3 + 0.4 * s:
                events.append({"type": "WARD_PLACED", "timestamp": m * 60000, "creatorId": top, "wardType": "YELLOW_TRINKET"})
        if m in (6, 15, 25):
            events.append({"type": "ELITE_MONSTER_KILL", "timestamp": m * 60000, "killerId": 2, "killerTeamId": 100,
                           "monsterType": "HORDE" if m == 6 else "DRAGON", "assistingParticipantIds": [1] if rng.random() < s else [],
                           "position": {"x": 5000, "y": 10000}})
        frames.append({"participantFrames": pf, "events": events, "timestamp": m * 60000})
    match = {"metadata": {"matchId": match_id},
             "info": {"gameVersion": "15.21.700.1", "gameDuration": minutes * 60, "participants": parts}}
    return match, {"info": {"frames": frames}}


def main():
    out = Path(sys.argv[1])
    rng = random.Random(1)
    for sub in ("match", "timeline"):
        (out / "cache" / sub).mkdir(parents=True, exist_ok=True)
    with (out / "index.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["match_id", "tier", "division", "patch"])
        w.writeheader()
        n = 0
        for tier in SKILL:
            for _ in range(120):
                n += 1
                mid = f"FAKE_{n}"
                match, tl = make(mid, tier, rng)
                (out / "cache" / "match" / f"{mid}.json").write_text(json.dumps(match))
                (out / "cache" / "timeline" / f"{mid}.json").write_text(json.dumps(tl))
                w.writerow({"match_id": mid, "tier": tier, "division": "II", "patch": "15.21"})


if __name__ == "__main__":
    main()

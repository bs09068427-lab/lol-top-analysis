"""scenes.csv の場面を、LoL クライアントのリプレイから自動で録画する（Windows / macOS のPCで実行）。

仕組み:
  1. クライアント内部の API (LCU) でリプレイをダウンロードして再生を開始
  2. Riot 公式の Replay API (https://127.0.0.1:2999) でカメラ・再生位置を合わせて録画
  3. 録画が終わったらゲームを閉じて次の場面へ

準備:
  - LoL クライアントにログインしておく（ゲームは起動しない）
  - 初回だけ `python record_replays.py --setup` で Replay API を有効にする
  - pip install requests

使い方:
  python record_replays.py scenes.csv --out recordings
  python record_replays.py scenes.csv --only 1,5      # 番号を指定して一部だけ
  python record_replays.py --check                    # クライアントにつながるかだけ確認

録画中は画面が全画面のリプレイになるため、PCは操作しないこと。
"""
import argparse
import base64
import configparser
import csv
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

DEFAULT_DIRS = {
    "Windows": [r"C:\Riot Games\League of Legends"],
    "Darwin": ["/Applications/League of Legends.app/Contents/LoL"],
}
REPLAY_API = "https://127.0.0.1:2999"


def find_install(arg):
    cands = [arg] if arg else DEFAULT_DIRS.get(platform.system(), [])
    for c in cands:
        if c and (Path(c) / "Config").exists():
            return Path(c)
    sys.exit("LoL のインストール先が見つかりません。--lol-dir で指定してください。")


# ---- 初期設定 --------------------------------------------------------------

def setup(lol_dir):
    cfg_path = lol_dir / "Config" / "game.cfg"
    backup = cfg_path.with_suffix(".cfg.bak")
    if not backup.exists():
        shutil.copy(cfg_path, backup)
    cfg = configparser.ConfigParser(strict=False, interpolation=None)
    cfg.optionxform = str
    cfg.read(cfg_path, encoding="utf-8")
    if not cfg.has_section("General"):
        cfg.add_section("General")
    cfg.set("General", "EnableReplayApi", "1")
    with cfg_path.open("w", encoding="utf-8") as f:
        cfg.write(f, space_around_delimiters=False)
    print(f"Replay API を有効にしました（元の設定は {backup} に保存）")


# ---- クライアント (LCU) ------------------------------------------------------

class Lcu:
    def __init__(self, lol_dir):
        lockfile = lol_dir / "lockfile"
        if not lockfile.exists():
            sys.exit("lockfile がありません。LoL クライアントを起動してログインしてください。")
        _, _, port, password, proto = lockfile.read_text().split(":")
        self.base = f"{proto}://127.0.0.1:{port}"
        token = base64.b64encode(f"riot:{password}".encode()).decode()
        self.s = requests.Session()
        self.s.verify = False
        self.s.headers["Authorization"] = f"Basic {token}"

    def get(self, path):
        r = self.s.get(self.base + path, timeout=15)
        return r.json() if r.ok and r.content else None

    def post(self, path, body=None):
        return self.s.post(self.base + path, json=body or {}, timeout=30)


def prepare_replay(lcu, game_id, timeout=300):
    """リプレイをダウンロードし、再生できる状態になるまで待つ。"""
    meta = lcu.get(f"/lol-replays/v1/metadata/{game_id}") or {}
    state = meta.get("state")
    if state == "download":
        lcu.post(f"/lol-replays/v1/rofls/{game_id}/download", {"contextData": "match-history"})
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = (lcu.get(f"/lol-replays/v1/metadata/{game_id}") or {}).get("state")
        if state == "watch":
            return True
        if state in ("incompatible", "missingOrExpired", "unsupported", "error", "lost"):
            print(f"  リプレイを使えません（state={state}）")
            return False
        time.sleep(2)
    print(f"  ダウンロードがタイムアウトしました（state={state}）")
    return False


# ---- Replay API --------------------------------------------------------------

def replay(method, path, body=None):
    r = requests.request(method, REPLAY_API + path, json=body, verify=False, timeout=10)
    r.raise_for_status()
    return r.json() if r.content else None


def wait_replay_ready(timeout=240):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            pb = replay("GET", "/replay/playback")
            if pb and pb.get("length", 0) > 0:
                return True
        except requests.RequestException:
            pass
        time.sleep(3)
    return False


def close_game():
    if platform.system() == "Windows":
        subprocess.run(["taskkill", "/IM", "League of Legends.exe", "/F"], capture_output=True)
    else:
        subprocess.run(["pkill", "-f", "League of Legends"], capture_output=True)


def record_scene(scene, out_dir, width, height, fps):
    start, end = int(scene["start_sec"]), int(scene["end_sec"])
    # 選択中のユニットにカメラを固定。名前で合わなければチャンピオン名で試す
    for name in filter(None, [scene.get("player_name"), scene["champion"]]):
        try:
            replay("POST", "/replay/render", {"selectionName": name, "cameraAttached": True,
                                             "interfaceScoreboard": False, "interfaceChat": False})
            break
        except requests.HTTPError:
            continue
    replay("POST", "/replay/playback", {"time": max(0, start - 3), "paused": True, "speed": 1.0})
    time.sleep(3)
    replay("POST", "/replay/recording", {
        "recording": True, "codec": "webm", "path": str(out_dir.resolve()),
        "startTime": start, "endTime": end, "framesPerSecond": fps,
        "width": width, "height": height, "enforceFrameRate": True, "replaySpeed": 1.0,
    })
    replay("POST", "/replay/playback", {"paused": False})
    # 録画終了まで待つ（録画は実時間より遅く進むことがある）
    deadline = time.time() + (end - start) * 4 + 120
    while time.time() < deadline:
        time.sleep(5)
        try:
            st = replay("GET", "/replay/recording")
        except requests.RequestException:
            continue
        if not st.get("recording"):
            return True
    return False


def latest_file(folder, since):
    files = [p for p in folder.glob("*") if p.is_file() and p.stat().st_mtime >= since]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenes", nargs="?", default="scenes.csv")
    ap.add_argument("--out", default="recordings")
    ap.add_argument("--only", default=None, help="録画する番号（例: 1,5,10）")
    ap.add_argument("--lol-dir", default=None)
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--setup", action="store_true", help="Replay API を有効にする（初回のみ）")
    ap.add_argument("--check", action="store_true", help="クライアントにつながるか確認するだけ")
    args = ap.parse_args()

    lol_dir = find_install(args.lol_dir)
    if args.setup:
        setup(lol_dir)
        return
    lcu = Lcu(lol_dir)
    if args.check:
        me = lcu.get("/lol-summoner/v1/current-summoner")
        print("クライアント接続 OK" if me else "クライアントに接続できません（ログインしているか確認）")
        return

    with open(args.scenes, encoding="utf-8") as f:
        scenes = list(csv.DictReader(f))
    if args.only:
        wanted = set(args.only.split(","))
        scenes = [s for s in scenes if s["no"] in wanted]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for sc in scenes:
        print(f"#{sc['no']} {sc['champion']} (game {sc['game_id']}) {sc['start_sec']}〜{sc['end_sec']}秒")
        if not prepare_replay(lcu, sc["game_id"]):
            results.append((sc["no"], "リプレイなし"))
            continue
        lcu.post(f"/lol-replays/v1/rofls/{sc['game_id']}/watch", {"contextData": "match-history"})
        if not wait_replay_ready():
            print("  リプレイが起動しませんでした（game.cfg の EnableReplayApi=1 を確認）")
            close_game()
            results.append((sc["no"], "起動失敗"))
            continue
        began = time.time()
        ok = record_scene(sc, out_dir, args.width, args.height, args.fps)
        close_game()
        time.sleep(8)
        f = latest_file(out_dir, began)
        if ok and f:
            final = out_dir / f"scene{int(sc['no']):02d}_{sc['theme']}_{sc['champion']}{f.suffix}"
            os.replace(f, final)
            print(f"  保存: {final}")
            results.append((sc["no"], "OK"))
        else:
            results.append((sc["no"], "録画失敗"))

    print("\n結果:")
    for no, status in results:
        print(f"  #{no}: {status}")


if __name__ == "__main__":
    main()

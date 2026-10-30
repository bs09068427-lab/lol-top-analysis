"""Riot API の薄いクライアント。レート制限の待機・リトライ・ディスクキャッシュを担当する。"""
import json
import os
import time
from collections import deque
from pathlib import Path

import requests

# 開発用キー / Personal キーの既定上限: 1秒20回, 2分100回
DEFAULT_LIMITS = ((20, 1.0), (100, 120.0))


class RateLimiter:
    def __init__(self, limits=DEFAULT_LIMITS):
        self.windows = [(n, sec, deque()) for n, sec in limits]

    def wait(self):
        while True:
            now = time.monotonic()
            sleep_for = 0.0
            for n, sec, q in self.windows:
                while q and now - q[0] >= sec:
                    q.popleft()
                if len(q) >= n:
                    sleep_for = max(sleep_for, sec - (now - q[0]) + 0.05)
            if sleep_for <= 0:
                break
            time.sleep(sleep_for)
        now = time.monotonic()
        for _, _, q in self.windows:
            q.append(now)


class RiotClient:
    def __init__(self, api_key=None, platform="jp1", region="asia",
                 cache_dir="data/cache", limits=DEFAULT_LIMITS):
        self.api_key = api_key or os.environ.get("RIOT_API_KEY")
        if not self.api_key:
            raise RuntimeError("RIOT_API_KEY が設定されていません")
        self.platform_host = f"https://{platform}.api.riotgames.com"
        self.region_host = f"https://{region}.api.riotgames.com"
        self.cache_dir = Path(cache_dir)
        self.limiter = RateLimiter(limits)
        self.session = requests.Session()
        self.session.headers["X-Riot-Token"] = self.api_key

    def _cache_path(self, key):
        return self.cache_dir / f"{key}.json"

    def get(self, url, params=None, cache_key=None):
        if cache_key:
            path = self._cache_path(cache_key)
            if path.exists():
                return json.loads(path.read_text())
        for attempt in range(6):
            self.limiter.wait()
            try:
                r = self.session.get(url, params=params, timeout=30)
            except requests.RequestException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                time.sleep(float(r.headers.get("Retry-After", 10)))
                continue
            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 404:
                return None
            if r.status_code in (401, 403):
                raise RuntimeError(f"APIキーが無効か失効しています ({r.status_code})")
            r.raise_for_status()
            data = r.json()
            if cache_key:
                path = self._cache_path(cache_key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data))
            return data
        raise RuntimeError(f"リトライ上限に達しました: {url}")

    def league_entries(self, tier, division, page=1):
        url = f"{self.platform_host}/lol/league/v4/entries/RANKED_SOLO_5x5/{tier}/{division}"
        return self.get(url, params={"page": page}) or []

    def match_ids(self, puuid, queue=420, count=20, start_time=None):
        params = {"queue": queue, "count": count}
        if start_time:
            params["startTime"] = int(start_time)
        url = f"{self.region_host}/lol/match/v5/matches/by-puuid/{puuid}/ids"
        return self.get(url, params=params) or []

    def match(self, match_id):
        url = f"{self.region_host}/lol/match/v5/matches/{match_id}"
        return self.get(url, cache_key=f"match/{match_id}")

    def timeline(self, match_id):
        url = f"{self.region_host}/lol/match/v5/matches/{match_id}/timeline"
        return self.get(url, cache_key=f"timeline/{match_id}")

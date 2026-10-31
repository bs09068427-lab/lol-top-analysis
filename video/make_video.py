"""分析結果 (output/tier_means.csv など) から解説動画を組み立てる。

スライド画像を matplotlib で描き、ナレーションを Open JTalk (pyopenjtalk) で読み上げ、
ffmpeg で1本の MP4 にまとめる。1文ごとに字幕付きのクリップを作って連結する。

使い方: python3 video/make_video.py  →  output/video/video.mp4
"""
import subprocess
import sys
import textwrap
import wave
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pyopenjtalk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analyze import setup_font  # noqa: E402
from extract import MAP_SIZE  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "output"
WORK = OUT / "video"
W, H, DPI = 19.2, 10.8, 100
BG, FG, SUB = "#10141c", "#f2f2f2", "#9aa4b5"
TIER_COLOR = {"DIAMOND": "#5aa9e6", "EMERALD": "#3fbf8a", "GOLD": "#e3b23c", "SILVER": "#b8c0cc"}
TIER_JA = {"DIAMOND": "ダイヤ", "EMERALD": "エメラルド", "GOLD": "ゴールド", "SILVER": "シルバー"}
GAP = 0.45  # 文と文の間の無音（秒）


def load():
    means = pd.read_csv(OUT / "tier_means.csv", index_col=0)
    effects = pd.read_csv(OUT / "win_effects_low.csv")
    laners = pd.read_csv(ROOT / "data" / "top_laners.csv")
    counts = laners.groupby("tier").match_id.nunique()
    players = len(laners)
    patch = pd.read_csv(ROOT / "data" / "index.csv").patch.astype(str).iloc[0]
    return means, effects, counts, players, patch


# ---- スライド描画 ---------------------------------------------------------

def new_fig():
    fig = plt.figure(figsize=(W, H), dpi=DPI, facecolor=BG)
    return fig


def caption(fig, text):
    wrapped = "\n".join(textwrap.wrap(text, 38))
    fig.text(0.5, 0.075, wrapped, ha="center", va="center", fontsize=30, color=FG, linespacing=1.4,
             bbox=dict(boxstyle="round,pad=0.6", fc="#000000", ec="none", alpha=0.65))


def heading(fig, text, y=0.9, size=46):
    fig.text(0.5, y, text, ha="center", va="center", fontsize=size, color=FG, weight="bold")


def slide_text(title, lines):
    def draw(fig):
        heading(fig, title, y=0.8, size=56)
        for i, ln in enumerate(lines):
            fig.text(0.5, 0.62 - i * 0.1, ln, ha="center", va="center", fontsize=36, color=SUB)
    return draw


def slide_bars(title, row, means, unit="", fmt="{:.2f}", note=None):
    def draw(fig):
        heading(fig, title)
        ax = fig.add_axes([0.18, 0.2, 0.64, 0.6], facecolor=BG)
        tiers = [t for t in TIER_JA if t in means.columns]
        vals = [means.loc[row, t] for t in tiers]
        bars = ax.bar([TIER_JA[t] for t in tiers], vals, color=[TIER_COLOR[t] for t in tiers], width=0.6)
        for b, v in zip(bars, vals):
            ax.text(b.get_x() + b.get_width() / 2, v, fmt.format(v) + unit, ha="center", va="bottom",
                    fontsize=34, color=FG, weight="bold")
        ax.set_ylim(0, max(vals) * 1.25)
        ax.tick_params(colors=FG, labelsize=28, length=0)
        ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        if note:
            fig.text(0.5, 0.83, note, ha="center", fontsize=26, color=SUB)
    return draw


def slide_death_map(title, tiers):
    deaths = pd.read_csv(ROOT / "data" / "deaths.csv")
    deaths = deaths[deaths.minute < 14]

    def draw(fig):
        heading(fig, title)
        width = 0.36
        for i, tier in enumerate(tiers):
            t = deaths[deaths.tier == tier]
            ax = fig.add_axes([0.1 + i * 0.44, 0.17, width, 0.58], facecolor="#1b2230")
            ax.scatter(t.x, t.y, s=22, alpha=0.5, color=TIER_COLOR[tier], edgecolors="none")
            ax.plot([0, MAP_SIZE], [MAP_SIZE, 0], color="#55606f", lw=1.5, ls="--")
            # トップレーン周辺を拡大表示
            ax.set_xlim(0, MAP_SIZE * 0.6)
            ax.set_ylim(MAP_SIZE * 0.4, MAP_SIZE)
            ax.set_xticks([])
            ax.set_yticks([])
            for sp in ax.spines.values():
                sp.set_color("#55606f")
            ax.set_title(f"{TIER_JA[tier]}（{len(t)}件）", color=FG, fontsize=32, pad=12)
            ax.text(MAP_SIZE * 0.02, MAP_SIZE * 0.45, "↓ 自陣タワー側", color=SUB, fontsize=22)
            ax.text(MAP_SIZE * 0.33, MAP_SIZE * 0.965, "敵陣タワー側 →", color=SUB, fontsize=22)
    return draw


def slide_effects(title, effects, labels):
    def draw(fig):
        heading(fig, title)
        rows = effects.set_index("指標").loc[list(labels)]["勝敗との関連"][::-1]
        ax = fig.add_axes([0.32, 0.22, 0.55, 0.58], facecolor=BG)
        ax.barh([labels[k] for k in rows.index], rows.values, color="#5aa9e6", height=0.55)
        for i, v in enumerate(rows.values):
            ax.text(v, i, f"  {v:.2f}", va="center", fontsize=30, color=FG)
        ax.set_xlim(0, rows.max() * 1.3)
        ax.tick_params(colors=FG, labelsize=30, length=0)
        ax.set_xticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        fig.text(0.5, 0.83, "ゴールド・シルバー帯で、勝った人と負けた人の差（大きいほど勝敗と関係が強い）",
                 ha="center", fontsize=24, color=SUB)
    return draw


# ---- 台本 ----------------------------------------------------------------

def script(means, effects, counts, players, patch):
    m = lambda row, tier: means.loc[row, tier]  # noqa: E731
    total = int(counts.sum())
    return [
        (slide_text("シルバーとダイヤのトップ", ["データで比べたら意外な結果だった"]), [
            "シルバーとダイヤのトップレーナーは、いったい何が違うのか。",
            "今回は感覚ではなく、実際の試合データで比べてみました。",
        ]),
        (slide_text("調べ方", [f"日本サーバー・パッチ{patch}のソロランク",
                              f"ダイヤ／エメラルド／ゴールド／シルバー　計{total}試合",
                              f"トップレーナー{players}人分を集計"]), [
            f"日本サーバーのパッチ{patch}、ソロランクの試合を、ランク帯ごとにおよそ百試合ずつ集めました。",
            f"合計で{total}試合、トップレーナー{players}人分のデータです。",
        ]),
        (slide_bars("序盤14分までに死んだ回数", "14分までのデス数", means, unit="回"), [
            "まずはクイズです。序盤によく死んでいるのは、上のランクと下のランク、どちらだと思いますか。",
            f"答えは、上のランクです。ダイヤは14分までに平均{m('14分までのデス数','DIAMOND'):.2f}回、"
            f"シルバーは{m('14分までのデス数','SILVER'):.2f}回でした。",
        ]),
        (slide_bars("敵ジャングラーが絡んだデス（14分まで）", "14分までのガンクによるデス", means, unit="回"), [
            "特に差が大きいのが、敵ジャングラーが絡んだデスです。",
            f"ダイヤは{m('14分までのガンクによるデス','DIAMOND'):.2f}回、シルバーは{m('14分までのガンクによるデス','SILVER'):.2f}回と、およそ二倍の差がありました。",
        ]),
        (slide_death_map("14分までに死んだ場所（トップレーン周辺）", ["DIAMOND", "SILVER"]), [
            "死んだ場所を地図にすると、シルバーは自分のタワーの近くに集まっています。",
            "一方ダイヤは、レーンの真ん中から敵のタワー側まで広く散らばっています。",
        ]),
        (slide_text("ここから考えられること", ["上位帯は序盤から前に出て勝負している",
                                          "※データだけで理由は断定できません"]), [
            "データだけで理由は断定できませんが、上のランクほど序盤から前に出て勝負していると考えられます。",
            "死なないことだけを目標にして、何もしないのは、必ずしも正解ではないのかもしれません。",
        ]),
        (slide_bars("10分時点のCS", "10分時点のCS", means, fmt="{:.1f}"), [
            f"CSも見てみましょう。10分時点で、ダイヤは{m('10分時点のCS','DIAMOND'):.1f}、シルバーは{m('10分時点のCS','SILVER'):.1f}でした。",
            "差はおよそ5CS。思ったより小さいですが、ランク順にきれいに並んでいます。",
        ]),
        (slide_bars("20分以降、敵陣側で死んだ回数", "20分以降の敵陣側でのデス", means, unit="回"), [
            "逆に、試合の後半になると立場が入れ替わります。",
            f"20分以降に敵陣側で死んだ回数は、シルバーが{m('20分以降の敵陣側でのデス','SILVER'):.2f}回で一番多くなりました。",
            "サイドレーンを押しすぎて捕まる場面が多いと考えられます。",
        ]),
        (slide_effects("ゴールド・シルバー帯で勝敗と関係が強いもの", effects, {
            "チームのタワープレート獲得数": "チームのタワープレート",
            "分間デス数": "デスの少なさ（試合全体）",
            "14分時点の対面ゴールド差": "14分の対面ゴールド差",
            "分間CS（試合全体）": "分間CS",
        }), [
            "最後に、ゴールドとシルバーの試合で、勝ち負けと関係が強かったものです。",
            "一番はチームでのタワープレート、次が試合全体でのデスの少なさでした。",
            "序盤にプレートを取りきって、後半は無理に死なない。これが勝ちにつながっています。",
        ]),
        (slide_text("まとめ", ["① 序盤は怖がりすぎず、プレートを取りに行く",
                             "② 20分以降はサイドで深追いしない",
                             "③ CSはまず10分で＋5を目標に"]), [
            "まとめです。一つ目、序盤は怖がりすぎず、プレートを取りに行く。",
            "二つ目、20分以降はサイドレーンで深追いしない。",
            "三つ目、CSはまず10分で今より5多く取ることを目標にしましょう。",
        ]),
        (slide_text("おわりに", ["各ランク約100試合の小さめのサンプルです",
                               "次回はもっと試合数を増やして検証します"]), [
            "今回は各ランク百試合ほどの、少なめのデータでの結果です。",
            "次回はデータを増やして、もっと詳しく検証します。チャンネル登録して待っていてください。",
        ]),
    ]


# ---- 書き出し --------------------------------------------------------------

def tts(text, path):
    x, sr = pyopenjtalk.tts(text, speed=1.1)
    pad = np.zeros(int(sr * GAP))
    x = np.concatenate([x, pad]).clip(-32768, 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(x.tobytes())


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def main():
    setup_font()
    WORK.mkdir(parents=True, exist_ok=True)
    clips = []
    for si, (draw, lines) in enumerate(script(*load())):
        for li, line in enumerate(lines):
            png, wav, mp4 = (WORK / f"s{si:02d}_{li}.{ext}" for ext in ("png", "wav", "mp4"))
            fig = new_fig()
            draw(fig)
            caption(fig, line)
            fig.savefig(png, facecolor=BG)
            plt.close(fig)
            tts(line, wav)
            ffmpeg("-loop", "1", "-i", str(png), "-i", str(wav), "-shortest",
                   "-c:v", "libx264", "-tune", "stillimage", "-pix_fmt", "yuv420p", "-r", "30",
                   "-c:a", "aac", "-ar", "48000", "-b:a", "160k", str(mp4))
            clips.append(mp4)
    listing = WORK / "clips.txt"
    listing.write_text("".join(f"file '{c.name}'\n" for c in clips))
    out = WORK / "video.mp4"
    ffmpeg("-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out))
    print(f"書き出しました: {out}")


if __name__ == "__main__":
    main()

"""ランク帯ごとの差・勝敗に効く指標・低ランクで勝ちやすいチャンピオンを集計し、
レポート (report.md) とグラフ (PNG) を出力する。"""
import argparse
import logging
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
import pandas as pd

logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)

from extract import MAP_SIZE

TIER_ORDER = ["CHALLENGER", "GRANDMASTER", "MASTER", "DIAMOND", "EMERALD",
              "PLATINUM", "GOLD", "SILVER", "BRONZE", "IRON"]

# (列名, 表示名, 高いほど良いか, ランク帯比較に使うか)
# 対面との差分 (csd/gd/xpd) は同じ試合の2人で打ち消し合いランク帯平均が0になるため、勝敗分析にだけ使う
METRICS = [
    ("cs10", "10分時点のCS", True, True),
    ("cs14", "14分時点のCS", True, True),
    ("cs_pm", "分間CS（試合全体）", True, True),
    ("cs_pm_after20", "20分以降の分間CS", True, True),
    ("csd10", "10分時点の対面CS差", True, False),
    ("csd14", "14分時点の対面CS差", True, False),
    ("gd14", "14分時点の対面ゴールド差", True, False),
    ("xpd14", "14分時点の対面経験値差", True, False),
    ("deaths_pre14", "14分までのデス数", False, True),
    ("gank_deaths_pre14", "14分までのガンクによるデス", False, True),
    ("deaths_pre14_enemy_half", "14分までの敵陣側でのデス", False, True),
    ("late_deaths_enemy_half", "20分以降の敵陣側でのデス", False, True),
    ("deaths_pm", "分間デス数", False, True),
    ("wards_pre14", "14分までのワード設置数", True, True),
    ("control_wards_bought", "コントロールワード購入数", True, True),
    ("vision_score_pm", "分間視界スコア", True, True),
    ("plates", "チームのタワープレート獲得数", True, True),
    ("solo_kills", "ソロキル数", True, True),
    ("objective_participation", "オブジェクト参加率", True, True),
    ("early_top_objective_participation", "グラブ/ヘラルド参加率", True, True),
    ("kill_participation", "キル関与率", True, True),
]
LABEL = {m[0]: m[1] for m in METRICS}


def setup_font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    for cand in ("Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "WenQuanYi Zen Hei"):
        if cand in names:
            plt.rcParams["font.family"] = cand
            break
    plt.rcParams["axes.unicode_minus"] = False


def cohens_d(a, b):
    a, b = a.dropna(), b.dropna()
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    pooled = math.sqrt(((len(a) - 1) * a.var() + (len(b) - 1) * b.var()) / (len(a) + len(b) - 2))
    return (a.mean() - b.mean()) / pooled if pooled else float("nan")


def wilson_lower(wins, n, z=1.96):
    if n == 0:
        return 0.0
    p = wins / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n)


def md_table(df, floatfmt="{:.2f}"):
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = [floatfmt.format(v) if isinstance(v, float) else str(v) for v in r]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def tier_gaps(df, high, low):
    out = []
    for col, label, higher_better, compare in METRICS:
        if not compare or col not in df:
            continue
        d = cohens_d(df[df.tier.isin(high)][col], df[df.tier.isin(low)][col])
        out.append({"指標": label, "col": col, "効果量": d if higher_better else -d})
    res = pd.DataFrame(out).dropna()
    return res.reindex(res["効果量"].abs().sort_values(ascending=False).index)


def win_effects(df, low):
    sub = df[df.tier.isin(low)]
    out = []
    for col, label, higher_better, _ in METRICS:
        if col not in sub:
            continue
        d = cohens_d(sub[sub.win == 1][col], sub[sub.win == 0][col])
        out.append({"指標": label, "col": col, "勝敗との関連": d if higher_better else -d})
    res = pd.DataFrame(out).dropna()
    return res.sort_values("勝敗との関連", ascending=False)


def champion_table(df, group, min_games):
    sub = df[df.tier.isin(group)]
    g = sub.groupby("champion").win.agg(["count", "sum"]).rename(columns={"count": "試合数", "sum": "勝利"})
    g["勝率"] = g["勝利"] / g["試合数"]
    g["ピック率"] = g["試合数"] / len(sub)
    g["勝率下限(95%)"] = [wilson_lower(w, n) for w, n in zip(g["勝利"], g["試合数"])]
    return g[g["試合数"] >= min_games].sort_values("勝率下限(95%)", ascending=False)


def plot_gaps(gaps, path):
    top = gaps.head(12).iloc[::-1]
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(top["指標"], top["効果量"], color=["#2a7ab9" if v > 0 else "#c0504d" for v in top["効果量"]])
    ax.axvline(0, color="#444", lw=0.8)
    ax.set_xlabel("効果量（＋＝上位ランクの方が良い）")
    ax.set_title("上位ランクと下位ランクで差が大きい指標")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_metric_bars(df, cols, tiers, path):
    fig, axes = plt.subplots(1, len(cols), figsize=(4 * len(cols), 4))
    axes = [axes] if len(cols) == 1 else axes
    for ax, col in zip(axes, cols):
        means = df.groupby("tier")[col].mean().reindex(tiers)
        ax.bar(means.index, means.values, color="#2a7ab9")
        ax.set_title(LABEL[col], fontsize=11)
        for i, v in enumerate(means.values):
            ax.text(i, v, f"{v:.2f}", ha="center", va="bottom", fontsize=9)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_deaths(deaths, tiers, path, max_minute=14):
    d = deaths[deaths.minute < max_minute]
    fig, axes = plt.subplots(1, len(tiers), figsize=(4 * len(tiers), 4.3))
    axes = [axes] if len(tiers) == 1 else axes
    for ax, tier in zip(axes, tiers):
        t = d[d.tier == tier]
        ax.scatter(t.x, t.y, s=8, alpha=0.35, c=["#c0504d" if g else "#2a7ab9" for g in t.gank])
        ax.plot([0, MAP_SIZE], [MAP_SIZE, 0], color="#888", lw=1, ls="--")  # 川
        ax.set_xlim(0, MAP_SIZE)
        ax.set_ylim(0, MAP_SIZE)
        ax.set_aspect("equal")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{tier}（{len(t)}件）")
    fig.suptitle(f"{max_minute}分までのトップのデス位置（左下=自陣、赤=敵ジャングラー関与）")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--out", default="output")
    ap.add_argument("--high", default="DIAMOND,EMERALD")
    ap.add_argument("--low", default="GOLD,SILVER")
    ap.add_argument("--min-games", type=int, default=20)
    args = ap.parse_args()

    setup_font()
    data_dir, out = Path(args.data_dir), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    high, low = args.high.split(","), args.low.split(",")

    df = pd.read_csv(data_dir / "top_laners.csv")
    deaths_path = data_dir / "deaths.csv"
    deaths = pd.read_csv(deaths_path) if deaths_path.exists() else pd.DataFrame()
    tiers = [t for t in TIER_ORDER if t in set(df.tier)]
    patches = ", ".join(sorted(df.patch.astype(str).unique()))

    present = [m[0] for m in METRICS if m[3] and m[0] in df]
    tier_means = df.groupby("tier")[present].mean().reindex(tiers).T
    tier_means.index = [LABEL[c] for c in tier_means.index]
    tier_means.to_csv(out / "tier_means.csv")

    gaps = tier_gaps(df, high, low)
    effects = win_effects(df, low)
    champs_low = champion_table(df, low, args.min_games)
    champs_high = champion_table(df, high, args.min_games)
    gaps.to_csv(out / "tier_gaps.csv", index=False)
    effects.to_csv(out / "win_effects_low.csv", index=False)
    champs_low.to_csv(out / "champions_low.csv")
    champs_high.to_csv(out / "champions_high.csv")

    plot_gaps(gaps, out / "tier_gaps.png")
    plot_metric_bars(df, list(gaps.col.head(4)), tiers, out / "top_metric_bars.png")
    if not deaths.empty:
        plot_deaths(deaths, tiers, out / "deaths_map.png")

    both = champs_low.join(champs_high[["勝率", "試合数"]], rsuffix="(上位)", how="inner")
    both["勝率差(下位-上位)"] = both["勝率"] - both["勝率(上位)"]
    both = both.sort_values("勝率差(下位-上位)", ascending=False)

    def pct(t, cols):
        t = t.copy()
        for c in cols:
            t[c] = (t[c] * 100).map(lambda v: f"{v:.1f}%")
        return t

    counts = df.groupby("tier").match_id.nunique().reindex(tiers)
    lines = [
        "# トップレーン ランク帯比較レポート",
        "",
        f"- パッチ: {patches}",
        "- 試合数: " + " / ".join(f"{t} {n}" for t, n in counts.items()),
        f"- 上位グループ: {', '.join(high)} / 下位グループ: {', '.join(low)}",
        "- ランク帯は「試合を拾ったプレイヤーのランク」で分類しています（同じ試合の他プレイヤーとは多少ずれます）",
        "",
        "## 1. 上位と下位で差が大きい指標（動画テーマの候補）",
        "効果量の目安: 0.2=小さい差, 0.5=中くらい, 0.8=大きい差。＋は上位の方が良いことを示す。",
        "",
        md_table(gaps[["指標", "効果量"]].head(10)),
        "",
        "![](tier_gaps.png)",
        "![](top_metric_bars.png)",
        "",
        "## 2. ランク帯ごとの平均値",
        "",
        md_table(tier_means.reset_index().rename(columns={"index": "指標"})),
        "",
        "## 3. ゴールド・シルバー帯で勝敗と関連が強い指標",
        "※相関であり因果ではありません（例: デスが少ないのは勝っているから、という逆向きの関係も含む）。",
        "",
        md_table(effects[["指標", "勝敗との関連"]].head(10)),
        "",
    ]
    if not deaths.empty:
        lines += ["## 4. 序盤のデス位置", "", "![](deaths_map.png)", ""]
    lines += [
        f"## 5. 下位帯で勝ちやすいトップチャンピオン（{args.min_games}試合以上）",
        "勝率下限(95%)＝試合数が少ないことを考慮した控えめな勝率。",
        "",
        md_table(pct(champs_low.head(10).reset_index(), ["勝率", "ピック率", "勝率下限(95%)"])),
        "",
        "## 6. 下位帯でだけ勝率が高いチャンピオン（下位で強く上位で弱い＝低ランク向け）",
        "",
        md_table(pct(both.head(10).reset_index()[["champion", "試合数", "勝率", "試合数(上位)", "勝率(上位)", "勝率差(下位-上位)"]],
                     ["勝率", "勝率(上位)", "勝率差(下位-上位)"])),
        "",
    ]
    (out / "report.md").write_text("\n".join(lines))
    print(f"レポートを書き出しました: {out / 'report.md'}")


if __name__ == "__main__":
    main()

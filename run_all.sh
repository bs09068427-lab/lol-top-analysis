#!/usr/bin/env bash
# 収集 → 抽出 → 分析 を一括実行する。追加の引数は collect.py に渡される。
set -euo pipefail
cd "$(dirname "$0")"
python3 collect.py "$@"
python3 extract.py
python3 analyze.py
echo "完了: output/report.md を確認してください"

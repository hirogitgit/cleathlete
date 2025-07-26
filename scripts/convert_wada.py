#!/usr/bin/env python
# coding: utf-8
"""
PDF → YAML/JSON 変換スクリプト（WADA 禁止表 2025 年版）
Usage:
    python scripts/convert_wada.py
"""

import json
import re
from pathlib import Path

import pdfplumber
import yaml

# ---------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------
PDF_PATH = Path("data/raw/wada2025.pdf")
OUT_YAML = Path("data/processed/prohibited_2025.yaml")
OUT_JSON = Path("data/processed/prohibited_2025.json")

# 出力フォルダが無い場合は作成
OUT_YAML.parent.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------
# ノイズ行判定関数
# ---------------------------------------------------------------------
def is_noise(line: str) -> bool:
    """
    以下に当てはまる行は物質名ではなく脚注／説明文とみなして除外。
    - しきい値説明: 'Prohibited when ... micrograms'
    - 長文説明: 'These substances are included in the ...'
    - OOC 注:    'Also prohibited Out-of-Competition'
    """
    noise_patterns = [
        r"Prohibited when",
        r"These substances",
        r"Also prohibited",
    ]
    return any(re.search(p, line, re.I) for p in noise_patterns)


# ---------------------------------------------------------------------
# PDF から物質名を抽出
# ---------------------------------------------------------------------
bullet_re = re.compile(r"^[•\*\-]\s*(.+)")  # 箇条書き行（• * - で始まる）

names: list[str] = []
buffer = ""

with pdfplumber.open(PDF_PATH) as pdf:
    for page in pdf.pages:
        for raw_line in page.extract_text().splitlines():
            line = raw_line.strip()

            m = bullet_re.match(line)
            if not m:
                continue  # 箇条書き以外はスキップ

            text = m.group(1)

            # (1) 前行がカンマ末尾で切れていた場合は連結
            if buffer:
                text = buffer + " " + text
                buffer = ""

            # (2) ノイズ行は除外
            if is_noise(text):
                continue

            # (3) 行末がカンマなら次行と連結するためバッファへ
            if text.endswith(","):
                buffer = text.rstrip(",")
                continue

            # (4) 単語中に紛れ込んだスペースを除去（C athine → Cathine）
            text = re.sub(r"(?<=\w)\s+(?=\w)", "", text)

            # (5) 括弧以降は補足情報なのでカット
            text = text.split(" (")[0].strip()

            names.append(text)

# ---------------------------------------------------------------------
# データ構造を作成（最小例: S6/STIMULANTS のみ）
# ---------------------------------------------------------------------
data = {
    "S6": {
        "STIMULANTS": sorted(set(names))
    }
}

# ---------------------------------------------------------------------
# YAML / JSON に保存
# ---------------------------------------------------------------------
with OUT_YAML.open("w", encoding="utf-8") as f:
    yaml.safe_dump(data, f, allow_unicode=True)

with OUT_JSON.open("w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✅ 変換完了: {len(names)} 物質を抽出しました。")
print(f" - YAML:  {OUT_YAML}")
print(f" - JSON:  {OUT_JSON}")

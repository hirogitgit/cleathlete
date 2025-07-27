#!/usr/bin/env python
"""
converter.py  –  2 段組 PDF に対応した左右列順の最小パーサ

1. ページ幅を 50% で左右に分割して左→右の順にテキスト走査
2. 章タイトル (S1 など) を検知後、箇条書き行 (•, *, -) をそのまま収集
3. YAML / JSON を data/processed/ に出力
"""

from __future__ import annotations
import json
import re
import sys
from pathlib import Path
from typing import Dict, List

import pdfplumber
import yaml

# ──────────────── パス設定 ────────────────
ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = ROOT / "data" / "raw"
OUT_DIR = ROOT / "data" / "processed"
DEFAULT_PDF = RAW_DIR / "wada2025.pdf"

OUT_DIR.mkdir(parents=True, exist_ok=True)

# ──────────────── パターン ────────────────
SEC_RE   = re.compile(r"^(S\d|M\d|P\d)\b")   # 章タイトル
BULLET_RE = re.compile(r"^[•*-]\s*(.+)")     # 箇条書き
ALL_CAPS  = re.compile(r"^[A-Z0-9][A-Z0-9 \-()]+$")  # サブヘッダ

# ──────────────── PDF 解析 ────────────────
def parse_pdf(pdf: Path) -> Dict[str, Dict[str, List[str]]]:
    data: Dict[str, Dict[str, List[str]]] = {}
    sec = sub = None

    with pdfplumber.open(pdf) as pdfdoc:
        for page in pdfdoc.pages:
            mid_x = page.width / 2
            # 左列 → 右列 の順で処理
            for bbox in [(0, 0, mid_x, page.height), (mid_x, 0, page.width, page.height)]:
                col = page.crop(bbox)
                for raw in col.extract_text().splitlines():
                    line = raw.strip()

                    if SEC_RE.match(line):
                        sec = line.split()[0]
                        data.setdefault(sec, {})
                        sub = None
                        continue

                    if sec is None:
                        continue  # 章確定前は無視

                    if ALL_CAPS.match(line) and not BULLET_RE.match(line):
                        sub = line.title()
                        data[sec].setdefault(sub, [])
                        continue

                    m = BULLET_RE.match(line)
                    if m:
                        bucket = sub or "Misc"
                        data[sec].setdefault(bucket, []).append(m.group(1).strip())

    return data

# ──────────────── 出力 ────────────────
def convert(pdf: Path) -> None:
    year = re.search(r"(\d{4})", pdf.stem)
    stem = f"prohibited_{year.group(1) if year else pdf.stem}"
    yaml_p = OUT_DIR / f"{stem}.yaml"
    json_p = OUT_DIR / f"{stem}.json"

    parsed = parse_pdf(pdf)
    yaml_p.write_text(yaml.safe_dump(parsed, allow_unicode=True), encoding="utf-8")
    json_p.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ 変換完了\n  YAML: {yaml_p}\n  JSON: {json_p}")

# ──────────────── CLI ────────────────
def cli() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not target.exists():
        sys.exit(f"❌ PDF が見つかりません: {target}")
    convert(target)

if __name__ == "__main__":
    cli()

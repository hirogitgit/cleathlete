#!/usr/bin/env python
"""
convert_wada.py  ─ WADA 禁止表 PDF → YAML / JSON
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List

import pdfplumber
import yaml

# ─────────────── パス設定 ───────────────
PROJ_ROOT   = Path(__file__).resolve().parents[1]
RAW_DIR     = PROJ_ROOT / "data" / "raw"
PROC_DIR    = PROJ_ROOT / "data" / "processed"
DEFAULT_PDF = RAW_DIR / "wada2025.pdf"

PROC_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────── 正規表現 ───────────────
SECTION_RE   = re.compile(r"^(S\d|M\d|P\d)\b")
BULLET_LINE  = re.compile(r"^[•*-]\s*(.+)")
SUBHEAD_ALL  = re.compile(r"^[A-Z0-9][A-Z0-9 \-()]+$")

BULLET_SEP_RE = re.compile(r"\s*[•;・·●‧∙]\s*")

EXCLUDE_PAT = re.compile(
    r"""
    \d\ ?(micrograms?|µg|μg|mg/ml|ml|hours?) |
    \b(maximum|inhaled|out\-of\-competition)\b |
    Archery|Shooting|Cycling
    """,
    re.I | re.X,
)

# ─────────────── ユーティリティ ───────────────
def looks_like_substance(name: str) -> bool:
    return not EXCLUDE_PAT.search(name)

def clean_token(tok: str) -> str:
    tok = tok.strip(" *;")
    tok = re.sub(r"\s{2,}", " ", tok)
    tok = re.split(r"\s*\(", tok, 1)[0].strip()
    tok = tok.rstrip(")")
    return tok

# ─────────────── 解析本体 ───────────────
def parse_pdf(pdf_path: Path) -> Dict[str, Dict[str, List[str]]]:
    data: Dict[str, Dict[str, List[str]]] = {}
    current_sec = current_sub = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for raw_line in page.extract_text().splitlines():
                line = raw_line.strip()

                # 章タイトル
                if SECTION_RE.match(line):
                    current_sec = line.split()[0]
                    data.setdefault(current_sec, {})
                    current_sub = None
                    continue

                # サブヘッダ（全大文字）
                if SUBHEAD_ALL.match(line) and not BULLET_LINE.match(line):
                    if current_sec is None:       # ← 追加ガード
                        continue
                    current_sub = line.title()
                    data[current_sec].setdefault(current_sub, [])
                    continue

                # 箇条書き行
                m = BULLET_LINE.match(line)
                if not m or current_sec is None:  # ← 追加ガード
                    continue

                for token in BULLET_SEP_RE.split(m.group(1)):
                    token = clean_token(token)
                    if token and looks_like_substance(token):
                        bucket = current_sub or "Misc"
                        data[current_sec].setdefault(bucket, []).append(token)

    return data

# ─────────────── ファイル出力 ───────────────
def convert(pdf_path: Path) -> None:
    year_match = re.search(r"(\d{4})", pdf_path.stem)
    outstem = f"prohibited_{year_match.group(1) if year_match else pdf_path.stem}"
    yaml_path = PROC_DIR / f"{outstem}.yaml"
    json_path = PROC_DIR / f"{outstem}.json"

    result = parse_pdf(pdf_path)

    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(result, f, allow_unicode=True)
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"✅ 変換完了:\n  YAML → {yaml_path}\n  JSON → {json_path}")

# ─────────────── CLI ───────────────
if __name__ == "__main__":
    pdf_file = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not pdf_file.exists():
        sys.exit(f"❌ PDF が見つかりません: {pdf_file}")
    convert(pdf_file)

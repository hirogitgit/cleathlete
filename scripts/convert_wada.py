#!/usr/bin/env python
"""
convert_wada.py

WADA 禁⽌表（PDF）を走査し、物質名を階層付き YAML / JSON に変換。
- S1, S2 ... の「章」→ 第1階層
- サブヘッダ（ANABOLIC ANDROGENIC STEROIDS など）→ 第2階層
- 箇条書きの物質名 → リスト要素

Usage:
    # デフォルト（プロジェクト標準パス）
    python scripts/convert_wada.py

    # 任意の PDF を渡す
    python scripts/convert_wada.py /path/to/2026_list.pdf
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List

import pdfplumber
import yaml

# ---------------------------------------------------------------------------
# 設定（環境毎に変えるのはここだけで済むようにする）
# ---------------------------------------------------------------------------
PROJ_ROOT = Path(__file__).resolve().parents[1]  # cleathlete/
RAW_DIR = PROJ_ROOT / "data" / "raw"
PROC_DIR = PROJ_ROOT / "data" / "processed"
DEFAULT_PDF = RAW_DIR / "wada2025.pdf"  # ファイル名は適宜変更

PROC_DIR.mkdir(parents=True, exist_ok=True)

# 正規表現パターン
SECTION_RE = re.compile(r"^(S\d|M\d|P\d)\b")         # S1, M2, など
BULLET_RE = re.compile(r"^[•*-]\s*(.+)")             # 箇条書き行
SUBHEAD_UPPER = re.compile(r"^[A-Z0-9][A-Z0-9 \-()]+$")  # 全大文字の見出し

# 物質らしくない行を除外するキーワード
EXCLUDE_PAT = re.compile(
    r"""
    \d                              # 数字（用量）
    |micrograms?|µg|μg|mg/ml
    |maximum|hours?|inhaled
    |Out-of-Competition
    |Archery|Shooting|Cycling       # 競技名など（例示）
    """,
    re.I | re.X,
)


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------
def looks_like_substance(text: str) -> bool:
    """数字・用量・注釈を含む行を False にする簡易フィルタ"""
    return not EXCLUDE_PAT.search(text)


def clean_token(token: str) -> str:
    """括弧書き以降などを取り除いた物質名を返す"""
    return re.split(r"\s*\(", token, 1)[0].strip()


# ---------------------------------------------------------------------------
# 変換メイン
# ---------------------------------------------------------------------------
def parse_pdf(pdf_path: Path) -> Dict[str, Dict[str, List[str]]]:
    data: Dict[str, Dict[str, List[str]]] = {}
    current_sec = current_sub = None

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for raw_line in page.extract_text().splitlines():
                line = raw_line.strip()

                # 章タイトル
                if SECTION_RE.match(line):
                    current_sec = line.split()[0]  # "S1" 部分
                    data.setdefault(current_sec, {})
                    current_sub = None
                    continue
                # ------ 章が決まっていない行はすべてスキップ ------
                if current_sec is None:
                    continue

                # 全大文字のサブヘッダ
                if SUBHEAD_UPPER.match(line) and not BULLET_RE.match(line):
                    current_sub = line.title()
                    data[current_sec].setdefault(current_sub, [])
                    continue

                # 箇条書きの物質行
                m = BULLET_RE.match(line)
                if m:
                    # "Buprenorphine • Fentanyl" のような複数物質を分割
                    for token in re.split(r"[•;・]", m.group(1)):
                        token = clean_token(token)
                        if token and looks_like_substance(token):
                            bucket = current_sub or "Misc"
                            data[current_sec].setdefault(bucket, []).append(token)
    return data


def convert(pdf_path: Path) -> None:
    """PDF を読み込み、YAML と JSON に保存"""
    output_stem = f"prohibited_{pdf_path.stem[-4:]}"  # 末尾4桁が年度なら流用
    yaml_path = PROC_DIR / f"{output_stem}.yaml"
    json_path = PROC_DIR / f"{output_stem}.json"

    data = parse_pdf(pdf_path)

    with yaml_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    print(f"✅ 変換完了\n  YAML: {yaml_path}\n  JSON: {json_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    pdf_file = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PDF
    if not pdf_file.exists():
        sys.exit(f"❌ PDF が見つかりません: {pdf_file}")
    convert(pdf_file)

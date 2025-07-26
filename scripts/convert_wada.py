import re, json, yaml, pdfplumber, pathlib

PDF_PATH = "data/raw/wada2025.pdf"
OUT_YAML = "data/processed/prohibited_2025.yaml"
OUT_JSON = "data/processed/prohibited_2025.json"

# ① PDF を読み込んで文字列リスト化
pages_text = []
with pdfplumber.open(PDF_PATH) as pdf:
    for page in pdf.pages:
        pages_text.extend(page.extract_text().splitlines())

# ② 正規表現パターン
section_re = re.compile(r"^(S\d|M\d|P\d)\b")        # 例: S1, M2, P1
subhead_re = re.compile(r"^[•*-]\s*(.+)")           # 先頭が • や - の行
chem_re    = re.compile(r"^•\s*(.+)")               # 箇条書きの物質名

data = {}
current_sec = current_sub = None

for line in pages_text:
    line = line.strip()
    # 章タイトル（S0, S1 …）
    if section_re.match(line):
        current_sec = line.split()[0]               # "S1" 部分
        data.setdefault(current_sec, {})
        current_sub = None                          # サブヘッダをリセット
        continue

    # サブヘッダ（例: "ANABOLIC ANDROGENIC STEROIDS (AAS)"）
    if subhead_re.match(line) and line.isupper():
        current_sub = subhead_re.match(line).group(1).title()
        data[current_sec].setdefault(current_sub, [])
        continue

    # 物質名
    if chem_re.match(line):
        chem = chem_re.match(line).group(1).split(" (")[0]  # 括弧以降カット
        # デフォルトのサブヘッダが無い場合は "Misc" に入れる
        bucket = current_sub or "Misc"
        data[current_sec].setdefault(bucket, []).append(chem)

# ③ YAML と JSON に保存
with open(OUT_YAML, "w", encoding="utf-8") as f:
    yaml.safe_dump(data, f, allow_unicode=True)

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✅ 変換完了: {pathlib.Path(OUT_YAML).resolve()}")

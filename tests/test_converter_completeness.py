# tests/test_converter_completeness.py
import re, yaml, pdfplumber, pathlib

PDF_FILE   = pathlib.Path("data/raw/wada2025.pdf")
YAML_FILE  = pathlib.Path("data/processed/prohibited_2025.yaml")

def extract_names_from_pdf(pdf_path: pathlib.Path) -> set[str]:
    bullet = re.compile(r"^[•*-]\s*(.+)")  # 先頭記号付き行
    names   = set()
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in page.extract_text().splitlines():
                m = bullet.match(line.strip())
                if m:
                    # "(例: Boldenone (including…))" の括弧以降は切り捨て
                    names.add(m.group(1).split(" (")[0].strip())
    return names

def extract_names_from_yaml(yaml_path: pathlib.Path) -> set[str]:
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    names = {chem for sec in data.values() for lst in sec.values() for chem in lst}
    return names

def test_pdf_vs_yaml():
    pdf_names  = extract_names_from_pdf(PDF_FILE)
    yaml_names = extract_names_from_yaml(YAML_FILE)

    missing_in_yaml = pdf_names - yaml_names
    extra_in_yaml   = yaml_names - pdf_names

    assert not missing_in_yaml, f"抜け: {missing_in_yaml}"
    assert not extra_in_yaml,   f"余分: {extra_in_yaml}"

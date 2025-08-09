# -*- coding: utf-8 -*-
"""
判定エンジン core module
Flow:
 1) user_input → normalize
 2) 6 CSV parallel search
 3) cache / product / API fallback
返り値: dict {color, reason, extra_question?}
"""
import pandas as pd
import re
# --- add at top ---
import requests

RXNAV = "https://rxnav.nlm.nih.gov/REST"

def rxnorm_find_rxcui(term: str):
    """Find best Rxcui for a name (brand or ingredient)"""
    q = term.strip()
    # 1) exact-ish search
    r = requests.get(f"{RXNAV}/rxcui.json", params={"name": q, "search": 2}, timeout=10)
    js = r.json()
    ids = js.get("idGroup", {}).get("rxnormId") or []
    if ids:
        return ids[0]
    # 2) fallback approximate
    r = requests.get(f"{RXNAV}/approximateTerm.json", params={"term": q, "maxEntries": 1}, timeout=10)
    cand = (r.json().get("approximateGroup", {}).get("candidate") or [])
    return cand[0]["rxcui"] if cand else None

def rxnorm_lookup(term: str):
    """
    Return:
      {"kind":"single","inn":"umeclidinium bromide","rxcui":"..","atc":"C07AB02"}  or
      {"kind":"combo","inn":"<brand or MIN>","rxcui":"..","compositions":[{"inn":"...","rxcui":"..."},...]}
      {"kind":"none"}
    """
    rxcui = rxnorm_find_rxcui(term)
    if not rxcui:
        return {"kind":"none"}

    # what is this rxcui? get properties (TTY)
    prop = requests.get(f"{RXNAV}/rxcui/{rxcui}/properties.json", timeout=10).json()
    tty  = (prop.get("properties") or {}).get("tty", "")

    # get ingredients via has_ingredient
    rel = requests.get(f"{RXNAV}/rxcui/{rxcui}/related.json", params={"rela":"has_ingredient"}, timeout=10).json()
    ingred = []
    for g in rel.get("relatedGroup", {}).get("conceptGroup", []) or []:
        for p in g.get("conceptProperties", []) or []:
            ingred.append({"inn": p["name"].lower(), "rxcui": p["rxcui"]})

    # If no related ingredients and TTY == IN → it's a plain ingredient
    if not ingred and tty == "IN":
        # try ATC via RxClass
        atc = None
        cls = requests.get(f"{RXNAV}/rxclass/class/byRxcui.json", params={"rxcui": rxcui}, timeout=10).json()
        for c in cls.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", []) or []:
            cl = c.get("rxclassMinConceptItem") or {}
            if (cl.get("classType") or "").startswith("ATC"):
                atc = cl.get("classId")  # e.g. "C07AB02"
                break
        return {"kind":"single", "inn": (prop["properties"]["name"]).lower(), "rxcui": rxcui, "atc": atc}

    # If has ≥2 ingredients → combo
    if len(ingred) >= 2:
        return {"kind":"combo", "inn": (prop["properties"]["name"]).lower(), "rxcui": rxcui, "compositions": ingred}

    # 1 ingredient found (e.g., brand SBD/SCD pointing to an IN)
    if len(ingred) == 1:
        ing = ingred[0]
        # pull ATC on ingredient rxcui
        atc = None
        cls = requests.get(f"{RXNAV}/rxclass/class/byRxcui.json", params={"rxcui": ing["rxcui"]}, timeout=10).json()
        for c in cls.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", []) or []:
            cl = c.get("rxclassMinConceptItem") or {}
            if (cl.get("classType") or "").startswith("ATC"):
                atc = cl.get("classId")
                break
        return {"kind":"single", "inn": ing["inn"], "rxcui": ing["rxcui"], "atc": atc}

    return {"kind":"none"}

# ---------- 0. Data load ----------
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent  # cleathlete/

FILES = {
    "green"   : BASE_DIR / "data" / "csv" / "always_green.csv",
    "red"     : BASE_DIR / "data" / "csv" /"always_red.csv",
    "route_dose"    : BASE_DIR / "data" / "csv" /"ask_route_and_dose.csv",
    "period"        : BASE_DIR / "data" / "csv" /"ask_period.csv",
    "period_route"  : BASE_DIR / "data" / "csv" /"ask_period_and_route.csv",
    "period_urine"  : BASE_DIR / "data" / "csv" /"ask_period_and_urine_caution.csv",
    "cache_sub"     : BASE_DIR / "data" / "csv" /"substances_cache.csv",
    "cache_prod"    : BASE_DIR / "data" / "csv" /"product_compositions.csv",
    "sections"      : BASE_DIR / "data" / "csv" /"sections.csv",
    "allowed"       : BASE_DIR / "data" / "csv" /"allowed_atc_code_prefix.csv",
    "sports"        : BASE_DIR / "data" / "csv" /"sports_rules.csv",
}
df = {k: pd.read_csv(v, keep_default_na=False) for k, v in FILES.items()}

def _split_tokens(s):
    return [tok.strip().upper() for tok in str(s).split(";") if tok and str(tok).strip()]

def _build_section_prefix_index(sections_df):
    mp = {}
    for _, row in sections_df.iterrows():
        sec = str(row.get("section_code","")).strip()
        for tok in _split_tokens(row.get("atc_code_prefix","")):
            mp[tok] = sec
    keys = sorted(mp.keys(), key=len, reverse=True)  # 最長一致用に降順
    return mp, keys

def _build_allowed_prefix_index(allowed_df):
    toks = set()
    if "atc_code_prefix" in allowed_df.columns:
        for v in allowed_df["atc_code_prefix"]:
            toks.update(_split_tokens(v))
    keys = sorted(toks, key=len, reverse=True)
    return toks, keys

SECTIONS_PREFIX_MAP, SECTIONS_PREFIXES_SORTED = _build_section_prefix_index(df["sections"])
ALLOWED_PREFIX_SET, ALLOWED_PREFIXES_SORTED   = _build_allowed_prefix_index(df["allowed"])

# ---------- 1. Normalize ----------
SALT_REGEX = re.compile(r'\b(hydrochloride|bromide|sulfate|tartrate|maleate|hydrate)\b', re.I)
def norm(term: str) -> str:
    term = term.lower().strip()
    term = SALT_REGEX.sub('', term).strip()
    return re.sub(r'\s+', ' ', term)

# ---------- 2. Core judgment ----------
def _ask(fields, provisional="yellow", why=""):
    # fields: list of {"field": "period"|"route"|"dose_24h", "options":[...], "hint": "..."}
    return {"status":"ask", "provisional_color": provisional, "reason": why, "need": fields}


def judge(term, sport_code="GEN", period=None, route=None, dose_24h=None):
    t = norm(term)
    sport_code_norm = str(sport_code).strip().upper().replace(" ", "")

    # 1) always green
    row = df["green"][df["green"]["inn"] == t]
    if not row.empty:
        return {"status":"final","color":"green","reason":"Allowed by whitelist"}

    # 2) ask_route_and_dose (S3 β2)
    row = df["route_dose"][df["route_dose"]["inn"] == t]
    if not row.empty:
        r = row.iloc[0]
        # need route?
        if not route:
            return _ask([{"field":"route","options":[r.permitted_route],"hint":"Select administration route"}],
                        "yellow","S3 requires route check")
        # route NG → 即赤
        if r.permitted_route and route != r.permitted_route:
            return {"status":"final","color":"red","reason":"Prohibited route for S3"}
        # need dose?
        if dose_24h is None:
            return _ask([{"field":"dose_24h","hint":f"24h total dose (max {r.maximum_dose} {r.dose_unit})"}],
                        "yellow","S3 requires 24h dose check")
        # dose check
        if float(dose_24h) > float(r.maximum_dose):
            return {"status":"final","color":"red","reason":"24h dose exceeds limit"}
        # urine caution (salbutamol/formoterol only)
        if pd.notna(r.get("urine_threshold_ng_ml", None)):
            if period is None:
                return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}],
                            "yellow","Urine threshold warning applies in-competition")
            if period == "in":
                return {"status":"final","color":"yellow",
                        "reason":f"In-competition urine > {int(r.urine_threshold_ng_ml)} ng/mL ⇒ AAF unless PK study"}
        return {"status":"final","color":"green","reason":"Within inhaled dose limit"}

    # 3) ask_period (S6/S7/S8/P1)
    row = df["period"][df["period"]["inn"] == t]
    if not row.empty:
        sec = row.section_code.iloc[0]
        # β-blocker (P1) はスポーツで上書き
        if sec == "P1":
            sr = df["sports"]
            mask = (sr["prohibited_section"]=="P1") & (sr["sport_code"].str.upper().str.replace(" ","",regex=False)==sport_code_norm)
            hit = sr[mask]
            sport_periodo = hit.prohibited_periodo.iloc[0] if not hit.empty else row.prohibited_period.iloc[0]  # 'both' or 'in'
            if sport_periodo == "both":
                return {"status":"final","color":"red","reason":"P1 beta-blocker prohibited in this sport (both)"}
            # sport_periodo == 'in' → 期間が必要
            if period is None:
                return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}],
                            "yellow","P1 period rule")
            color = "red" if period=="in" else "green"
            return {"status":"final","color":color,"reason":"P1 period rule (in only)"}
        # S6/S7/S8 一般
        if period is None:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}],
                        "yellow",f"{sec} period rule")
        color = "red" if period=="in" else "green"
        return {"status":"final","color":color,"reason":f"{sec} period rule"}

    # 4) ask_period_and_route (S9, epinephrine, imidazoline etc.)
    row = df["period_route"][df["period_route"]["inn"] == t]
    if not row.empty:
        pr = row.iloc[0]
        # period 未入力ならまず期間
        if period is None:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}],
                        "yellow","Route/period rule")
        if period == "out":
            return {"status":"final","color":"green","reason":"Out-of-competition allowed"}
        # in-competition → route 必須
        if not route:
            opts = str(pr.permitted_route).split(';') if pr.permitted_route else []
            return _ask([{"field":"route","options":opts or None,"hint":"Select administration route"}],
                        "yellow","In-competition requires route decision")
        red_routes = set(str(pr.prohibited_route).split(';')) if pr.prohibited_route else set()
        if route in red_routes:
            return {"status":"final","color":"red","reason":"Prohibited route in-competition"}
        return {"status":"final","color":"green","reason":"Permitted route in-competition"}

    # 5) ask_period_and_urine_caution (S6 thresholds)
    row = df["period_urine"][df["period_urine"]["inn"] == t]
    if not row.empty:
        if period is None:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}],
                        "yellow","Urine threshold rule")
        if period == "in":
            th = int(row.urine_threshold_ng_ml.iloc[0])
            return {"status":"final","color":"yellow","reason":f"In-competition urine > {th} ng/mL ⇒ AAF"}
        return {"status":"final","color":"green","reason":"Out-of-competition allowed"}

    # 6) always red
    if not df["red"][df["red"]["inn"] == t].empty:
        return {"status":"final","color":"red","reason":"Always prohibited"}

    # ---------- 3. cache & API fallback ----------
    # 3-1 substances_cache
    row = df["cache_sub"][df["cache_sub"]["inn"] == t]
    if not row.empty:
        return _section_fallback(row.atc_code.iloc[0], period, route)
    
    # 3-2 product_compositions (brand)
    res_brand = _handle_product_brand(t, sport_code, period, route, dose_24h)
    if res_brand is not None:
        return res_brand

    # 3-3 外部 API
    lk = rxnorm_lookup(t)
    if lk["kind"] == "single":
        atc = lk.get("atc")
        if atc:
            # Use existing ATC→section logic
            return _section_fallback(atc, period, route)
        # No ATC from RxNorm → safe fallback: treat as unknown class (S0)
        return {"status":"final","color":"red","reason":"ATC not found via RxNorm (S0-like). Please verify."}

    elif lk["kind"] == "combo":
        # Compose caution message (未収載の運用方針どおり最終赤＋注意)
        comp_names = "; ".join([c["inn"] for c in lk["compositions"]])
        msg = ("この製品はデータベースに未収載です。成分が確認できるまで使用を控えてください。"
               f"（候補成分: {comp_names}）")
        return {"status":"final","color":"red","reason": msg}

    else:
        # not found anywhere
        return {"status":"final","color":"red","reason":"Not found in RxNorm (S0). Possibly unapproved."}

# ---------- helper ----------
def _section_fallback(atc_code, period, route):
    code = str(atc_code).strip().upper()

    # a) sections.csv: 最長一致でセクション特定
    section = None
    matched_prefix = None
    for p in SECTIONS_PREFIXES_SORTED:
        if code.startswith(p):
            section = SECTIONS_PREFIX_MAP[p]
            matched_prefix = p
            break

    if section:
        if section in ("S1","S2","S3","S4","S5"):
            return {"status":"final","color":"red",   "reason":f"{section} class (ATC {matched_prefix})"}
        elif section in ("S6","S7","S8"):
            col = "red" if period=="in" else "green"
            return {"status":"final","color":col,     "reason":f"{section} period rule (ATC {matched_prefix})"}
        elif section == "S9":
            red_routes = {"oral","injectable","rectal","oromucosal","buccal","gingival","sublingual"}
            if period=="in" and route in red_routes:
                return {"status":"final","color":"red","reason":f"S9 prohibited route in-competition (ATC {matched_prefix})"}
            return {"status":"final","color":"green", "reason":f"S9 permitted (ATC {matched_prefix})"}

    # b) allowed_atc_code_prefix.csv: 最長一致で許可
    for p in ALLOWED_PREFIXES_SORTED:
        if code.startswith(p):
            return {"status":"final","color":"green","reason":f"Allowed ATC class ({p})"}

    # c) どちらにも無い → S0相当
    return {"status":"final","color":"red","reason":"Unknown ATC class (S0-like)"}

def _out(color, reason):
    return {"color": color, "reason": reason}

def _find_col(df, candidates):
    for c in candidates:
        if c in df.columns:
            return c
    return None

def _split_semicol(s: str):
    return [x.strip().lower() for x in str(s).split(";") if str(x).strip()]

def _split_semicol(s: str):
    return [x.strip().lower() for x in str(s).split(";") if str(x).strip()]

def _handle_product_brand(t, sport_code, period, route, dose_24h):
    cp = df["cache_prod"]
    row = cp[cp["brand_name"].str.lower().str.strip() == t]
    if row.empty:
        return None  # キャッシュ未ヒット→後段の RxNorm へ

    inns = [s.strip().lower() for s in str(row.iloc[0]["list_name"]).split(";") if s.strip()]

    final_color = "green"
    reasons, needs = [], []
    unknown_components = []  # S0（未承認/未特定）をここで一旦保留

    for inn in inns:
        r = judge(inn, sport_code=sport_code, period=period, route=route, dose_24h=dose_24h)

        # 追加質問が必要なものは ask を集約
        if r.get("status") == "ask":
            needs += r["need"]
            reasons.append(f"{inn}: needs " + ", ".join(n["field"] for n in r["need"]))
            final_color = "yellow"
            continue

        # “真っ赤”の違反は即終了（S1–S5 など）
        if r["color"] == "red" and "S0" not in r.get("reason",""):
            return r

        # S0（Unknown/Unapproved）は保留して最後にまとめて扱う
        if r["color"] == "red" and "S0" in r.get("reason",""):
            unknown_components.append(inn)
            final_color = "yellow"  # いったん黄に倒す（最後に注意文で締める）
            continue

        # 通常の green / yellow
        reasons.append(f"{inn}: {r['reason']}")
        if r["color"] == "yellow":
            final_color = "yellow"

    # まだ質問があれば ask で返す（重複は除去）
    if needs:
        dedup, seen = [], set()
        for n in needs:
            if n["field"] not in seen:
                dedup.append(n); seen.add(n["field"])
        return {"status":"ask","provisional_color": final_color,
                "reason": " / ".join(reasons) if reasons else "Component requires input",
                "need": dedup}

    # S0 が含まれていたら、方針に従って最終化（警告付き赤 or 警告付き黄）
    if unknown_components:
        msg = ("この製品にはデータ未収載の成分があります："
               + "; ".join(unknown_components)
               + "。成分の確認が済むまで使用を控えてください。")
        # ポリシー：厳格運用なら最終 🟥、慎重運用なら 🟨
        return {"status":"final","color":"red", "reason": msg}

    # ここまで赤なし → green / yellow を確定
    return {"status":"final","color": final_color,
            "reason": " / ".join(reasons) if reasons else "All components allowed"}

# ---------- test ----------
if __name__ == "__main__":
    res = judge("ibuprofen", sport_code="GEN", period="in")
    print(res)

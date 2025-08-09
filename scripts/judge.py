import os
import re
from typing import Dict, Any, List, Optional, Tuple, Set

import pandas as pd

try:
    import requests  # 外部APIフォールバック用（任意）
except Exception:  # requests が無い環境でも動くように
    requests = None


# ============================
# 設定
# ============================
DATA_DIR = os.environ.get("CLEATHLETE_DATA_DIR", ".")

FILES = {
    # 6つのINN直指定CSV
    "always_green": "always_green.csv",
    "always_red": "always_red.csv",
    "ask_route_and_dose": "ask_route_and_dose.csv",
    "ask_period": "ask_period.csv",
    "ask_period_and_route": "ask_period_and_route.csv",
    "ask_period_and_urine_caution": "ask_period_and_urine_caution.csv",
    # 補助CSV
    "sections": "sections.csv",
    "sports": "sports_rules.csv",
    "cache_sub": "substances_cache.csv",          # 推奨: inn,rxcui,rxclass_ids,mapped_section_code,normalization_to,aliases
    "cache_prod": "product_compositions.csv",     # brand_name,list_name,aliases
    "classes_map": "classes_map.csv",             # RxClass EPC/MoA → WADAセクション
    "allowed_class": "allowed_class.csv",         # 明確に許可な薬理クラス（EPC/MoAベース）
}


# ============================
# ロード & 前処理
# ============================
DF: Dict[str, pd.DataFrame] = {}

def _csv_path(name: str) -> str:
    return os.path.join(DATA_DIR, FILES[name])

def _safe_read_csv(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()
    try:
        df = pd.read_csv(path, keep_default_na=False)
    except Exception:
        # Excel由来BOMなどを考慮
        df = pd.read_csv(path, keep_default_na=False, encoding="utf-8-sig")
    # 列名BOM/空白除去
    df.columns = (
        df.columns
        .astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    return df

def load_data():
    global DF
    for key in FILES.keys():
        DF[key] = _safe_read_csv(_csv_path(key))

    # 補正：小文字化便利列を足す（inn/brand/aliases 等）
    def add_norm_alias_cols(df: pd.DataFrame, inn_col: str = "inn") -> pd.DataFrame:
        if df.empty:
            return df
        if inn_col in df.columns:
            df["_inn_norm"] = df[inn_col].astype(str).str.lower().str.strip()
        if "aliases" in df.columns:
            df["_aliases_list"] = df["aliases"].fillna("").astype(str).str.lower().apply(
                lambda s: [a.strip() for a in s.split(";") if a.strip()]
            )
        return df

    for name in ["always_green", "always_red",
                 "ask_route_and_dose", "ask_period",
                 "ask_period_and_route", "ask_period_and_urine_caution",
                 "cache_sub"]:
        if not DF[name].empty:
            inn_col = "inn" if "inn" in DF[name].columns else None
            if inn_col:
                DF[name] = add_norm_alias_cols(DF[name], inn_col=inn_col)

    # ブランドCSV
    if not DF["cache_prod"].empty:
        dfp = DF["cache_prod"]
        # 正規化列
        for col in ["brand_name", "list_name", "aliases"]:
            if col not in dfp.columns:
                dfp[col] = ""
        DF["cache_prod"]["_brand_norm"] = dfp["brand_name"].astype(str).str.lower().str.strip()
        DF["cache_prod"]["_aliases_list"] = dfp["aliases"].fillna("").astype(str).str.lower().apply(
            lambda s: [a.strip() for a in s.split(";") if a.strip()]
        )

    # sections（S9ルート判定に使用）
    if not DF["sections"].empty:
        # プロパティ列の標準化
        for col in ["prohibited_route", "permitted_route", "prohibited_period", "initial_flag", "section_code"]:
            if col not in DF["sections"].columns:
                DF["sections"][col] = ""
        # セミコロンをリスト化
        DF["sections"]["_prohibited_routes"] = DF["sections"]["prohibited_route"].fillna("").astype(str).apply(
            lambda s: [x.strip().lower() for x in s.split(";") if str(x).strip()]
        )
        DF["sections"]["_permitted_routes"] = DF["sections"]["permitted_route"].fillna("").astype(str).apply(
            lambda s: [x.strip().lower() for x in s.split(";") if str(x).strip()]
        )

    # classes_map / allowed_class 正規化
    if not DF["classes_map"].empty:
        for col in ["source_system", "source_id", "source_label", "mapped_section_code"]:
            if col not in DF["classes_map"].columns:
                DF["classes_map"][col] = ""
        DF["classes_map"]["_label_norm"] = DF["classes_map"]["source_label"].astype(str).str.lower().str.strip()
        DF["classes_map"]["_key_norm"] = (
            DF["classes_map"]["source_system"].astype(str).str.strip() + ":" +
            DF["classes_map"]["source_id"].astype(str).str.strip()
        ).str.lower()

    if not DF["allowed_class"].empty:
        for col in ["source_system", "source_id", "source_label"]:
            if col not in DF["allowed_class"].columns:
                DF["allowed_class"][col] = ""
        DF["allowed_class"]["_label_norm"] = DF["allowed_class"]["source_label"].astype(str).str.lower().str.strip()
        DF["allowed_class"]["_key_norm"] = (
            DF["allowed_class"]["source_system"].astype(str).str.strip() + ":" +
            DF["allowed_class"]["source_id"].astype(str).str.strip()
        ).str.lower()


# 初期ロード
load_data()


# ============================
# ユーティリティ
# ============================
def _norm(s: Optional[str]) -> str:
    return (s or "").strip().lower()

def _out(color: str, reason: str) -> Dict[str, Any]:
    return {"status": "final", "color": color, "reason": reason}

def _ask(need: List[Dict[str, Any]], provisional_color: str, reason: str) -> Dict[str, Any]:
    return {"status": "ask", "provisional_color": provisional_color, "reason": reason, "need": need}

def _split_semicol(s: str) -> List[str]:
    return [x.strip().lower() for x in str(s).split(";") if str(x).strip()]

def _match_inn(df: pd.DataFrame, term_norm: str) -> pd.DataFrame:
    if df.empty:
        return df
    m = pd.Series([False]*len(df))
    if "_inn_norm" in df.columns:
        m = m | (df["_inn_norm"] == term_norm)
    if "_aliases_list" in df.columns:
        m = m | df["_aliases_list"].apply(lambda lst: term_norm in lst if isinstance(lst, list) else False)
    return df[m]

def _canonical_route(route: Optional[str]) -> Optional[str]:
    if not route:
        return None
    r = _norm(route)
    # 正規化辞書（増やしてOK）
    alias = {
        "nasal": "intranasal",
        "nose": "intranasal",
        "skin": "dermal",
        "eye": "ophthalmic",
        "ear": "otic",
        "mouth": "oromucosal",
        "buccal": "oromucosal",
        "gingival": "oromucosal",
        "sublingual": "oromucosal",
        "topical": "topical",
        "inhalation": "inhaled",
        "inhaled": "inhaled",
        "rectum": "rectal",
        "oral": "oral",
        "po": "oral",
        "iv": "injectable",
        "im": "injectable",
        "sc": "injectable",
        "injectable": "injectable",
        "perianal": "perianal",
        "ophthalmological": "ophthalmic",
        "dental-intracanal": "dental-intracanal",
    }
    return alias.get(r, r)

def _ensure_period(period: Optional[str]) -> Optional[str]:
    if not period:
        return None
    p = _norm(period)
    return "in" if p in ("in", "in-comp", "incompetition", "in_comp", "comp") else ("out" if p in ("out", "out-comp", "out_comp") else None)


# ============================
# 6つのCSV（INN直指定）で判定
# ============================
def _judge_by_6csv(term_norm: str, sport_code: str, period: Optional[str], route: Optional[str], dose_24h: Optional[float]) -> Optional[Dict[str, Any]]:
    # 1) always_green
    row = _match_inn(DF["always_green"], term_norm)
    if not row.empty:
        sec = row.iloc[0].get("section_code", "")
        return _out("green", f"Always allowed{f' ({sec})' if sec else ''}.")

    # 2) always_red
    row = _match_inn(DF["always_red"], term_norm)
    if not row.empty:
        sec = row.iloc[0].get("section_code", "")
        return _out("red", f"Always prohibited{f' ({sec})' if sec else ''}.")

    # 3) ask_route_and_dose（S3の特定β2作動薬など）
    row = _match_inn(DF["ask_route_and_dose"], term_norm)
    if not row.empty:
        r = row.iloc[0]
        permitted_routes = _split_semicol(r.get("permitted_route", ""))
        prohibited_routes = _split_semicol(r.get("prohibited_route", ""))
        max_dose = None
        try:
            max_dose = float(str(r.get("maximum_dose", "")).strip()) if str(r.get("maximum_dose", "")).strip() != "" else None
        except Exception:
            max_dose = None

        asks = []
        if route is None:
            opts = list(set((permitted_routes or []) + (prohibited_routes or [])))
            asks.append({"field": "route", "options": opts or ["inhaled","oral","injectable","topical","nasal","ophthalmic","otic","rectal","oromucosal"], "hint": "Select route"})
        if max_dose is not None and dose_24h is None:
            asks.append({"field": "dose_24h", "hint": f"24h total dose (max {max_dose})"})

        if asks:
            return _ask(asks, "yellow", "Route and/or dose required.")

        # route/dose が揃っていれば評価
        rt = _canonical_route(route)
        if prohibited_routes and rt in prohibited_routes:
            return _out("red", "Prohibited by route.")
        if permitted_routes and rt not in permitted_routes:
            # 想定外の経路は安全側で赤
            return _out("red", "Unsupported route for this substance.")

        if max_dose is not None:
            try:
                if float(dose_24h) > max_dose:
                    return _out("red", f"Dose exceeds limit ({dose_24h} > {max_dose}).")
            except Exception:
                return _ask([{"field":"dose_24h","hint":f"24h total dose (max {max_dose})"}], "yellow", "Dose required.")
        return _out("green", "Within permitted route/dose.")

    # 4) ask_period（S6/S7/S8 の多く）
    row = _match_inn(DF["ask_period"], term_norm)
    if not row.empty:
        p = _ensure_period(period)
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", "Period required.")
        color = "red" if p == "in" else "green"
        sec = row.iloc[0].get("section_code", "")
        return _out(color, f"{sec} period rule.")

    # 5) ask_period_and_route（S9 や一部例外）
    row = _match_inn(DF["ask_period_and_route"], term_norm)
    if not row.empty:
        # 期間 → 経路の順に
        p = _ensure_period(period)
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", "Period required.")
        r = row.iloc[0]
        permitted_routes = _split_semicol(r.get("permitted_route", ""))
        prohibited_routes = _split_semicol(r.get("prohibited_route", ""))

        # inn側に指定が無ければ、sections(S9)の既定を参照
        if not permitted_routes and not prohibited_routes:
            s9 = DF["sections"][DF["sections"]["section_code"].astype(str).str.upper()=="S9"]
            if not s9.empty:
                permitted_routes = s9.iloc[0]["_permitted_routes"]
                prohibited_routes = s9.iloc[0]["_prohibited_routes"]

        if p == "out":
            return _out("green", "Out-of-competition permitted.")
        # in の場合、route が必要
        if route is None:
            opts = list(set((permitted_routes or []) + (prohibited_routes or [])))
            return _ask([{"field":"route","options": opts or ["inhaled","oral","injectable","rectal","oromucosal","nasal","topical","ophthalmic","otic","perianal","dental-intracanal"],"hint":"Select route"}], "yellow", "Route required.")
        rt = _canonical_route(route)
        if rt in prohibited_routes:
            return _out("red", "Prohibited route in-competition.")
        return _out("green", "Permitted route in-competition.")

    # 6) ask_period_and_urine_caution（S6.Bの閾値系など）
    row = _match_inn(DF["ask_period_and_urine_caution"], term_norm)
    if not row.empty:
        p = _ensure_period(period)
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", "Period required.")
        if p == "in":
            return _out("yellow", "Urine threshold caution applies in-competition.")
        return _out("green", "Out-of-competition permitted.")
    return None


# ============================
# substances_cache で判定
# ============================
def _judge_by_cache(term_norm: str, sport_code: str, period: Optional[str], route: Optional[str], dose_24h: Optional[float]) -> Optional[Dict[str, Any]]:
    dfc = DF["cache_sub"]
    if dfc.empty:
        return None
    # マッチ（inn / aliases）
    m = pd.Series([False]*len(dfc))
    if "inn" in dfc.columns:
        m = m | (dfc["inn"].astype(str).str.lower().str.strip() == term_norm)
    if "aliases" in dfc.columns:
        m = m | dfc["aliases"].fillna("").astype(str).str.lower().apply(
            lambda s: term_norm in [a.strip() for a in s.split(";") if a.strip()]
        )
    row = dfc[m]
    if row.empty:
        return None

    r = row.iloc[0]
    sec = str(r.get("mapped_section_code","")).strip().upper()  # S1..S9, P1, ALLOWED, S0
    if not sec:
        return None

    # ALLOWED
    if sec == "ALLOWED":
        return _out("green", "Allowed class (cache).")

    # S0（未知/未承認）
    if sec == "S0":
        return _out("red", "Unknown/Unapproved (S0) via cache.")

    # P1（βブロッカー：競技別）
    if sec == "P1":
        # スポーツ規則参照
        dfs = DF["sports"]
        period_rule = "in"  # 既定
        if not dfs.empty and "sport_code" in dfs.columns:
            rs = dfs[dfs["sport_code"].astype(str).str.strip().str.upper() == sport_code.upper()]
            if not rs.empty:
                # 'both' or 'in' を想定
                period_rule = str(rs.iloc[0].get("prohibited_period", "in")).strip().lower()
        p = _ensure_period(period)
        if period_rule == "both":
            return _out("red", "P1 prohibited in this sport (both).")
        # in ルール
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", "Period required for P1.")
        return _out("red" if p=="in" else "green", "P1 period rule.")

    # S1-S5（常時禁止）
    if sec in ("S1","S2","S3","S4","S5"):
        # S3 は本来一部例外・用量等があるが、ここはキャッシュ確定ルートなので赤で良い
        return _out("red", f"{sec} prohibited.")

    # S6/S7/S8（期間ルール）
    if sec in ("S6","S7","S8"):
        p = _ensure_period(period)
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", f"{sec} period rule.")
        return _out("red" if p=="in" else "green", f"{sec} period rule.")

    # S9（期間＋経路）
    if sec == "S9":
        p = _ensure_period(period)
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", "S9 period required.")
        # sections.csv からS9の既定ルート集合
        s9 = DF["sections"][DF["sections"]["section_code"].astype(str).str.upper()=="S9"]
        permitted_routes: List[str] = []
        prohibited_routes: List[str] = []
        if not s9.empty:
            permitted_routes = s9.iloc[0]["_permitted_routes"]
            prohibited_routes = s9.iloc[0]["_prohibited_routes"]
        if p == "out":
            return _out("green", "S9 out-of-competition permitted (within label).")
        # in の場合
        if route is None:
            opts = list(set((permitted_routes or []) + (prohibited_routes or [])))
            return _ask([{"field":"route","options": opts or ["inhaled","oral","injectable","rectal","oromucosal","nasal","topical","ophthalmic","otic","perianal","dental-intracanal"],"hint":"Select route"}], "yellow", "S9 route required.")
        rt = _canonical_route(route)
        if rt in prohibited_routes:
            return _out("red", "S9 prohibited route in-competition.")
        return _out("green", "S9 permitted route in-competition.")
    # 想定外
    return None


# ============================
# ブランド → 成分分解（キャッシュ）
# ============================
def _judge_by_brand_cache(term_norm: str, sport_code: str, period: Optional[str], route: Optional[str], dose_24h: Optional[float], depth: int) -> Optional[Dict[str, Any]]:
    dfp = DF["cache_prod"]
    if dfp.empty:
        return None
    # brand / aliases マッチ
    row = dfp[
        (dfp["_brand_norm"] == term_norm) |
        (dfp["_aliases_list"].apply(lambda lst: term_norm in lst if isinstance(lst, list) else False))
    ]
    if row.empty:
        return None

    inns = _split_semicol(row.iloc[0].get("list_name",""))
    if not inns:
        return None

    # 各成分に judge を再帰 → 集約
    reasons = []
    asks: List[Dict[str, Any]] = []
    for inn in inns:
        res = judge(inn, sport_code=sport_code, period=period, route=route, dose_24h=dose_24h, _depth=depth+1)
        if res.get("status") == "ask":
            asks += res["need"]
            reasons.append(f"{inn}: needs " + ", ".join(n["field"] for n in res["need"]))
        else:
            reasons.append(f"{inn}: {res['reason']}")
            if res["color"] == "red":
                return res  # 真っ赤が1つでもあれば即終了

    if asks:
        # 重複除去
        dedup, seen = [], set()
        for n in asks:
            f = n["field"]
            if f not in seen:
                dedup.append(n); seen.add(f)
        return _ask(dedup, "yellow", " / ".join(reasons))

    return _out("green", " / ".join(reasons) if reasons else "All components allowed")


# ============================
# 外部API（RxNorm/RxClass）フォールバック（任意）
# ============================
RXNAV = "https://rxnav.nlm.nih.gov/REST"

def _rxnorm_find_rxcui(name: str) -> Optional[str]:
    if requests is None:
        return None
    q = name.strip()
    try:
        r = requests.get(f"{RXNAV}/rxcui.json", params={"name": q, "search": 2}, timeout=10)
        ids = (r.json().get("idGroup", {}) or {}).get("rxnormId") or []
        if ids:
            return ids[0]
        # approximate
        r = requests.get(f"{RXNAV}/approximateTerm.json", params={"term": q, "maxEntries": 1}, timeout=10)
        cand = (r.json().get("approximateGroup", {}) or {}).get("candidate") or []
        return cand[0]["rxcui"] if cand else None
    except Exception:
        return None

def _rxnorm_related_in(rxcui: str) -> List[Dict[str, str]]:
    if requests is None:
        return []
    try:
        rel = requests.get(f"{RXNAV}/rxcui/{rxcui}/related.json", params={"tty":"IN"}, timeout=10).json()
        out = []
        for g in rel.get("relatedGroup", {}).get("conceptGroup", []) or []:
            for p in g.get("conceptProperties", []) or []:
                out.append({"inn": p["name"].lower(), "rxcui": p["rxcui"]})
        return out
    except Exception:
        return []

def _rxclass_labels_by_rxcui(rxcui: str) -> List[str]:
    """ RxClass から EPC / MoA の label を集める（小文字）"""
    if requests is None:
        return []
    labels: Set[str] = set()
    try:
        # EPC
        epc = requests.get(f"{RXNAV}/rxclass/class/byRxcui.json", params={"rxcui": rxcui}, timeout=10).json()
        for it in epc.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo", []) or []:
            lab = (it.get("rxclassMinConceptItem", {}) or {}).get("className", "")
            if lab:
                labels.add(lab.lower().strip())
    except Exception:
        pass
    # MoA など別エンドポイントの追加は運用で拡張可
    return list(labels)

def _map_labels_to_section(labels: List[str]) -> Optional[str]:
    if not labels or DF["classes_map"].empty:
        return None
    # label で直接突き合わせ（小文字）
    labs = set(lab.lower().strip() for lab in labels)
    rows = DF["classes_map"][DF["classes_map"]["_label_norm"].isin(labs)]
    if not rows.empty:
        # 複数当たったらS1>S9>P1…などの優先度付けも可能。今は最初のもの。
        return str(rows.iloc[0]["mapped_section_code"]).strip().upper()
    return None

def _labels_allowed(labels: List[str]) -> bool:
    if DF["allowed_class"].empty or not labels:
        return False
    labs = set(lab.lower().strip() for lab in labels)
    rows = DF["allowed_class"][DF["allowed_class"]["_label_norm"].isin(labs)]
    return not rows.empty

def _judge_by_external(term_norm: str, sport_code: str, period: Optional[str], route: Optional[str], dose_24h: Optional[float], depth: int) -> Optional[Dict[str, Any]]:
    # RxNorm → IN優先 → EPC/MoA でクラス推定 → classes_map で Sx
    rxcui = _rxnorm_find_rxcui(term_norm)
    if not rxcui:
        return _out("red", "Not found in external data (S0). Possibly unapproved.")

    # 成分を取得（IN）
    ins = _rxnorm_related_in(rxcui)
    if len(ins) >= 2:
        # 合剤 → 成分ごとに再帰
        reasons = []
        asks: List[Dict[str, Any]] = []
        for it in ins:
            res = judge(it["inn"], sport_code=sport_code, period=period, route=route, dose_24h=dose_24h, _depth=depth+1)
            if res.get("status") == "ask":
                asks += res["need"]
                reasons.append(f"{it['inn']}: needs " + ", ".join(n["field"] for n in res["need"]))
            else:
                reasons.append(f"{it['inn']}: {res['reason']}")
                if res["color"] == "red":
                    return res
        if asks:
            # 重複除去
            dedup, seen = [], set()
            for n in asks:
                f = n["field"]
                if f not in seen:
                    dedup.append(n); seen.add(f)
            return _ask(dedup, "yellow", " / ".join(reasons))
        return _out("green", " / ".join(reasons) if reasons else "All components allowed")

    # 単剤（または1 IN）として扱う
    rxcui_in = ins[0]["rxcui"] if ins else rxcui

    labels = _rxclass_labels_by_rxcui(rxcui_in)
    sec = _map_labels_to_section(labels) if labels else None
    if sec:
        # セクションに基づいて既定フロー
        return _judge_by_cachelike_section(sec, period, route, sport_code)
    # セクション不明でも allowed クラスなら緑に逃がす
    if _labels_allowed(labels):
        return _out("green", "Allowed pharmacologic class.")
    # 依然不明 → 安全側
    return _out("red", "Unknown/Unmapped class (S0).")


def _judge_by_cachelike_section(sec: str, period: Optional[str], route: Optional[str], sport_code: str) -> Dict[str, Any]:
    sec = sec.strip().upper()
    if sec == "ALLOWED":
        return _out("green", "Allowed class.")
    if sec == "S0":
        return _out("red", "Unknown/Unapproved (S0).")
    if sec == "P1":
        # スポーツ規則
        dfs = DF["sports"]
        period_rule = "in"
        if not dfs.empty and "sport_code" in dfs.columns:
            rs = dfs[dfs["sport_code"].astype(str).str.strip().str.upper() == sport_code.upper()]
            if not rs.empty:
                period_rule = str(rs.iloc[0].get("prohibited_period", "in")).strip().lower()
        p = _ensure_period(period)
        if period_rule == "both":
            return _out("red", "P1 prohibited in this sport (both).")
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", "Period required for P1.")
        return _out("red" if p=="in" else "green", "P1 period rule.")
    if sec in ("S1","S2","S3","S4","S5"):
        return _out("red", f"{sec} prohibited.")
    if sec in ("S6","S7","S8"):
        p = _ensure_period(period)
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", f"{sec} period rule.")
        return _out("red" if p=="in" else "green", f"{sec} period rule.")
    if sec == "S9":
        p = _ensure_period(period)
        if not p:
            return _ask([{"field":"period","options":["in","out"],"hint":"In-competition?"}], "yellow", "S9 period required.")
        s9 = DF["sections"][DF["sections"]["section_code"].astype(str).str.upper()=="S9"]
        permitted_routes: List[str] = []
        prohibited_routes: List[str] = []
        if not s9.empty:
            permitted_routes = s9.iloc[0]["_permitted_routes"]
            prohibited_routes = s9.iloc[0]["_prohibited_routes"]
        if p == "out":
            return _out("green", "S9 out-of-competition permitted (within label).")
        if route is None:
            opts = list(set((permitted_routes or []) + (prohibited_routes or [])))
            return _ask([{"field":"route","options": opts or ["inhaled","oral","injectable","rectal","oromucosal","nasal","topical","ophthalmic","otic","perianal","dental-intracanal"],"hint":"Select route"}], "yellow", "S9 route required.")
        rt = _canonical_route(route)
        if rt in prohibited_routes:
            return _out("red", "S9 prohibited route in-competition.")
        return _out("green", "S9 permitted route in-competition.")
    # fallback
    return _out("red", "Unknown section (S0).")


# ============================
# 公開API
# ============================
def judge(term: str, sport_code: str, period: Optional[str] = None, route: Optional[str] = None, dose_24h: Optional[float] = None, _depth: int = 0) -> Dict[str, Any]:
    """
    メイン判定関数（stateless）。
    - term: ユーザー入力（物質名またはブランド名）
    - sport_code: 競技コード（先にUIで必須入力）
    - period: "in" / "out" or None（未指定なら ask）
    - route: 投与経路（英語・日本語どちらでも可、ある程度正規化）
    - dose_24h: 24時間総量（必要な場合のみ）
    """
    if _depth > 5:
        return _out("red", "Recursion limit reached.")

    t = _norm(term)
    p = _ensure_period(period)
    rt = _canonical_route(route)

    # 0) 6つのCSV（INN直指定）
    res = _judge_by_6csv(t, sport_code, p, rt, dose_24h)
    if res is not None:
        return res

    # 1) substances_cache.csv
    res = _judge_by_cache(t, sport_code, p, rt, dose_24h)
    if res is not None:
        return res

    # 2) product_compositions.csv（ブランド→成分）
    res = _judge_by_brand_cache(t, sport_code, p, rt, dose_24h, _depth)
    if res is not None:
        return res

    # 3) 外部API（RxNorm/RxClass）フォールバック（requests が無ければ S0）
    res = _judge_by_external(t, sport_code, p, rt, dose_24h, _depth) if requests else None
    if res is not None:
        return res

    # 4) それでも不明 → S0
    return _out("red", "Not found (S0). Possibly unapproved.")


if __name__ == "__main__":
    # 簡易テスト
    print(judge("salbutamol", sport_code="GEN", period=None))

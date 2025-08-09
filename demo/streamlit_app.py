# app.py

import os, sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import streamlit as st
from scripts.judge_legacy import judge  # さっき作った judge() を import

st.title("Anti-Doping Judge (Step-by-step)")

# 1) 初期入力
sport = st.selectbox("Sport", ["GEN","ISSF_SHOT","WA_ARCH","IGF_GOLF"])
term  = st.text_input("Drug/Brand (e.g., ACEBUTOLOL)")

# 状態を持つ（回答を貯める）
if "ctx" not in st.session_state:
    st.session_state.ctx = {"sport_code": None, "term": None, "period": None, "route": None, "dose_24h": None}

def reset():
    st.session_state.ctx = {"sport_code": None, "term": None, "period": None, "route": None, "dose_24h": None}

if st.button("Start / Retry"):
    st.session_state.ctx.update({"sport_code": sport, "term": term})

ctx = st.session_state.ctx
if ctx["sport_code"] and ctx["term"]:
    # 2) judge を呼ぶ（足りない項目は None のまま）
    res = judge(
        term=ctx["term"],
        sport_code=ctx["sport_code"],
        period=ctx["period"],
        route=ctx["route"],
        dose_24h=ctx["dose_24h"],
    )

    if res["status"] == "final":
        st.success(f"{res['color'].upper()} : {res['reason']}")
        if st.button("Reset"):
            reset()
    else:
        st.info(f"Need: {', '.join([f['field'] for f in res['need']])}  /  Hint: {res.get('reason','')}")
        # 3) 追加質問を動的に表示
        for need in res["need"]:
            field = need["field"]
            if field == "period":
                val = st.radio("In-competition?", ["in","out"], horizontal=True)
                if st.button("Next (period)"):
                    ctx["period"] = val
                    st.rerun()
            elif field == "route":
                opts = need.get("options") or ["oral","injectable","inhaled","topical","nasal","ophthalmic","otic","rectal","oromucosal"]
                val = st.selectbox("Select route", opts)
                if st.button("Next (route)"):
                    ctx["route"] = val
                    st.rerun()
            elif field == "dose_24h":
                val = st.number_input("24h total dose", min_value=0.0, step=1.0)
                if st.button("Next (dose)"):
                    ctx["dose_24h"] = val
                    st.rerun()

        st.button("Reset", on_click=reset)

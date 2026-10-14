"""인격 / E.G.O 뷰어.

실행: .venv/Scripts/streamlit run app/viewer.py
"""
import json
import re
import sqlite3
from pathlib import Path

import streamlit as st

DB = Path(__file__).resolve().parent.parent / "data" / "gesellschaft.db"
SIN_COLOR = {"분노": "#e74c3c", "색욕": "#e67e22", "나태": "#f1c40f", "탐식": "#2ecc71",
             "우울": "#3498db", "오만": "#2c3e8f", "질투": "#8e44ad"}
SLOT_LABEL = {"S1": "스킬 1", "S2": "스킬 2", "S3": "스킬 3", "DEF": "수비", "EGO": "각성", "EGO_COR": "침식"}

st.set_page_config(page_title="gesellschaft", layout="wide")


@st.cache_resource
def conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def q(sql, *args):
    return [dict(r) for r in conn().execute(sql, args).fetchall()]


@st.cache_data
def keyword_dict():
    return {r["name"]: r["description"] for r in q("SELECT name, description FROM keyword WHERE description IS NOT NULL")}


def sin_badge(sin):
    color = SIN_COLOR.get(sin, "#666")
    return f"<span style='background:{color};color:white;padding:1px 6px;border-radius:4px;font-size:0.8em'>{sin}</span>"


def tags(owner_type, owner_id, kind):
    return [r["value"] for r in q("SELECT value FROM tag WHERE owner_type=? AND owner_id=? AND kind=?", owner_type, owner_id, kind)]


def render_effect(text):
    if not text:
        return
    # [트리거] 태그 강조
    html = re.sub(r"\[([^\]]{1,20})\]", r"<b style='color:#c9a227'>[\1]</b>", text).replace("\n", "<br>")
    st.markdown(f"<div style='font-size:0.9em;line-height:1.5'>{html}</div>", unsafe_allow_html=True)


def render_skills(owner_type, owner_id, uptie):
    skills = q("SELECT * FROM skill WHERE owner_type=? AND owner_id=? AND uptie=? ORDER BY "
               "CASE slot WHEN 'S1' THEN 1 WHEN 'S2' THEN 2 WHEN 'S3' THEN 3 WHEN 'DEF' THEN 4 WHEN 'EGO' THEN 5 ELSE 6 END, variant",
               owner_type, owner_id, uptie)
    for s in skills:
        coins = q("SELECT idx, effect FROM coin WHERE skill_id=? ORDER BY idx", s["id"])
        max_power = (s["base"] or 0) + (s["coin_power"] or 0) * (s["coin_num"] or 0)
        variant = "" if s["variant"] == 0 else " · 변형"
        qty = f" ×{s['quantity']}" if s["quantity"] else ""
        with st.container(border=True):
            st.markdown(
                f"**{SLOT_LABEL.get(s['slot'], s['slot'])}{variant}{qty} — {s['name']}** &nbsp; {sin_badge(s['sin'])} "
                f"&nbsp; `{s['atk_type']}` &nbsp; 위력 **{s['base']}** + {s['coin_power']} × {s['coin_num']}코인 "
                f"(최대 {max_power}) &nbsp; 가중치 {s['weight']} &nbsp; 레벨 {s['level_text']}"
                + (f" &nbsp; 정신력 소모 {s['sanity_cost']}" if s["sanity_cost"] else ""),
                unsafe_allow_html=True)
            render_effect(s["effect"])
            for c in coins:
                st.markdown(f"<div style='margin-top:4px;font-size:0.85em;color:#999'>코인 {c['idx']}</div>",
                            unsafe_allow_html=True)
                render_effect(c["effect"])
            if s["playable_desc"]:
                st.caption(s["playable_desc"])


def render_passives(owner_type, owner_id, uptie):
    sql = "SELECT * FROM passive WHERE owner_type=? AND owner_id=?"
    args = [owner_type, owner_id]
    if owner_type == "identity":
        sql += " AND uptie=?"
        args.append(uptie)
    for p in q(sql + " ORDER BY kind, id", *args):
        sins = json.loads(p["cond_sins"] or "[]")
        cond = ""
        if sins:
            cond = " ".join(sin_badge(x) for x in sins) + f" {p['cond_type'] or ''} {p['cond_count'] or ''}"
        label = {"main": "패시브", "support": "서포트 패시브", "ego": "E.G.O 패시브"}[p["kind"]]
        with st.container(border=True):
            st.markdown(f"**{label} — {p['name']}** &nbsp; {cond}", unsafe_allow_html=True)
            render_effect(p["effect"])


def render_keywords(owner_type, owner_id):
    kd = keyword_dict()
    for k in tags(owner_type, owner_id, "keyword"):
        with st.expander(k):
            st.write(kd.get(k, "(사전에 정의 없음)"))


def identity_page():
    sinners = [r["sinner"] for r in q("SELECT DISTINCT sinner FROM identity ORDER BY sinner")]
    keywords = [r["value"] for r in q("SELECT value FROM tag WHERE kind='keyword' AND owner_type='identity' "
                                      "GROUP BY value ORDER BY COUNT(*) DESC")]
    affs = [r["value"] for r in q("SELECT DISTINCT value FROM tag WHERE kind='affiliation' ORDER BY value")]

    with st.sidebar:
        f_sinner = st.multiselect("수감자", sinners)
        f_kw = st.multiselect("키워드 (모두 포함)", keywords)
        f_aff = st.multiselect("소속", affs)
        f_sin = st.multiselect("스킬 죄악 속성", list(SIN_COLOR))
        f_type = st.multiselect("공격 유형", ["참격", "관통", "타격"])
        f_grade = st.multiselect("등급", [3, 2, 1])
        f_text = st.text_input("효과 전문 검색", placeholder='예: 진동 폭발')

    sql = "SELECT * FROM identity i WHERE 1=1"
    args = []
    if f_sinner:
        sql += f" AND sinner IN ({','.join('?' * len(f_sinner))})"; args += f_sinner
    if f_grade:
        sql += f" AND grade IN ({','.join('?' * len(f_grade))})"; args += f_grade
    for k in f_kw:
        sql += " AND EXISTS (SELECT 1 FROM tag t WHERE t.owner_type='identity' AND t.owner_id=i.id AND t.kind='keyword' AND t.value=?)"; args.append(k)
    for a in f_aff:
        sql += " AND EXISTS (SELECT 1 FROM tag t WHERE t.owner_type='identity' AND t.owner_id=i.id AND t.kind='affiliation' AND t.value=?)"; args.append(a)
    if f_sin:
        sql += f" AND EXISTS (SELECT 1 FROM skill s WHERE s.owner_type='identity' AND s.owner_id=i.id AND s.sin IN ({','.join('?' * len(f_sin))}))"; args += f_sin
    if f_type:
        sql += f" AND EXISTS (SELECT 1 FROM skill s WHERE s.owner_type='identity' AND s.owner_id=i.id AND s.atk_type IN ({','.join('?' * len(f_type))}))"; args += f_type
    if f_text:
        sql += " AND i.id IN (SELECT owner_id FROM effect_fts WHERE owner_type='identity' AND effect_fts MATCH ?)"
        args.append('"' + f_text.replace('"', '') + '"')
    rows = q(sql + " ORDER BY id DESC", *args)

    left, right = st.columns([1, 2.4])
    with left:
        st.caption(f"{len(rows)}개")
        options = {f"[{r['sinner']}] {r['name']}  ({'★' * r['grade']}, S{r['season']})": r["id"] for r in rows}
        if not options:
            return
        picked = st.radio("인격", list(options), label_visibility="collapsed")
    d = q("SELECT * FROM identity WHERE id=?", options[picked])[0]
    with right:
        c1, c2 = st.columns([1, 2])
        with c1:
            if d["image"]:
                st.image(d["image"])
        with c2:
            st.subheader(f"[{d['sinner']}] {d['name']}")
            st.caption(f"{d['name_en']} · {'★' * d['grade']} · 시즌 {d['season']} · 출시 {d['release_date']}")
            st.markdown(f"**HP** {d['hp']} · **속도** {d['speed_min']}-{d['speed_max']} · **방어 레벨** {d['def_level']}")
            st.markdown(f"**내성** 참격 {d['res_slash']} · 관통 {d['res_pierce']} · 타격 {d['res_blunt']}")
            ext = q("SELECT stagger_json FROM identity_ext WHERE identity_id=?", d["id"])
            if ext:
                lines = json.loads(ext[0]["stagger_json"])
                st.markdown("**흐트러짐 선** " + " · ".join(
                    f"{p}% ({round(d['hp'] * p / 100)})" for p in lines) if d["hp"] else "")
            st.markdown("**소속** " + ", ".join(tags("identity", d["id"], "affiliation")))
            st.caption(d["obtaining"] or "")
        uptie = st.segmented_control("동기화", [3, 4], default=4, key=f"up_{d['id']}") or 4
        t1, t2, t3 = st.tabs(["스킬", "패시브", "키워드"])
        with t1:
            render_skills("identity", d["id"], uptie)
        with t2:
            render_passives("identity", d["id"], uptie)
        with t3:
            render_keywords("identity", d["id"])


def ego_page():
    sinners = [r["sinner"] for r in q("SELECT DISTINCT sinner FROM ego ORDER BY sinner")]
    with st.sidebar:
        f_sinner = st.multiselect("수감자", sinners)
        f_grade = st.multiselect("등급", ["ZAYIN", "TETH", "HE", "WAW", "ALEPH"])
    sql, args = "SELECT * FROM ego WHERE 1=1", []
    if f_sinner:
        sql += f" AND sinner IN ({','.join('?' * len(f_sinner))})"; args += f_sinner
    if f_grade:
        sql += f" AND grade IN ({','.join('?' * len(f_grade))})"; args += f_grade
    rows = q(sql + " ORDER BY id DESC", *args)
    left, right = st.columns([1, 2.4])
    with left:
        st.caption(f"{len(rows)}개")
        options = {f"[{r['sinner']}] {r['name']}  ({r['grade']})": r["id"] for r in rows}
        if not options:
            return
        picked = st.radio("E.G.O", list(options), label_visibility="collapsed")
    d = q("SELECT * FROM ego WHERE id=?", options[picked])[0]
    with right:
        c1, c2 = st.columns([1, 2])
        with c1:
            if d["image"]:
                st.image(d["image"])
        with c2:
            st.subheader(f"[{d['sinner']}] {d['name']}")
            st.caption(f"{d['name_en']} · {d['grade']} · 시즌 {d['season']} · 출시 {d['release_date']}")
            st.markdown(f"**내성** {', '.join(json.loads(d['resistance_json'] or '[]'))} · **비용** {d['cost_json']}")
            st.caption(d["obtaining"] or "")
        uptie = st.segmented_control("동기화", [3, 4], default=4, key=f"eup_{d['id']}") or 4
        t1, t2, t3 = st.tabs(["스킬", "패시브", "키워드"])
        with t1:
            render_skills("ego", d["id"], uptie)
        with t2:
            render_passives("ego", d["id"], uptie)
        with t3:
            render_keywords("ego", d["id"])


page = st.sidebar.radio("보기", ["인격", "E.G.O"], horizontal=True)
identity_page() if page == "인격" else ego_page()

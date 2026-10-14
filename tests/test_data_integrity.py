"""정규화된 DB의 기본 불변식. 실패하면 원본 데이터 특이 케이스이거나 build_db 보정이 필요한 지점."""
import json
import sqlite3
from pathlib import Path

import pytest

DB = Path(__file__).resolve().parent.parent / "data" / "gesellschaft.db"
SINS = {"분노", "색욕", "나태", "탐식", "우울", "오만", "질투"}


@pytest.fixture(scope="module")
def db():
    if not DB.exists():
        pytest.skip("run scripts/build_db.py first")
    c = sqlite3.connect(DB)
    yield c
    c.close()


def test_every_identity_has_deck_skills(db):
    bad = db.execute("""
        SELECT i.id, i.name, group_concat(DISTINCT s.slot) FROM identity i
        LEFT JOIN skill s ON s.owner_type='identity' AND s.owner_id=i.id AND s.is_deck_skill=1
        GROUP BY i.id HAVING COUNT(DISTINCT s.slot) < 3""").fetchall()
    assert not bad, bad


def test_every_identity_has_defense_skill(db):
    bad = db.execute("""SELECT id, name FROM identity i WHERE NOT EXISTS
        (SELECT 1 FROM skill s WHERE s.owner_type='identity' AND s.owner_id=i.id AND s.slot='DEF')""").fetchall()
    assert not bad, bad


def test_skill_sins_are_known(db):
    bad = db.execute("SELECT DISTINCT sin FROM skill WHERE sin IS NOT NULL").fetchall()
    assert {r[0] for r in bad} <= SINS, bad


def test_passive_conditions_are_parseable(db):
    bad = []
    for pid, owner, name, sins, cnt, ctype in db.execute(
            "SELECT id, owner_id, name, cond_sins, cond_count, cond_type FROM passive WHERE owner_type='identity'"):
        sins = json.loads(sins)
        if not set(sins) <= SINS:
            bad.append((owner, name, sins))
        if ctype in ("보유", "공명") and not (sins and cnt):
            bad.append((owner, name, sins, cnt, ctype))
    assert not bad, bad


def test_identity_resistance_values(db):
    vals = {r[0] for r in db.execute(
        "SELECT res_slash FROM identity UNION SELECT res_pierce FROM identity UNION SELECT res_blunt FROM identity")}
    assert vals <= {"취약", "보통", "견딤", "내성"}, vals


def test_identity_resistance_follows_wiki(db):
    # baslimbus 내성 오류 11건 → wiki.gg 값으로 덮어씀
    ko = {"Fatal": "취약", "Normal": "보통", "Endure": "견딤", "Ineff": "내성"}
    bad = [(i, s, p, b, r) for i, s, p, b, r in db.execute(
        "SELECT i.id, res_slash, res_pierce, res_blunt, e.res_en_json FROM identity i JOIN identity_ext e ON e.identity_id=i.id")
        if [s, p, b] != [ko[x] for x in json.loads(r)]]
    assert not bad, bad


def test_ego_has_awakening_skill(db):
    bad = db.execute("""SELECT id, name FROM ego e WHERE NOT EXISTS
        (SELECT 1 FROM skill s WHERE s.owner_type='ego' AND s.owner_id=e.id AND s.slot='EGO')""").fetchall()
    assert not bad, bad


def test_only_defense_skills_lack_sin_and_only_below_uptie4(db):
    bad = db.execute("""SELECT owner_type, owner_id, slot, uptie, name FROM skill
        WHERE sin IS NULL AND NOT (slot = 'DEF' AND uptie < 4)""").fetchall()
    assert not bad, bad


def test_every_identity_matched_to_wiki_with_stagger(db):
    bad = db.execute("""SELECT i.id, i.name_en FROM identity i LEFT JOIN identity_ext e ON e.identity_id = i.id
        WHERE e.identity_id IS NULL OR e.stagger_json = '[]'""").fetchall()
    assert not bad, bad


def test_def_mod_agrees_between_sources(db):
    # baslimbus def_level "65(+4)" 의 괄호 값 == wiki.gg defmod
    bad = []
    for iid, lvl, mod in db.execute("SELECT i.id, i.def_level, e.def_mod FROM identity i JOIN identity_ext e ON e.identity_id=i.id"):
        if lvl and "(" in lvl and mod is not None and int(lvl.split("(")[1].rstrip(")").replace("+", "")) != mod:
            bad.append((iid, lvl, mod))
    assert len(bad) <= 5, bad

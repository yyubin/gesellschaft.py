"""확장 어휘의 실행 의미 — 작은 인라인 spec 으로 검증 (DB 불필요)."""
import pytest

import lpdl
from engine.runtime import Battle, Runtime, Status, Unit, run_action
from engine.runtime.state import SkillData

HEADS = lambda p: True


def runtime(src: str) -> Runtime:
    doc = lpdl.parse("lpdl 2\n" + src)
    rep = lpdl.validate(doc)
    assert rep.ok, rep.errors
    return Runtime(doc)


def unit(name, ident=None, side="ally", formation=0, **kw) -> Unit:
    skills = {("S1", 0): SkillData("S1", 0, "s1", "나태", "타격", base=4, coin_power=0, coins=1, level=50)}
    return Unit(name=name, identity_id=ident, side=side, formation=formation, skills=skills, **kw)


def test_pick_prefer_affiliation_and_hp_ratio():
    """'자신을 제외한 현재 체력 비율이 가장 낮은 르누아르 아군 (충전 인격 우선) 1명'."""
    rt = runtime('identity 1 { skill S1 { on use { '
                 'let a = pick(allies, where: it != self and has_affiliation(it, "르누아르"), '
                 'prefer: keyword_identity(it, 충전), by: it.hp_ratio, n: 1) \n gain a.shield 10 } } }')
    me = unit("me", 1, affiliations={"르누아르"})
    low = unit("low", affiliations={"르누아르"}, max_hp=100, hp=10)
    charge = unit("charge", affiliations={"르누아르"}, keyword_tags={"충전"}, max_hp=100, hp=90)
    other = unit("other", max_hp=100, hp=1)                     # 르누아르 아님
    foe = unit("foe", side="enemy")
    b = Battle([me, low, charge, other, foe])
    run_action(rt, b, me, ("S1", 0), foe, HEADS)
    assert (charge.shield, low.shield, other.shield) == (10, 0, 0)


def test_aura_modify_to_allies():
    """always { modify def_level 5 to allies } → 같은 편 다른 유닛이 맞을 때 방어 레벨 +5 (자신 포함)."""
    rt = runtime('identity 2 { passive "오라" { always { modify taken_damage_pct -50 to allies } } }')
    holder, ally, foe = unit("holder", 2), unit("ally"), unit("foe", side="enemy")
    b = Battle([holder, ally, foe])
    r = run_action(rt, b, foe, ("S1", 0), ally, HEADS)          # 적이 아군을 공격
    assert r.damage == pytest.approx(4 * 0.5)
    r2 = run_action(rt, b, ally, ("S1", 0), foe, HEADS)        # 아군이 적을 공격 → 오라 대상 아님
    assert r2.damage == pytest.approx(4)


def test_ally_hit_taken_event():
    rt = runtime('identity 3 { passive "p" { on ally_hit_taken when target.shield > 0 { gain self.sanity 5 } } }')
    watcher, ally, foe = unit("watcher", 3), unit("ally", shield=100), unit("foe", side="enemy")
    b = Battle([watcher, ally, foe])
    run_action(rt, b, foe, ("S1", 0), ally, HEADS)
    assert watcher.sanity == 5
    run_action(rt, b, foe, ("S1", 0), watcher, HEADS)          # 자신이 맞는 건 아군 피격 아님
    assert watcher.sanity == 5


def test_reuse_coin_once_per_use():
    rt = runtime('identity 4 { skill S1 { coin 1 { on hit limit 1/use { reuse_coin } } } }')
    me, foe = unit("me", 4), unit("foe", side="enemy")
    r = run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert r.damage == pytest.approx(8)                           # 코인 1 이 두 번 적중


def test_sin_damage_uses_sin_resistance():
    rt = runtime('identity 5 { skill S1 { coin 1 { on hit { deal target 나태 10 } } } }')
    me, foe = unit("me", 5), unit("foe", side="enemy", sin_res={"나태": "취약"})
    run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert foe.damage_taken == pytest.approx(4 * 2 + 10 * 2)      # 스킬도 나태 속성이라 취약 ×2


def test_overflow_and_retype():
    rt = runtime('keyword "보존" { prop kind special_charge prop count_max 20 }\n'
                 'identity 6 { skill S1 { on use { gain self.보존.count 8 } '
                 'coin 1 { on hit { retype target 진동 -> 파열 } } } '
                 'passive "p" { on gained(self.보존.count) { gain self.shield ev.overflow } } }')
    me, foe = unit("me", 6), unit("foe", side="enemy")
    me.statuses["보존"] = Status(0, 15)
    foe.statuses["진동"] = Status(3, 2)
    run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert me.statuses["보존"].count == 20 and me.shield == 3
    assert "진동" not in foe.statuses and (foe.statuses["파열"].potency, foe.statuses["파열"].count) == (3, 2)


def test_target_is_ally_and_max_resonance():
    rt = runtime('identity 7 { skill S1 { on use when target is ally { gain target.shield 1 } '
                 'on use { modify final_power per(team.max_resonance, 1) cap 4 } } }')
    me, friend, foe = unit("me", 7), unit("friend"), unit("foe", side="enemy")
    b = Battle([me, friend, foe], max_resonance=3)
    run_action(rt, b, me, ("S1", 0), friend, HEADS)
    assert friend.shield == 0 or friend.shield == 1               # 보호막 1 획득 후 피격으로 소진될 수 있음
    r = run_action(rt, b, me, ("S1", 0), foe, HEADS)
    assert r.damage == pytest.approx(4 + 3) and foe.shield == 0

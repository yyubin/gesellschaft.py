"""표준 상태 키워드 (lpdlc-v2 keywords/standard.lpdl) 의 실행 의미 — 원문 문장별 시나리오 (DB 불필요)."""
import pytest

import lpdl
from engine.runtime import Battle, Runtime, Status, Unit, run_action, turn_end
from engine.runtime.loader import SPEC_DIRS, dummy
from engine.runtime.state import SkillData

HEADS = lambda p: True
STANDARD = SPEC_DIRS[0]


@pytest.fixture(scope="module")
def rt() -> Runtime:
    doc = lpdl.load([STANDARD])
    rep = lpdl.validate(doc)
    assert rep.ok, rep.errors
    return Runtime(doc)


def unit(name, side="ally", coins=1, **kw) -> Unit:
    skills = {("S1", 0): SkillData("S1", 0, "s1", "우울", "타격", base=4, coin_power=0, coins=coins, level=50)}
    return Unit(name=name, side=side, skills=skills, **kw)


def test_bleed_damages_attacker_per_coin_flip_and_not_counted_as_attack_damage(rt):
    """출혈: 공격 스킬의 코인 판정 시 위력만큼 고정 피해, 판정 후 횟수 -1."""
    me = unit("me", coins=3, statuses={"출혈": Status(potency=3, count=2)})
    foe = unit("foe", side="enemy")
    r = run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert me.hp == 100 - 3 * 2                 # 횟수 2 → 코인 2개까지만, 3번째 코인 판정 전에 사라짐
    assert "출혈" not in me.statuses
    assert r.damage == pytest.approx(4 * 3)     # 자기 출혈 피해는 행동 피해에 들어가지 않음


@pytest.mark.parametrize("atk_type", ["반격", "회피", "방어", "참격"])
def test_bleed_does_not_trigger_on_defense_skills(rt, atk_type):
    """'공격 스킬의 코인 판정 시' — 반격·강화반격 등 DEF 슬롯 스킬은 공격 타입이어도 방어 스킬."""
    me = unit("me", statuses={"출혈": Status(potency=3, count=2)})
    me.skills = {("DEF", 0): SkillData("DEF", 0, "d", None, atk_type, base=4, coin_power=0, coins=2, level=50)}
    foe = unit("foe", side="enemy")
    run_action(rt, Battle([me, foe]), me, ("DEF", 0), foe, HEADS)
    assert me.hp == 100 and me.statuses["출혈"].count == 2


def test_bleed_does_not_trigger_when_holder_is_hit(rt):
    me, foe = unit("me"), unit("foe", side="enemy", statuses={"출혈": Status(potency=3, count=2)})
    run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert foe.statuses["출혈"].count == 2


def test_burn_at_turn_end(rt):
    """화상: 턴 종료 시 위력만큼 고정 피해, 횟수 -1."""
    u = unit("u", statuses={"화상": Status(potency=5, count=2)})
    b = Battle([u])
    turn_end(rt, b)
    assert (u.hp, u.statuses["화상"].count, u.statuses["화상"].potency) == (95, 1, 5)
    turn_end(rt, b)
    assert u.hp == 90 and "화상" not in u.statuses


def test_tremor_count_decays_at_turn_end_potency_kept(rt):
    u = unit("u", statuses={"진동": Status(potency=6, count=2)})
    turn_end(rt, Battle([u]))
    assert (u.statuses["진동"].potency, u.statuses["진동"].count) == (6, 1)


def test_rupture_on_each_hit_counted_as_attack_damage(rt):
    """파열: 공격 스킬로 피격 시 위력만큼 고정 피해, 피격 후 횟수 -1. 공격한 행동의 피해로 집계."""
    me, foe = unit("me", coins=3), unit("foe", side="enemy", statuses={"파열": Status(potency=4, count=2)})
    r = run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert r.damage == pytest.approx(4 * 3 + 4 * 2)
    assert "파열" not in foe.statuses


def test_sinking_sanity_damage(rt):
    """침잠: 피격 시 위력만큼 정신력 피해, 횟수 -1."""
    me, foe = unit("me"), unit("foe", side="enemy", sanity=10, statuses={"침잠": Status(potency=3, count=2)})
    r = run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert foe.sanity == 7 and foe.statuses["침잠"].count == 1
    assert r.damage == pytest.approx(4)


def test_sinking_on_sanityless_target_becomes_gloom_damage(rt):
    """정신력이 없는 대상에게는 우울 속성 피해 (우울 내성 적용)."""
    foe = dummy()
    foe.sin_res = {"우울": "취약"}
    foe.statuses["침잠"] = Status(potency=3, count=1)
    me = unit("me")
    r = run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert foe.sanity == 0
    assert r.damage == pytest.approx(4 * 2 + 3 * 2)     # 스킬도 우울 속성이라 취약 ×2


def test_plain_sanity_damage_on_sanityless_target_is_not_converted():
    """우울 전환은 침잠 고유 효과. 일반 정신력 피해는 정신력 없는 대상에게 효과 없음."""
    rt1 = Runtime(lpdl.parse("lpdl 2\nidentity 1 { skill S1 { on use { deal target sanity 5 } } }"))
    foe, me = dummy(), unit("me", identity_id=1)
    foe.sin_res = {"우울": "취약"}
    r = run_action(rt1, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert foe.sanity == 0 and r.damage == pytest.approx(4 * 2)


def test_poise_count_decays_at_turn_end(rt):
    u = unit("u", statuses={"호흡": Status(potency=10, count=1)})
    turn_end(rt, Battle([u]))
    assert "호흡" not in u.statuses


def test_charge_cap_20_and_stays_at_zero(rt):
    """충전: 최대 20, 턴 종료 시 1 감소. charge 계열은 횟수 0 이어도 상태가 남음."""
    u = unit("u", statuses={"충전": Status(potency=0, count=19)})
    b = Battle([u])
    rt.change(b, u, ("status", "충전", "count"), 5, source=u)
    assert u.statuses["충전"].count == 20
    u.statuses["충전"].count = 1
    turn_end(rt, b)
    assert "충전" in u.statuses and u.statuses["충전"].count == 0

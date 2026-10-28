"""합 (docs/rules 3장) + 치명타 — 손계산 시나리오 (DB 불필요)."""
import pytest

import lpdl
from engine.runtime import (Battle, Runtime, Status, Unit, expect, run_action, run_clash, simulate_action,
                            simulate_clash)
from engine.runtime.loader import SPEC_DIRS
from engine.runtime.state import SkillData

HEADS = lambda p: True


def runtime(src: str = "") -> Runtime:
    doc = lpdl.merge([lpdl.load([SPEC_DIRS[0]]), lpdl.parse("lpdl 2\n" + src)])
    rep = lpdl.validate(doc)
    assert rep.ok, rep.errors
    return Runtime(doc)


def unit(name, base, cp, coins, side="ally", ident=None, **kw) -> Unit:
    skills = {("S1", 0): SkillData("S1", 0, "s1", None, "타격", base=base, coin_power=cp, coins=coins, level=50)}
    return Unit(name=name, identity_id=ident, side=side, skills=skills, **kw)


def scripted(*values):
    it = iter(values)
    return lambda p: next(it)


def test_stronger_side_wins_all_coins_remain_and_clash_count_bonus():
    """A 10 고정 2코인 vs B 5 고정 2코인 → 2합 승리, 남은 코인 2개로 공격. 피해 × (1 + 합 횟수 0.01), 정신력 +12."""
    rt = runtime()
    a, b = unit("A", 10, 0, 2), unit("B", 5, 0, 2, side="enemy")
    o = run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert (o.winner, o.rounds, o.remaining) == ("A", 2, [1, 2])
    assert o.result.damage == pytest.approx(10 * 1.02 * 2)
    assert a.sanity == pytest.approx(10 * (1 + 0.2 * 1))
    assert b.sanity == 0


def test_win_probability_from_power_distribution():
    """A 4+2 1코인 (4 또는 6) vs B 5 고정 1코인 → A 승리 50%."""
    rt = runtime()
    a, b = unit("A", 4, 2, 1), unit("B", 5, 0, 1, side="enemy")
    outs = simulate_clash(rt, Battle([a, b]), "A", ("S1", 0), "B", ("S1", 0))
    assert sum(o.prob for o in outs) == pytest.approx(1)
    assert expect(outs, lambda o: o.result.winner == "A") == pytest.approx(0.5)


def test_ties_repeat_and_count_as_clashes():
    """같은 스킬 5+1 1코인끼리: 라운드마다 승 .25 / 패 .25 / 동점 .5 → 승률 .5, 합 횟수 기대값 2."""
    rt = runtime()
    a, b = unit("A", 5, 1, 1), unit("B", 5, 1, 1, side="enemy")
    outs = simulate_clash(rt, Battle([a, b]), "A", ("S1", 0), "B", ("S1", 0), min_prob=1e-12)
    assert sum(o.prob for o in outs) == pytest.approx(1, abs=1e-9)
    assert expect(outs, lambda o: o.result.winner == "A") == pytest.approx(0.5, abs=1e-9)
    assert expect(outs, lambda o: o.result.rounds) == pytest.approx(2, abs=1e-6)


def test_fixed_equal_powers_never_resolve():
    rt = runtime()
    a, b = unit("A", 5, 0, 1), unit("B", 5, 0, 1, side="enemy")
    o = run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert o.winner is None and o.result is None


def test_destroyed_coin_effects_do_not_fire_and_power_uses_remaining_coins():
    """A 5+2 2코인 vs B 6 고정 2코인. 1합 패배(A 코인1 파괴) → 2·3합 승리 → 코인 2 만으로 공격.
    코인 1 의 [적중시] 효과는 발동하지 않는다. 코인 2 위력 = 5 + 2 (남은 코인 중 앞면), 합 3회."""
    rt = runtime('identity 1 { skill S1 { coin 1 { on hit { gain self.shield 100 } } '
                 'coin 2 { on hit { gain self.shield 1 } } } }')
    a, b = unit("A", 5, 2, 2, ident=1), unit("B", 6, 0, 2, side="enemy")
    # 라운드 선택: 1합 toss(승 .75)=False → 패, 2합 toss(.5)=True → 승, 3합 True → 승, 공격 코인 2 앞면
    o = run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), scripted(False, True, True, True))
    assert (o.winner, o.rounds, o.remaining) == ("A", 3, [2])
    assert [r[0] for r in o.log] == ["B", "A", "A"]
    assert a.shield == 1
    assert o.result.coin_power == {2: 7}
    assert o.result.damage == pytest.approx(7 * 1.03)


def test_bleed_triggers_on_every_coin_flip_in_clash():
    """합도 행동 — 라운드마다 남은 코인 수만큼 출혈. 1합 (A 2코인) + 공격 2코인 = 4회."""
    rt = runtime()
    a = unit("A", 10, 0, 2, statuses={"출혈": Status(potency=3, count=10)})
    b = unit("B", 1, 0, 1, side="enemy")
    o = run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert o.rounds == 1
    assert a.hp == 100 - 3 * 4 and a.statuses["출혈"].count == 6


def test_clash_win_lose_events():
    rt = runtime('identity 1 { skill S1 { on clash_win { gain self.shield 5 } on clash_lose { gain self.shield 50 } } }')
    a, b = unit("A", 10, 0, 1, ident=1), unit("B", 1, 0, 1, side="enemy", ident=1)
    run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert a.shield == 5
    assert b.shield == pytest.approx(50 - 10 * 1.01)        # [합 패배시] 보호막이 이어지는 공격(1합 보너스)을 흡수


def test_use_before_clash_before_attack_only_for_winner_after_clash():
    """[사용시] = 합 전 양쪽, [공격 시작 전] = 합 승리 후 공격 직전 (패자 없음).
    [사용시]에 얻은 보호막 1 은 합 중이라 그대로, 승자는 [합 승리시] 다음 [공격 시작 전]에 정신력 확인."""
    rt = runtime('identity 1 { skill S1 { on use { gain self.shield 1 } '
                 'on before_attack when self.sanity > 0 { gain self.shield 10 } } }')
    a, b = unit("A", 10, 0, 1, ident=1), unit("B", 1, 0, 1, side="enemy", ident=1)
    run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert a.shield == 1 + 10                    # 정신력 증가(합 종료) 뒤에 [공격 시작 전]
    assert b.shield == pytest.approx(max(0, 1 - 10 * 1.01))   # 패자: [사용시] 만, 보호막은 공격에 흡수


def test_skill_and_coin_effects_do_not_fire_during_clash_rounds():
    """스킬·코인 효과는 합 중이 아니라 합을 끝낸 뒤 공격할 때만. 2합(코인 2개 판정 ×2라운드)이어도 공격 때 코인당 1회."""
    rt = runtime('identity 1 { skill S1 { on coin_flip { gain self.shield 100 } '
                 'coin 1 { on coin_flip { gain self.shield 1 } } coin 2 { on coin_flip { gain self.shield 1 } } } }')
    a, b = unit("A", 10, 0, 2, ident=1), unit("B", 5, 0, 2, side="enemy")
    o = run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert o.rounds == 2
    assert a.shield == (100 + 1) * 2


def test_one_sided_before_attack_after_use():
    rt = runtime('identity 1 { skill S1 { on use { gain self.sanity 5 } '
                 'on before_attack when self.sanity == 5 { gain self.shield 3 } } }')
    a, b = unit("A", 10, 0, 1, ident=1), unit("B", 1, 0, 1, side="enemy")
    run_action(rt, Battle([a, b]), a, ("S1", 0), b, HEADS)
    assert a.shield == 3


def test_counter_skill_can_crit():
    """반격 스킬 적중에도 치명타 (사용자 확인)."""
    rt = runtime()
    a = unit("A", 10, 0, 1, statuses={"호흡": Status(potency=20, count=1)})
    a.skills = {("DEF", 0): SkillData("DEF", 0, "반격", None, "반격", base=10, coin_power=0, coins=1, level=50)}
    b = unit("B", 1, 0, 1, side="enemy")
    r = run_action(rt, Battle([a, b]), a, ("DEF", 0), b, HEADS)
    assert r.crits == {1: True}


def test_poise_crit_chance_and_count():
    """호흡 위력 10 → 치명타 50%. 피해 10 → 치명타 시 ×(1 + 0.2). 치명타 발동 후 횟수 -1."""
    rt = runtime()
    a = unit("A", 10, 0, 1, statuses={"호흡": Status(potency=10, count=3)})
    b = unit("B", 1, 0, 1, side="enemy")
    outs = simulate_action(rt, Battle([a, b]), "A", ("S1", 0), "B")
    assert expect(outs, lambda o: o.result.damage) == pytest.approx(0.5 * 10 + 0.5 * 12)
    for o in outs:
        crit = o.result.crits[1]
        assert o.battle.units[0].statuses["호흡"].count == (2 if crit else 3)


def test_poise_20_always_crits_without_branching():
    rt = runtime()
    a = unit("A", 10, 0, 1, statuses={"호흡": Status(potency=20, count=1)})
    b = unit("B", 1, 0, 1, side="enemy")
    outs = simulate_action(rt, Battle([a, b]), "A", ("S1", 0), "B")
    assert len(outs) == 2                                   # 코인 앞/뒤만 (고정 위력이라 피해 동일)
    assert all(o.result.damage == pytest.approx(12) for o in outs)
    assert all("호흡" not in o.battle.units[0].statuses for o in outs)


def test_no_poise_no_crit():
    rt = runtime()
    a, b = unit("A", 10, 0, 1), unit("B", 1, 0, 1, side="enemy")
    r = run_action(rt, Battle([a, b]), a, ("S1", 0), b, HEADS)
    assert r.crits == {1: False} and r.damage == pytest.approx(10)


COIN_START = ('identity 1 { skill S1 { coin 2 { on coin_start { consume self.충전.count up_to 10 -> n '
              'modify final_power n } } } }')


def test_coin_start_does_not_apply_to_clash_power():
    """[코인 시작 시] 위력 증가는 합 위력에 들어가지 않음 (사용자 확인 2026-10-28). 5 고정 vs 7 고정 → 패배, 충전 그대로."""
    rt = runtime(COIN_START)
    a = unit("A", 5, 0, 2, ident=1, statuses={"충전": Status(potency=0, count=15)})
    b = unit("B", 7, 0, 2, side="enemy")
    o = run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert o.winner == "B"
    assert a.statuses["충전"].count == 15


def test_coin_start_fires_once_per_coin_in_attack_after_clash():
    """합 2회 이겨도 소모는 공격할 때 1회. 코인 2 최종 위력 10 + 10."""
    rt = runtime(COIN_START)
    a = unit("A", 10, 0, 2, ident=1, statuses={"충전": Status(potency=0, count=15)})
    b = unit("B", 5, 0, 2, side="enemy")
    o = run_clash(rt, Battle([a, b]), a, ("S1", 0), b, ("S1", 0), HEADS)
    assert o.rounds == 2
    assert o.result.coin_power == {1: 10, 2: 20}
    assert a.statuses["충전"].count == 5
    assert o.result.damage == pytest.approx((10 + 20) * 1.02)


def test_last_coin_start_final_power_regardless_of_heads():
    """[마지막 코인 시작 시] 최종 위력 +4: 기본 4, 코인 3, 3코인 → 코인 3 = 4 + 앞면 합 + 4 (뒷면이어도 +4, 사용자 예시)."""
    rt = runtime('identity 1 { passive "p" { on coin_start when coin.is_last { modify final_power 4 coins current } } }')
    a, b = unit("A", 4, 3, 3, ident=1), unit("B", 1, 0, 1, side="enemy")
    r = run_action(rt, Battle([a, b]), a, ("S1", 0), b, scripted(True, True, False))
    assert r.coin_power == {1: 7, 2: 10, 3: 14}
    r = run_action(rt, Battle([a, b]), a, ("S1", 0), b, HEADS)
    assert r.coin_power == {1: 7, 2: 10, 3: 17}

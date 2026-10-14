"""엔진 코어 골든 테스트 — docs/rules/00-core-rules.md 기준."""
import random

import pytest

from engine.clash import ClashSide, clash, simulate
from engine.coins import expected_coin_powers, heads_prob, power_dist
from engine.damage import HitContext, hit_damage, multiplier
from engine.params import Params
from engine.resources import gain_resources, passive_active


# ---- 2장 코인 판정 ----
@pytest.mark.parametrize("sanity,p", [(0, 0.5), (1, 0.51), (45, 0.95), (-45, 0.05), (-10, 0.40)])
def test_heads_prob(sanity, p):
    assert heads_prob(sanity) == pytest.approx(p)


def test_power_dist_negative_coin():
    # #45 뫼르소 S4: 15 + (-5) x1
    assert power_dist(15, -5, 1) == pytest.approx({15: 0.5, 10: 0.5})


# ---- 3장 합: 사용자 예시 ----
def test_clash_example_from_rules():
    """승리(상대 코인1 파괴) → 패배(내 코인1 파괴) → 승리(상대 코인2 파괴) → 내 코인 2·3으로 타격."""
    me, enemy = ClashSide(base=5, coin_power=3, coins=3), ClashSide(base=5, coin_power=3, coins=2)
    script = iter([(10, 5), (5, 10), (10, 5)])
    rolls = {}

    def toss(name, side, n):
        if name == "A":
            rolls["cur"] = next(script)
            return rolls["cur"][0]
        return rolls["cur"][1]

    winner, remaining, n, log = simulate(me, enemy, toss=toss)
    assert winner == "A"
    assert remaining == [2, 3]
    assert n == 3
    assert [x[3] for x in log] == ["B 코인1 파괴", "A 코인1 파괴", "B 코인2 파괴"]


def test_clash_tie_repeats_and_counts():
    script = iter([(7, 7), (7, 7), (9, 1)])
    cur = {}

    def toss(name, side, n):
        if name == "A":
            cur["v"] = next(script)
            return cur["v"][0]
        return cur["v"][1]

    winner, remaining, n, _ = simulate(ClashSide(3, 4, 1), ClashSide(3, 4, 1), toss=toss)
    assert (winner, remaining, n) == ("A", [1], 3)


def test_clash_symmetric_is_half():
    s = ClashSide(base=4, coin_power=4, coins=3)
    r = clash(s, s)
    assert r.win == pytest.approx(0.5)
    assert r.win + r.lose + r.stalemate == pytest.approx(1)


def test_clash_deterministic_stalemate():
    s = ClashSide(base=5, coin_power=0, coins=2)
    assert clash(s, s).stalemate == pytest.approx(1)


def test_clash_dp_matches_simulation():
    a = ClashSide(base=4, coin_power=4, coins=3, sanity=10)
    b = ClashSide(base=6, coin_power=5, coins=2, sanity=-5)
    exact = clash(a, b)
    rng = random.Random(7)
    N = 40000
    wins = 0
    rem = {}
    clashes = 0
    for _ in range(N):
        w, left, n, _ = simulate(a, b, rng=rng)
        clashes += n
        if w == "A":
            wins += 1
            rem[len(left)] = rem.get(len(left), 0) + 1
    assert wins / N == pytest.approx(exact.win, abs=0.01)
    assert clashes / N == pytest.approx(exact.expected_clashes, abs=0.03)
    for k, v in exact.a_remaining.items():
        assert rem.get(k, 0) / N == pytest.approx(v, abs=0.01)


def test_expected_coin_powers():
    # 기본 3, 코인 +4 x2, 정신력 0 → 코인1: 3+4*0.5=5, 코인2: 3+4*1=7
    assert expected_coin_powers(3, 4, 2) == pytest.approx([5, 7])


# ---- 6장 피해량 공식 ----
def test_damage_formula_neutral():
    assert hit_damage(10, HitContext()) == pytest.approx(10)


def test_damage_formula_terms_are_additive_in_second_bracket():
    p = Params(res_delta={"보통": 0.0, "취약": 0.5}, offdef_k=25)
    ctx = HitContext(phys_res="취약", crit=True, clash_count=3)
    # 1 + 0.5 + 0 - 0 + 0.2 + 0.03
    assert multiplier(ctx, p) == pytest.approx(1.73)
    ctx.dmg_pct, ctx.flat_pre, ctx.flat_post = 0.4, 2, 5
    assert hit_damage(10, ctx, p) == pytest.approx((10 + 2) * 1.73 * 1.4 + 5)


def test_offdef_sign():
    # 공격 레벨이 방어 레벨보다 높으면 피해 증가
    assert multiplier(HitContext(offense_level=50, defense_level=40)) > 1
    assert multiplier(HitContext(offense_level=40, defense_level=50)) < 1


# ---- 1장 패시브 조건 ----
def test_owned_condition_example():
    owned = gain_resources({}, ["분노"] * 4)            # 전 턴 분노 스킬 4개
    assert passive_active("보유", ["분노"], 4, owned, [])
    assert not passive_active("보유", ["분노"], 5, owned, [])


def test_owned_multi_sin_is_each():
    owned = {"분노": 2, "오만": 1}
    assert not passive_active("보유", ["분노", "오만"], 2, owned, [])
    owned["오만"] = 2
    assert passive_active("보유", ["분노", "오만"], 2, owned, [])


def test_resonance_example():
    selected = ["오만", "오만", "오만", "분노", None]     # 이번 턴 팀 전체
    assert passive_active("공명", ["오만"], 2, {}, selected)
    assert passive_active("공명", ["오만"], 3, {}, selected)
    assert not passive_active("공명", ["오만"], 4, {}, selected)


def test_sinless_skill_gives_no_resource():
    assert gain_resources({}, [None, "분노"]) == {"분노": 1}


# ---- 2.1장 정신력 ----
@pytest.mark.parametrize("n,gain", [(1, 10), (2, 12), (3, 14), (5, 18)])
def test_sanity_clash_win_gain(n, gain):
    from engine.sanity import clash_win_gain
    assert clash_win_gain(n) == pytest.approx(gain)


def test_identity_resistance_multipliers():
    # 인격 취약 = ×2.0, 내성 = ×0.5
    assert multiplier(HitContext(phys_res="취약")) == pytest.approx(2.0)
    assert multiplier(HitContext(phys_res="내성")) == pytest.approx(0.5)


def test_endure_resistance():
    assert multiplier(HitContext(sin_res="견딤")) == pytest.approx(0.75)


@pytest.mark.parametrize("level,expected", [(1, 2.0), (2, 2.5), (3, 3.0)])
def test_stagger_replaces_phys_resistance(level, expected):
    # 원래 내성이 '내성'이어도 흐트러짐 배율로 대체, 죄악 내성은 그대로
    assert multiplier(HitContext(phys_res="내성", stagger=level)) == pytest.approx(expected)
    assert multiplier(HitContext(phys_res="내성", sin_res="견딤", stagger=level)) == pytest.approx(expected - 0.25)

"""M1 실행기 — #202 료슈 (오트쿠튀르::르누아르 신발관) 원문 문장별 시나리오.

spec: lpdlc-v2 examples/identity/202.lpdl, examples/keyword/renoir.lpdl
실행 의미 가정: docs/07 §4
"""
import pytest

from engine.runtime import (Battle, Status, combat_start, distribution, dummy, expect, load_runtime, load_unit,
                            run_action, simulate_action, stage_enter, turn_end, turn_start)

COAT, ARM = "르누아르 개버딘 원단 코트", "아르뮈르 에비이예"
HEADS = lambda p: True


@pytest.fixture(scope="module")
def rt():
    return load_runtime()


def setup(**sin_owned):
    u, d = load_unit(202, name="료슈"), dummy()
    return Battle([u, d], sin_owned=sin_owned), u, d


def keep(b):
    return b.units[0], b.units[1]


# ── 기본 피해 ──
def test_s1_expected_damage_matches_hand_calc(rt):
    """S1: 기본 3, 코인 +4 ×2. 코인1 = 3+4h1, 코인2 = 3+4(h1+h2) → E = 5 + 7 = 12 (보통 내성, 레벨 동일)."""
    b, _, _ = setup()
    outs = simulate_action(rt, b, "료슈", ("S1", 0), "허수아비")
    assert sum(o.prob for o in outs) == pytest.approx(1)
    assert expect(outs, lambda o: o.result.damage) == pytest.approx(12)
    assert distribution(outs, lambda o: o.result.damage) == pytest.approx({6: .25, 10: .25, 14: .25, 18: .25})


def test_s1_resources_without_coat(rt):
    """[사용시] 보존 12 미만 → +2, 코인2 [적중시] +5, 코인1 [적중시] 진동 2 부여 (새로 부여 → 횟수 1)."""
    b, u, d = setup()
    run_action(rt, b, u, ("S1", 0), d, HEADS)
    assert u.statuses["보존"].count == 7
    assert (d.statuses["진동"].potency, d.statuses["진동"].count) == (2, 1)


def test_coat_follows_self_preservation_gain(rt):
    """코트 키워드: 자신의 효과로 보존 획득 시 획득량만큼 코트 증가 (R2). 전장 등장 시 코트 25."""
    b, u, d = setup()
    stage_enter(rt, b)
    assert u.values[COAT] == 25
    run_action(rt, b, u, ("S1", 0), d, HEADS)
    assert u.values[COAT] == 25 + 2 + 5


def test_coat_50_adds_damage_pct(rt):
    """[사용시] 코트 50 이상이면 피해량 +30%."""
    b, u, _ = setup()
    u.values[COAT] = 50
    outs = simulate_action(rt, b, "료슈", ("S1", 0), "허수아비")
    assert expect(outs, lambda o: o.result.damage) == pytest.approx(12 * 1.3)


def test_armure_consumed_bonus_and_converts_to_coat(rt):
    """[사용시] 아르뮈르 최대 15 소모 → 최종 위력 +1, 피해량 +(소모×3)%. 0이 되면 코트로 전환 (R4)."""
    b, u, _ = setup()
    u.values[ARM] = 10
    outs = simulate_action(rt, b, "료슈", ("S1", 0), "허수아비")
    assert expect(outs, lambda o: o.result.damage) == pytest.approx((5 + 1 + 7 + 1) * 1.3)
    after, _ = keep(outs[0].battle)
    assert ARM not in after.values and COAT in after.values


# ── 턴당 제한 ──
def test_s1_combat_start_shield_limit_two_per_turn(rt):
    """[전투 시작시] 보존 위력 3·횟수 12 이상 → 횟수 6 소모, 최대 체력 5% 보호막 (턴당 2회). S1 3장이 잡혀도 2번만."""
    b, u, _ = setup()
    u.statuses["보존"] = Status(potency=3, count=20)
    combat_start(rt, b, {"료슈": [("S1", 0)] * 3})
    assert u.shield == pytest.approx(282 * 0.05 * 2)
    assert u.statuses["보존"].count == 8


# ── 진동 폭발 순서 (R1) + 패시브 되먹임 ──
def test_s3_coin3_three_bursts_and_feedback_passive(rt):
    """S3 코인3: [적중시] 진동 폭발 + [적중시] 보존 위력 3 이상이면 진동 폭발 2회 → 총 3회 (R1).
    되먹임: 폭발 흐트러짐 피해 +15%, 보존 12 이상이면 대신 보존 1 소모하고 +30%."""
    b, u, d = setup(나태=2, 오만=2, 질투=2)
    u.statuses["보존"] = Status(potency=3, count=0)
    d.statuses["진동"] = Status(potency=5, count=3)
    run_action(rt, b, u, ("S3", 0), d, HEADS)
    # 사용시 +5(+1 보존 위력 보너스) = 6 → 코인1 +5(+1) = 12 → 코인2 진동 위력 +3 = 8, 횟수 +3 = 6
    bursts = [x for x in b.log if x[0] == "burst"]
    assert [(x[3], x[4]) for x in bursts] == [(8, 30), (8, 15), (8, 15)]
    assert d.stagger_damage == pytest.approx(8 * 1.3 + 8 * 1.15 * 2)
    assert u.statuses["보존"].count == 11
    assert d.statuses["진동"].count == 6 - 1 - 2


def test_feedback_inactive_without_sin_resources(rt):
    b, u, d = setup()
    u.statuses["보존"] = Status(potency=3, count=0)
    d.statuses["진동"] = Status(potency=5, count=3)
    run_action(rt, b, u, ("S3", 0), d, HEADS)
    assert [x[4] for x in b.log if x[0] == "burst"] == [0, 0, 0]


# ── 패시브 보존 ──
def test_preservation_potency_every_10_consumed(rt):
    """전투 중 누적 보존 횟수 10 소모마다 보존 위력 1. S3: 10 이상이면 최대 20 소모 → 피해량 +소모%(최대 20%)."""
    b, u, _ = setup()
    u.statuses["보존"] = Status(potency=0, count=20)
    outs = simulate_action(rt, b, "료슈", ("S3", 0), "허수아비")
    after, _ = keep(outs[0].battle)
    assert after.statuses["보존"].potency == 2


def test_turn_end_low_potency_converts_preservation_to_next_turn_shield(rt):
    """턴 종료 시 보존 위력 3 미만이면 보존 횟수 전부 소모 → (소모 × 5) 보호막을 다음 턴에."""
    b, u, _ = setup()
    u.statuses["보존"] = Status(potency=0, count=7)
    turn_end(rt, b)
    assert u.shield == 0 and u.statuses["보존"].count == 0
    turn_start(rt, b)
    assert u.shield == 35


def test_armure_replaces_s3_with_one_kick(rt):
    """르누아르 개버딘 원단: 턴 시작 시 아르뮈르가 있으면 S3 → '원 킥 - 쿠튀르 뮐티플' (코인 4개)."""
    b, u, d = setup()
    u.values[ARM] = 30
    turn_start(rt, b)
    r = run_action(rt, b, u, ("S3", 0), d, HEADS)
    assert r.skill == ("S3", 1) and len(r.coin_damage) == 4

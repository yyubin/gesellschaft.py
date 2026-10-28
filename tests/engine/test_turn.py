"""M2 턴 진행: 덱·손패, 속도, 흐트러짐, E.G.O 자원 (사용자 확인 규칙 2026-10-28, DB 불필요)."""
import random
from collections import Counter

import pytest

import lpdl
from engine.runtime import Battle, Choice, Runtime, Unit, deal_hand, play, run_turn, simulate_turns
from engine.runtime.loader import DB, SPEC_DIRS, dummy, load_runtime, load_unit
from engine.runtime.state import SkillData


def runtime(src: str = "") -> Runtime:
    doc = lpdl.merge([lpdl.load([SPEC_DIRS[0]]), lpdl.parse("lpdl 2\n" + src)])
    rep = lpdl.validate(doc)
    assert rep.ok, rep.errors
    return Runtime(doc)


def unit(name, side="ally", speed=(4, 4), base=10, sin="분노", **kw) -> Unit:
    skills = {(s, 0): SkillData(s, 0, s, sin, "타격", base=base, coin_power=0, coins=1, level=50)
              for s in ("S1", "S2", "S3")}
    return Unit(name=name, side=side, skills=skills, speed_min=speed[0], speed_max=speed[1], **kw)


def test_deck_is_s1x3_s2x2_s3x1_and_reshuffles_when_empty():
    u, rng = unit("u"), random.Random(1)
    deal_hand(u, rng)
    assert Counter(u.hand + u.deck) == {("S1", 0): 3, ("S2", 0): 2, ("S3", 0): 1}
    seen = []
    for _ in range(4):                       # 덱 4장 소진
        seen.append(play(u, 0, rng))
    assert u.deck == [] and len(u.hand) == 2
    play(u, 0, rng)                          # 빈 덱 → 6장 다시 섞어 보충
    assert len(u.deck) == 5


def test_unused_slot_stays():
    u, rng = unit("u"), random.Random(2)
    deal_hand(u, rng)
    keep = u.hand[1]
    play(u, 0, rng)
    assert u.hand[1] == keep


def test_speed_uniform_in_range_and_faster_acts_first():
    rt = runtime()
    a, b = unit("A", speed=(1, 3)), unit("B", speed=(5, 7))
    foe = dummy()
    battle = Battle([a, b, foe])
    rng = random.Random(0)
    for u in (a, b):
        deal_hand(u, rng)
    speeds = Counter()
    for _ in range(300):
        log = run_turn(rt, battle, rng, lambda bt, u: Choice(0, foe.name))
        assert [x.unit for x in log.actions] == ["B", "A"]
        speeds[a.speed] += 1
    assert set(speeds) == {1, 2, 3} and min(speeds.values()) > 70


def test_stagger_cancels_rest_of_turn_blocks_next_turn_recovers_at_its_end():
    """적(속도 9)이 A(속도 1)를 때려 흐트러짐 선 50% 아래로 → A 이번 턴 취소, 다음 턴 행동 불가, 다음 턴 종료 시 회복."""
    rt = runtime()
    a = unit("A", speed=(1, 1), max_hp=100, hp=100, stagger_lines=[50])
    e = unit("E", side="enemy", speed=(9, 9), base=60)
    battle = Battle([a, e])
    rng = random.Random(0)
    deal_hand(a, rng), deal_hand(e, rng)
    pol = lambda bt, u: Choice(0, "E" if u is a else "A")
    t1 = run_turn(rt, battle, rng, pol)
    assert a.hp == 40 and a.stagger == 1 and t1.cancelled == ["A"]
    assert a.stagger_lines == []
    e.hand = None                                    # 이후 적은 행동 안 함
    t2 = run_turn(rt, battle, rng, pol)
    assert t2.actions == [] and a.stagger == 0      # 2턴: 행동 불가, 종료 시 회복
    t3 = run_turn(rt, battle, rng, pol)
    assert [x.unit for x in t3.actions] == ["A"]


def test_staggered_target_takes_phys_multiplier():
    rt = runtime()
    a, foe = unit("A"), dummy()
    foe.stagger, foe.stagger_until = 1, 99
    rng = random.Random(0)
    deal_hand(a, rng)
    log = run_turn(rt, Battle([a, foe]), rng, lambda bt, u: Choice(0, foe.name))
    assert log.actions[0].damage == pytest.approx(10 * 2)


def test_multiple_lines_at_once_is_stagger_plus():
    rt = runtime()
    a = unit("A", base=80)
    e = unit("E", side="enemy", max_hp=100, hp=100, stagger_lines=[60, 30])
    e.hand = None
    rng = random.Random(0)
    deal_hand(a, rng)
    run_turn(rt, Battle([a, e]), rng, lambda bt, u: Choice(0, "E"))
    assert e.stagger == 2


def test_sin_resources_count_from_next_turn():
    """보유 조건: 이번 턴 사용한 스킬 속성은 턴 종료 후 반영 → 다음 턴부터 패시브 발동."""
    rt = runtime('identity 1 { passive "p" { requires hold [분노] 1 on use { gain self.shield 1 } } }')
    a, foe = unit("A", identity_id=1), dummy()
    battle, rng = Battle([a, foe]), random.Random(0)
    deal_hand(a, rng)
    run_turn(rt, battle, rng, lambda bt, u: Choice(0, foe.name))
    assert a.shield == 0 and battle.sin_owned == {"분노": 1}
    run_turn(rt, battle, rng, lambda bt, u: Choice(0, foe.name))
    assert a.shield == 1


@pytest.mark.skipif(not DB.exists(), reason="DB 없음")
def test_202_runs_multiple_turns():
    rt = load_runtime()
    u = load_unit(202)
    runs = simulate_turns(rt, Battle([u, dummy()]), 5, runs=30)
    assert len(runs) == 30 and all(len(r) == 5 for r in runs)
    assert all(len(row["log"].actions) == 1 for r in runs for row in r)


def test_static_speed_modifiers_apply_to_roll():
    rt = runtime('identity 1 { passive "p" { always { modify speed_min 2 modify speed_max 2 } } }')
    a, foe = unit("A", identity_id=1, speed=(3, 3)), dummy()
    rng = random.Random(0)
    deal_hand(a, rng)
    log = run_turn(rt, Battle([a, foe]), rng, lambda bt, u: Choice(0, foe.name))
    assert log.actions[0].speed == 5

"""M3: 적 의도 공개, 가로채기 합, 적의 공격·아군 사망, 팀 vs 보스 (사용자 확인 규칙 2026-10-28)."""
import random

import pytest

import lpdl
from engine.runtime import Battle, Choice, Runtime, Unit, deal_hand, run_turn
from engine.runtime.loader import DB, SPEC_DIRS, load_runtime, load_unit
from engine.runtime.scenarios import boss
from engine.runtime.state import SkillData
from engine.runtime.turn import battle_over, begin_turn, resolve_turn


def runtime() -> Runtime:
    doc = lpdl.load([SPEC_DIRS[0]])
    assert lpdl.validate(doc).ok
    return Runtime(doc)


def ally(name, speed, base=10, hp=100, **kw) -> Unit:
    skills = {(s, 0): SkillData(s, 0, s, "분노", "타격", base=base, coin_power=0, coins=1, level=50)
              for s in ("S1", "S2", "S3")}
    return Unit(name=name, skills=skills, speed_min=speed, speed_max=speed, max_hp=hp, hp=hp, **kw)


def fixed_boss(speeds=(4, 4), base=5, hp=1000, **kw):
    b = boss(hp=hp, dice=len(speeds), speed=(min(speeds), max(speeds)),
             skills=[("일격", "참격", "분노", base, 0, 1)], pattern=["일격"], level=50, def_level=0, **kw)
    return b


def setup(*allies, boss_unit, seed=0):
    battle = Battle([*allies, boss_unit])
    rng = random.Random(seed)
    for a in allies:
        deal_hand(a, rng)
    return battle, rng


def test_intents_revealed_before_choice_and_pattern_cycles():
    rt = runtime()
    a, b = ally("A", 3), ally("B", 3)
    e = boss(dice=2, speed=(4, 4))
    battle, rng = setup(a, b, boss_unit=e)
    begin_turn(rt, battle, rng)
    assert [(i.die, i.skill) for i in battle.intents] == [(0, ("E1", 0)), (1, ("E2", 0))]
    assert all(i.target in ("A", "B") and i.speed == 4 for i in battle.intents)
    resolve_turn(rt, battle, rng, [a, b], {"A": Choice(0, "보스"), "B": Choice(0, "보스")})
    begin_turn(rt, battle, rng)
    assert [i.skill for i in battle.intents] == [("E1", 0), ("E3", 0)]          # 패턴 E1 E2 E1 E3 순환


def test_faster_ally_intercepts_die_aimed_at_someone_else():
    """A(속도 5) 가 주사위 0(속도 4, B 를 노림)을 대상으로 → A 와 합. B 는 맞지 않음."""
    rt = runtime()
    a, b = ally("A", 5, base=20), ally("B", 1)
    e = fixed_boss(speeds=(4,))
    battle, rng = setup(a, b, boss_unit=e)
    begin_turn(rt, battle, rng)
    battle.intents = [battle.intents[0].__class__("보스", 0, ("E1", 0), "B", 4)]
    log = resolve_turn(rt, battle, rng, [a, b], {"A": Choice(0, "보스", 0), "B": Choice(0, "보스", 0)})
    clash = [x for x in log.actions if x.kind == "clash"]
    assert {x.unit for x in clash} == {"A", "보스"} and clash[0].clash["winner"] == "A"
    assert b.hp == b.max_hp


def test_slower_ally_cannot_intercept_both_one_sided():
    rt = runtime()
    a = ally("A", 3)
    e = fixed_boss(speeds=(4,))
    battle, rng = setup(a, boss_unit=e)
    begin_turn(rt, battle, rng)
    log = resolve_turn(rt, battle, rng, [a], {"A": Choice(0, "보스", 0)})
    assert [x.kind for x in log.actions] == ["one-sided", "one-sided"]
    assert [x.unit for x in log.actions] == ["보스", "A"]                        # 속도순
    assert a.hp == 100 - 5 and e.hp == 1000 - 10


def test_last_ally_to_target_the_die_gets_the_clash():
    rt = runtime()
    a, b = ally("A", 5), ally("B", 6)
    e = fixed_boss(speeds=(4,))
    battle, rng = setup(a, b, boss_unit=e)
    begin_turn(rt, battle, rng)
    log = resolve_turn(rt, battle, rng, [a, b], {"A": Choice(0, "보스", 0), "B": Choice(0, "보스", 0)})
    assert {x.unit for x in log.actions if x.kind == "clash"} == {"B", "보스"}
    assert [x.kind for x in log.actions if x.unit == "A"] == ["one-sided"]


def test_dead_ally_does_not_act_and_battle_ends_when_allies_die():
    rt = runtime()
    a = ally("A", 1, hp=5)
    e = fixed_boss(speeds=(9,), base=10)
    battle, rng = setup(a, boss_unit=e)
    log = run_turn(rt, battle, rng, lambda bt, u: Choice(0, "보스", 0))
    assert a.hp == 0 and log.cancelled == ["A"] and battle_over(battle)


@pytest.mark.skipif(not DB.exists(), reason="DB 없음")
def test_renoir_trio_vs_boss_runs():
    rt = load_runtime()
    team = [load_unit(i, formation=n) for n, i in enumerate((202, 203, 204))]
    battle = Battle([*team, boss()])
    rng = random.Random(0)
    for u in team:
        deal_hand(u, rng)
    from engine.runtime import stage_enter
    stage_enter(rt, battle)
    kinds = set()
    for _ in range(6):
        log = run_turn(rt, battle, rng)
        kinds |= {x.kind for x in log.actions}
        if battle_over(battle):
            break
    assert "clash" in kinds
    assert battle.units[3].hp < battle.units[3].max_hp


def def_ally(name, speed, def_type, base=7):
    u = ally(name, speed)
    u.skills[("DEF", 0)] = SkillData("DEF", 0, "d", "분노", def_type, base=base, coin_power=0, coins=1, level=50)
    return u


def test_counter_reacts_to_first_incoming_attack_after_damage():
    rt = runtime()
    a = def_ally("A", 1, "반격")
    e = fixed_boss(speeds=(4, 3))
    battle, rng = setup(a, boss_unit=e)
    begin_turn(rt, battle, rng)
    log = resolve_turn(rt, battle, rng, [a], {"A": Choice("DEF", "보스", 0)})
    kinds = [(x.unit, x.kind) for x in log.actions]
    assert kinds == [("보스", "one-sided"), ("A", "counter"), ("보스", "one-sided")]   # 첫 공격에만 반격
    assert a.hp == 100 - 5 * 2 and e.hp == 1000 - 7


def test_guard_shield_absorbs_first_attack():
    rt = runtime()
    a = def_ally("A", 1, "방어", base=4)
    e = fixed_boss(speeds=(4,), base=5)
    battle, rng = setup(a, boss_unit=e)
    begin_turn(rt, battle, rng)
    log = resolve_turn(rt, battle, rng, [a], {"A": Choice("DEF", "보스", 0)})
    assert [x.kind for x in log.actions] == ["guard", "one-sided"]
    assert a.hp == 100 - 1


def test_use_skill_extra_one_sided_attack():
    """#204 신의 뜻: 르누아르 아군 공격 시작 전 심판! (S1 variant 1) 추가 공격."""
    doc = lpdl.merge([lpdl.load([SPEC_DIRS[0]]), lpdl.parse('''lpdl 2
    identity 1 { passive "p" { on ally_before_attack when target != self limit 1/turn {
        use_skill S1 variant 1 on pick(enemies, n: 1) } } }''')])
    assert lpdl.validate(doc).ok
    rt = Runtime(doc)
    d = ally("D", 1, identity_id=1)
    d.skills[("S1", 1)] = SkillData("S1", 1, "심판", "오만", "타격", base=3, coin_power=0, coins=1, level=50)
    r = ally("R", 5)
    e = fixed_boss(speeds=(1,))
    e.pattern = None                                      # 공격 안 함
    battle, rng = setup(r, d, boss_unit=e)
    begin_turn(rt, battle, rng)
    resolve_turn(rt, battle, rng, [r, d], {"R": Choice(0, "보스"), "D": Choice(0, "보스")})
    assert ("use_skill", "D", ("S1", 1), "보스", 3.0) in battle.log
    assert e.hp == 1000 - 10 - 3 - 10                    # R 10 + 심판 3 + D 10 (D 의 자기 공격 땐 발동 안 함)

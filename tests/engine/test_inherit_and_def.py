"""prop inherit, 수치형 부여·부여량 수정, 수비 스킬의 행동 여부·[전투 시작시] 교체 (DB 불필요)."""
import random

import pytest

import lpdl
from engine.runtime import Battle, Choice, Runtime, Status, Unit, deal_hand, run_action, run_turn
from engine.runtime.loader import SPEC_DIRS, dummy
from engine.runtime.state import SkillData

HEADS = lambda p: True


def runtime(src: str) -> Runtime:
    doc = lpdl.merge([lpdl.load([SPEC_DIRS[0]]), lpdl.parse("lpdl 2\n" + src)])
    rep = lpdl.validate(doc)
    assert rep.ok, rep.errors
    return Runtime(doc)


def unit(name="u", def_type="반격", **kw) -> Unit:
    skills = {(s, 0): SkillData(s, 0, s, "나태", "타격", base=10, coin_power=0, coins=1, level=50)
              for s in ("S1", "S2", "S3")}
    skills[("DEF", 0)] = SkillData("DEF", 0, "d", "나태", def_type, base=5, coin_power=0, coins=1, level=50)
    skills[("S3", 1)] = SkillData("S3", 1, "s3v1", "나태", "타격", base=50, coin_power=0, coins=1, level=50)
    return Unit(name=name, identity_id=1, skills=skills, speed_min=3, speed_max=3, **kw)


CHILD = '''
keyword "진동 - 보존" { prop kind status prop inherit "진동" }
keyword "못" { prop kind value prop max 5 }
'''


def test_inherit_redirects_gain_apply_burst_and_event_filter():
    """대상이 진동 대신 진동 - 보존을 가지면 진동을 가리키는 증감·부여·폭발·[폭발 시]가 그쪽에 적용."""
    rt = runtime(CHILD + '''identity 1 {
      passive "p" { on burst(진동) { gain self.shield 1 } }
      skill S1 { coin 1 { on hit { gain target.진동.count 2  apply target 진동 potency 3  burst target 진동 } } }
    }''')
    me, foe = unit(), dummy()
    foe.statuses["진동 - 보존"] = Status(4, 1)
    run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert "진동" not in foe.statuses
    assert (foe.statuses["진동 - 보존"].potency, foe.statuses["진동 - 보존"].count) == (7, 3)
    assert foe.stagger_damage == 7 and me.shield == 1


def test_value_keyword_apply_adds_value_with_max_and_applied_can_change_amount():
    rt = runtime(CHILD + '''identity 1 {
      passive "p" { on applied(못) by self { modify amount 1 } }
      skill S1 { coin 1 { on hit { apply target 못 potency 2 } } }
    }''')
    me, foe = unit(), dummy()
    b = Battle([me, foe])
    run_action(rt, b, me, ("S1", 0), foe, HEADS)
    assert foe.values == {"못": 3} and "못" not in foe.statuses
    run_action(rt, b, me, ("S1", 0), foe, HEADS)
    assert foe.values["못"] == 5                       # max 5


def test_passive_defense_does_not_attack_but_combat_start_runs():
    rt = runtime('identity 1 { skill DEF { on combat_start { gain self.sanity 6 } } }')
    me, foe = unit(), dummy()
    rng = random.Random(0)
    deal_hand(me, rng)
    log = run_turn(rt, Battle([me, foe]), rng, lambda b, u: Choice("DEF", foe.name))
    assert me.sanity == 6 and log.actions[0].damage == 0 and foe.hp == foe.max_hp


def test_combat_start_replace_this_sticks_and_attacks():
    """#204 묵시: 가호 100 이면 [전투 시작시] 이 스킬 → S3 variant 1 (공격)."""
    rt = runtime('''keyword "가호" { prop kind value }
    identity 1 { skill DEF { on combat_start when self.가호 == 100 { replace this -> S3 variant 1 } } }''')
    me, foe = unit(values={"가호": 100}), dummy()
    rng = random.Random(0)
    deal_hand(me, rng)
    log = run_turn(rt, Battle([me, foe]), rng, lambda b, u: Choice("DEF", foe.name))
    assert log.actions[0].skill == ("S3", 1) and log.actions[0].damage == pytest.approx(50)

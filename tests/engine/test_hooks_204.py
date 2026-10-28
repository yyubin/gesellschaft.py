"""#204 훅 (사용자 확인 2026-10-28, DB 불필요)."""
import pytest

import lpdl
from engine.runtime import Battle, Runtime, Unit, run_action
from engine.runtime.hooks import HOOKS
from engine.runtime.loader import SPEC_DIRS, dummy
from engine.runtime.state import SkillData

HEADS = lambda p: True
SRC = '''
keyword "보존 신역" { prop kind value }
identity 1 {
  skill S1 {
    coin 1 { on hit { hook mark_skill_as_charge_consuming } }
  }
  skill S2 { on use { hook damage_pct_per_targets_below_weight(50) } }
}
'''


def setup(**kw):
    doc = lpdl.merge([lpdl.load([SPEC_DIRS[0]]), lpdl.parse("lpdl 2\n" + SRC)])
    assert lpdl.validate(doc).ok
    rt = Runtime(doc, hooks=HOOKS)
    skills = {(s, 0): SkillData(s, 0, s, "나태", "타격", base=10, coin_power=0, coins=2, level=50) for s in ("S1", "S2")}
    me = Unit(name="me", identity_id=1, skills=skills, affiliations={"르누아르"}, **kw)
    return rt, me, dummy()


def test_charge_consuming_bonus_applies_from_first_coin_retroactively():
    """코인 1 적중 뒤 표시되어도 코인 1 피해까지 +20% (르누아르 2배)."""
    rt, me, foe = setup(values={"보존 신역": 1})
    r = run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert r.coin_damage == {1: pytest.approx(10 * 1.2), 2: pytest.approx(10 * 1.2)}
    assert r.damage == pytest.approx(24) and foe.max_hp - foe.hp == pytest.approx(24)


def test_charge_consuming_no_bonus_without_sanctuary():
    rt, me, foe = setup()
    r = run_action(rt, Battle([me, foe]), me, ("S1", 0), foe, HEADS)
    assert r.damage == pytest.approx(20)


def test_single_target_below_weight_gets_full_bonus():
    rt, me, foe = setup()
    r = run_action(rt, Battle([me, foe]), me, ("S2", 0), foe, HEADS)
    assert r.damage == pytest.approx(20 * 1.5)

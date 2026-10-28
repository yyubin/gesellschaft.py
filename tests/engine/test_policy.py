"""탐색 정책 P1 (docs/08): 결정화, 합법 행동, 빌드업을 위해 약한 스킬을 고르는지 (DB 불필요)."""
import random
from collections import Counter

import lpdl
from engine.runtime import Battle, Choice, RolloutPolicy, Runtime, Unit, deal_hand, legal_choices, policy_highest
from engine.runtime.loader import SPEC_DIRS, dummy
from engine.runtime.policy import determinize
from engine.runtime.state import SkillData
from engine.runtime.turn import begin_turn


def runtime(src: str = "") -> Runtime:
    doc = lpdl.merge([lpdl.load([SPEC_DIRS[0]]), lpdl.parse("lpdl 2\n" + src)])
    rep = lpdl.validate(doc)
    assert rep.ok, rep.errors
    return Runtime(doc)


def unit(name, bases=(1, 10, 10), def_type=None, **kw) -> Unit:
    skills = {(s, 0): SkillData(s, 0, s, "분노", "타격", base=b, coin_power=0, coins=1, level=50)
              for s, b in zip(("S1", "S2", "S3"), bases)}
    if def_type:
        skills[("DEF", 0)] = SkillData("DEF", 0, "d", None, def_type, base=5, coin_power=0, coins=1, level=50)
    return Unit(name=name, skills=skills, speed_min=3, speed_max=3, **kw)


def test_determinize_reshuffles_remaining_deck_keeps_composition_and_hand():
    u = unit("u")
    deal_hand(u, random.Random(0))
    b = Battle([u])
    seen = set()
    for i in range(20):
        d = determinize(b, random.Random(i)).units[0]
        assert Counter(d.deck) == Counter(u.deck) and d.hand == u.hand
        seen.add(tuple(d.deck))
    assert len(seen) > 1


def test_legal_choices_dedupe_same_card_and_def_only_if_attacking():
    rt = runtime()
    foe = dummy()
    u = unit("u", def_type="방어")
    u.hand = [("S1", 0), ("S1", 0)]
    assert legal_choices(rt, Battle([u, foe]), u) == [Choice(0, foe.name)]
    v = unit("v", def_type="참격")
    v.hand = [("S1", 0), ("S2", 0)]
    assert [c.card for c in legal_choices(rt, Battle([v, foe]), v)] == [0, 1, "DEF"]


BUILDUP = '''
keyword "기세" { prop kind value }
identity 1 {
  skill S1 { on use { gain self.기세 1 } }
  skill S2 { on use { modify damage_pct self.기세 * 200 } }
  skill S3 { on use { modify damage_pct self.기세 * 200 } }
}
'''


def test_rollout_takes_weak_buildup_skill_that_highest_skips():
    """S1 = 피해 1 + 기세 1, S2·S3 = 피해 10 × (1 + 2×기세). 체력 60: 1턴 S1 이면 3턴 처치, S2 먼저면 더 늦음."""
    rt = runtime(BUILDUP)
    u, foe = unit("u", identity_id=1), dummy(hp=60)
    u.hand, u.deck = [("S1", 0), ("S2", 0)], [("S1", 0), ("S1", 0), ("S2", 0), ("S3", 0)]
    b = Battle([u, foe])
    begin_turn(rt, b, random.Random(0))
    assert policy_highest(b, u).card == 1
    pol = RolloutPolicy(rt, horizon=4, rollouts=16)
    assert pol(b, u).card == 0
    s1, s2 = pol.last[Choice(0, foe.name)], pol.last[Choice(1, foe.name)]
    assert s1 < s2

"""P2 계획 DSL·평가 도구·하네스 루프 (가짜 LLM, DB 불필요)."""
import json
import random

import pytest

import lpdl
from engine.agent import plan as P
from engine.agent.harness import run_guide
from engine.agent.tools import evaluate
from engine.runtime import Battle, Runtime, Status, Unit, deal_hand
from engine.runtime.loader import SPEC_DIRS, dummy
from engine.runtime.state import SkillData
from engine.runtime.turn import Choice

SRC = '''
keyword "기세" { prop kind value }
identity 1 {
  use 힘 = "기세"
  skill S1 { on use { gain self.기세 1 } }
  skill S2 { on use { modify damage_pct self.기세 * 200 } }
  skill S3 { on use { modify damage_pct self.기세 * 200 } }
}
'''


def runtime() -> Runtime:
    doc = lpdl.merge([lpdl.load([SPEC_DIRS[0]]), lpdl.parse("lpdl 2\n" + SRC)])
    assert lpdl.validate(doc).ok
    return Runtime(doc)


def unit(**kw) -> Unit:
    skills = {(s, 0): SkillData(s, 0, s, "분노", "타격", base=b, coin_power=0, coins=1, level=50)
              for s, b in zip(("S1", "S2", "S3"), (1, 10, 10))}
    return Unit(name="u", identity_id=1, skills=skills, speed_min=3, speed_max=3, **kw)


def pick(rt, plan_dict, hand, **kw):
    u, foe = unit(**kw), dummy()
    u.hand = [(c, 0) for c in hand]
    b = Battle([u, foe])
    pol = P.PlanPolicy(rt, P.Plan.from_dict(plan_dict))
    c = pol(b, u)
    return u.hand[c.card][0] if c.card != "DEF" else "DEF", pol.fired


def test_rules_in_order_use_first_available_and_default():
    rt = runtime()
    plan = {"rules": [{"when": "self.힘 < 1", "use": ["S1"]}, {"use": ["S3", "S2"]}], "default": "top"}
    assert pick(rt, plan, ["S2", "S1"])[0] == "S1"                     # 기세 0 → S1
    assert pick(rt, plan, ["S2", "S1"], values={"기세": 1})[0] == "S2"  # 다음 규칙: S3 없음 → S2
    card, fired = pick(rt, plan, ["S1", "S1"], values={"기세": 1})
    assert card == "S1" and fired == {-1: 1}                           # 어느 규칙도 못 씀 → default


def test_avoid_holds_card_unless_forced():
    rt = runtime()
    plan = {"rules": [{"when": "self.기세 < 2", "avoid": ["S3"]}], "default": "highest"}
    assert pick(rt, plan, ["S3", "S1"])[0] == "S1"
    assert pick(rt, plan, ["S3", "S1"], values={"기세": 2})[0] == "S3"
    assert pick(rt, plan, ["S3", "S3"])[0] == "S3"                     # 그것뿐이면 씀


def test_condition_names_hand_turn_status_fields():
    rt = runtime()
    plan = {"rules": [{"when": "'S3' in hand and turn >= 1 and self.출혈.count == 0 and target.진동.potency >= 3",
                       "use": ["S1"]}], "default": "highest"}
    u, foe = unit(), dummy()
    foe.statuses["진동"] = Status(4, 1)
    u.hand = [("S3", 0), ("S1", 0)]
    pol = P.PlanPolicy(rt, P.Plan.from_dict(plan))
    assert pol(Battle([u, foe]), u) == Choice(1, foe.name)


@pytest.mark.parametrize("when, msg", [
    ("self.없는이름 +", "문법"),
    ("foo > 1", "모르는 이름"),
    ("self.출혈 > 1", ".potency"),
    ("self.기세.potency > 1", None),       # 수치형에 .potency → 오류
])
def test_plan_errors_are_explained(when, msg):
    rt = runtime()
    try:
        pl = P.Plan.from_dict({"rules": [{"when": when, "use": ["S1"]}]})
        u = unit()
        u.hand = [("S1", 0), ("S2", 0)]
        P.check_plan(rt, Battle([u, dummy()]), u, pl)
    except P.PlanError as e:
        assert msg is None or msg in str(e)
    else:
        pytest.fail("PlanError 가 나야 함")


def test_bad_card_and_keys_rejected():
    with pytest.raises(P.PlanError, match="카드 이름"):
        P.Plan.from_dict({"rules": [{"use": ["S4"]}]})
    with pytest.raises(P.PlanError, match="모르는 키"):
        P.Plan.from_dict({"rules": [{"use": ["S1"], "then": 1}]})


def test_buildup_plan_beats_highest_in_evaluate():
    rt = runtime()
    battle = Battle([unit(), dummy(hp=60)])
    build = P.Plan.from_dict({"rules": [{"when": "self.기세 < 1", "use": ["S1"]}, {"avoid": ["S1"]}]})
    hi = P.Plan.from_dict({"rules": [], "default": "highest"})
    rb = evaluate(rt, battle, lambda: P.PlanPolicy(rt, build), games=100)
    rh = evaluate(rt, battle, lambda: P.PlanPolicy(rt, hi), games=100)
    assert rb["mean_kill_turn"] < rh["mean_kill_turn"]
    assert rb["rule_fired"]["rules[0]"] == 100           # 판마다 S1 이 처음 손에 들어온 턴에 1회


class FakeConv:
    """라운드마다 정해진 응답. 받은 메시지를 기록."""
    def __init__(self, replies):
        self.replies, self.got = list(replies), []

    def send(self, text):
        self.got.append(text)
        return self.replies.pop(0)


def test_guide_loop_feeds_back_results_and_errors(monkeypatch):
    rt = runtime()
    monkeypatch.setattr("engine.agent.harness.spec_source", lambda rt, i: SRC)
    battle = Battle([unit(), dummy(hp=60)])
    good = {"name": "빌드업", "hypothesis": "기세 먼저", "rules": [{"when": "self.기세 < 1", "use": ["S1"]}, {"avoid": ["S1"]}]}
    bad = {"name": "오타", "rules": [{"when": "self.기세.count > 1", "use": ["S1"]}]}
    conv = FakeConv([f"분석...\n```json\n{json.dumps([good, bad], ensure_ascii=False)}\n```",
                     f"```json\n{json.dumps([good], ensure_ascii=False)}\n```",
                     "# 가이드\n기세를 먼저 쌓는다"])
    run = run_guide(rt, battle, 1, lambda system: conv, hp=60, refs={"highest": 9.9}, rounds=2, n_plans=2,
                    games=50, log=lambda *a: None)
    assert run.best.plan.name == "빌드업"
    assert [c.error is None for c in run.candidates] == [True, False, True]
    assert "수치형 키워드" in conv.got[1]                  # 오류 이유가 LLM 에게 돌아감
    assert "mean_kill_turn" in conv.got[1] and "error" in conv.got[1]
    assert run.guide.startswith("# 가이드")


def test_one_bad_plan_does_not_drop_the_others():
    good = {"name": "a", "rules": [{"use": ["S1"]}]}
    bad = {"name": "b", "rules": [{"use": ["S3v1"]}]}
    plans, errors = P.parse_plans(f"```json\n{json.dumps([good, bad])}\n```")
    assert [p.name for p in plans] == ["a"]
    assert len(errors) == 1 and errors[0].startswith("b:") and "원래 칸" in errors[0]

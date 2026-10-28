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
    run = run_guide(rt, battle, 1, lambda system: conv, refs={"highest": 9.9}, rounds=2, n_plans=2,
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


def test_ablation_flags_harmful_rule_and_best_prefers_simpler_plan(monkeypatch):
    """r0 = 2턴째 S1 (해로움: 기세 1 이후 S1 은 낭비), r1 = 기세 0 이면 S1, r2 = S1 보관."""
    from engine.agent.harness import Candidate, GuideRun, _eval_cached, ablate
    rt = runtime()
    battle = Battle([unit(), dummy(hp=60)])
    cache = {}
    good = P.Plan.from_dict({"name": "good", "rules": [{"when": "self.기세 < 1", "use": ["S1"]}, {"avoid": ["S1"]}]})
    noisy = P.Plan.from_dict({"name": "noisy", "rules": [{"when": "turn == 2", "use": ["S1"]},
                                                          {"when": "self.기세 < 1", "use": ["S1"]}, {"avoid": ["S1"]}]})
    cg = Candidate(good, _eval_cached(rt, [battle], good, 200, cache))
    cn = Candidate(noisy, _eval_cached(rt, [battle], noisy, 200, cache))
    abl = ablate(rt, [battle], cn, 200, cache)
    assert abl[0]["delta"] < 0 and abs(abl[0]["delta"]) > 2 * abl[0]["se_delta"]      # 빼면 빨라짐 = 해로운 규칙
    assert abl[1]["delta"] > 0                                                         # 빌드업 규칙은 도움
    run = GuideRun(1, candidates=[cn, cg])
    assert run.best is cg


def test_best_tie_break_fewer_rules_within_noise():
    from engine.agent.harness import Candidate, GuideRun
    r = lambda turns: {"mean_kill_turn": sum(turns) / len(turns), "turns": turns, "se": 0}
    a = Candidate(P.Plan.from_dict({"name": "a", "rules": [{"use": ["S1"]}, {"use": ["S2"]}]}), r([5, 6, 5, 6]))
    b = Candidate(P.Plan.from_dict({"name": "b", "rules": [{"use": ["S1"]}]}), r([5, 6, 6, 5]))   # 평균 같음
    c = Candidate(P.Plan.from_dict({"name": "c", "rules": []}), r([7, 8, 7, 8]))                 # 확실히 느림
    assert GuideRun(1, candidates=[a, b, c]).best is b


def test_distill_finds_buildup_deviation_and_refine_loop_runs(monkeypatch):
    from engine.agent.distill import collect, summarize
    from engine.agent.harness import run_refine
    rt = runtime()
    battles = [Battle([unit(), dummy(hp=60)])]
    hi = P.Plan.from_dict({"name": "highest", "rules": []})
    summ = summarize(collect(rt, battles, hi, games=4, rollouts=8))
    assert summ["deviations"] > 0
    assert any(p["rollout"] == "S1" for p in summ["patterns"])           # 탐색은 약한 S1 로 기세를 쌓음
    monkeypatch.setattr("engine.agent.harness.spec_source", lambda rt, i: SRC)
    good = {"name": "빌드업", "rules": [{"when": "self.기세 < 1", "use": ["S1"]}, {"avoid": ["S1"]}]}
    conv = FakeConv([f"```json\n{json.dumps([good], ensure_ascii=False)}\n```", "# 가이드"])
    run = run_refine(rt, battles, 1, lambda system: conv, hi, refs={}, iterations=1, n_plans=1, games=50,
                     dev_games=2, rollouts=4, log=lambda *a: None)
    assert run.best.plan.name == "빌드업"
    assert "patterns" in conv.got[0] and run.guide == "# 가이드"


def test_deck_slots_and_count_in_conditions():
    rt = runtime()
    u, foe = unit(), dummy()
    u.hand, u.deck = [("S2", 0), ("S3", 0)], [("S1", 0), ("S1", 0)]
    b = Battle([u, foe])
    pl = P.Plan.from_dict({"rules": [{"when": "hand[1] == 'S3' and count(deck, 'S3') == 0 and len(deck) == 2",
                                      "use": ["S2"]}]})
    assert P.PlanPolicy(rt, pl)(b, u).card == 0
    with pytest.raises(P.PlanError, match="함수는"):
        P.check_plan(rt, b, u, P.Plan.from_dict({"rules": [{"when": "sum(deck) > 1", "use": ["S1"]}]}))


def test_team_plan_targets_and_intent_conditions():
    """팀 계획: 인격별 규칙 + 대상 방식. one_sided 면 합이 안 되는 주사위, block 이면 나를 노리는 주사위."""
    from engine.agent.team import TeamPlan, TeamPlanPolicy
    from engine.runtime.scenarios import boss
    from engine.runtime.turn import Intent, begin_turn
    rt = runtime()
    u = unit()
    e = boss(hp=1000, dice=2, speed=(4, 4), level=50, def_level=0)
    b = Battle([u, e])
    deal_hand(u, random.Random(0))
    begin_turn(rt, b, random.Random(0))
    u.speed = 5
    b.intents = [Intent("보스", 0, ("E1", 0), "u", 4), Intent("보스", 1, ("E3", 0), "u", 9)]
    tp = TeamPlan.from_dict({"name": "t", "units": {"1": {
        "rules": [{"when": "incoming == 2 and blockable == 1 and incoming_power > 15", "target": "block"}]}}})
    assert TeamPlanPolicy(rt, tp).joint(b, [u])["u"].die == 0
    tp2 = TeamPlan.from_dict({"name": "t", "units": {"1": {"rules": [], "default_target": "one_sided"}}})
    assert TeamPlanPolicy(rt, tp2).joint(b, [u])["u"].die == 1
    with pytest.raises(P.PlanError, match="target"):
        TeamPlan.from_dict({"units": {"1": {"rules": [{"target": "nope"}]}}})


def test_team_guide_loop_with_fake_llm(monkeypatch):
    from engine.agent.team_harness import run_team_guide
    from engine.runtime.scenarios import boss
    rt = runtime()
    monkeypatch.setattr("engine.agent.team_harness.spec_source", lambda rt, i: SRC)
    battles = [Battle([unit(), boss(hp=200, dice=1, speed=(1, 1), level=50, def_level=0)])]
    good = {"name": "빌드업", "units": {"1": {"rules": [{"when": "self.기세 < 1", "use": ["S1"]}, {"avoid": ["S1"]}]}}}
    conv = FakeConv([f"```json\n{json.dumps([good], ensure_ascii=False)}\n```", "# 팀 가이드"])
    run = run_team_guide(rt, battles, [1], lambda system: conv, boss_desc="{}", refs={}, rounds=1, n_plans=1,
                         games=20, log=lambda *a: None)
    assert run.best.plan.name == "빌드업" and run.best.ablation and run.guide == "# 팀 가이드"

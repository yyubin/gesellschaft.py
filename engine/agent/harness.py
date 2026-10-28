"""LLM 하네스 (docs/08 §3): 가이드 생성 루프 + 조언 모드.

가이드: 프롬프트(규칙·spec·기준 성능) → LLM 이 계획 N개 → 엔진 평가 → 결과 피드백 → 수정 (R 라운드)
        → 최고 계획으로 운용 가이드 작성. 숫자는 전부 평가 결과에서만 인용하게 한다.
조언: 상태 → 롤아웃 정책의 후보별 점수 → LLM 이 이유 설명.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.runtime import RolloutPolicy, policy_highest
from engine.runtime.interp import Runtime
from engine.runtime.state import Battle
from engine.runtime.turn import Choice

from . import plan as P
from .tools import battle_from_state, evaluate, spec_source

GAME_RULES = """\
## 게임 규칙 (시뮬레이터에 구현된 것)
- 스킬 덱 = S1×3, S2×2, S3×1 을 섞음. 슬롯 2칸이 보이고 매 턴 그중 1장을 씀 → 쓴 칸만 덱 다음 장으로 보충, 안 쓴 칸은 남음.
  덱이 비면 6장 다시 섞음. 덱 순서는 모름 (남은 구성만 앎)
- 수비 스킬(DEF)은 아래 칸 대신 쓰며, 아래 칸 카드는 소모되고 보충됨. 공격형 수비(합 가능 반격)만 허수아비에게 의미 있음
- 턴 순서: 턴 시작 → 속도 → 스킬 선택 → 전투 시작 → 행동 → 턴 종료. 보호막은 턴 종료 시 사라짐
- 표준 키워드: 진동(턴 종료 횟수 -1, 진동 폭발 = 위력만큼 흐트러짐 피해), 출혈·화상·파열·침잠·호흡·충전 (키워드 사전 그대로)
- 상대: 공격하지 않는 허수아비 (체력 HP). 목표 = **처치까지 걸리는 턴 수 최소** (생존은 이번 시나리오에서 영향 없음)
"""

PLAN_FORMAT = P.__doc__


def _system(identity_id: int, spec: str, hp: float, refs: dict) -> str:
    return f"""너는 Limbus Company 인격 운용 전략가다. 시뮬레이터를 도구로 써서 인격 #{identity_id} 의 최적 운용 규칙을 찾는다.
네가 쓰는 계획은 엔진이 실제로 실행·평가한다. 수치(턴 수, 비율)는 반드시 평가 결과에서만 인용하고 추측하지 마라.

{GAME_RULES}
시나리오: 인격 #{identity_id} 단독 vs 체력 {hp:g} 허수아비.

## 기준 성능 (같은 난수, 평균 처치 턴 — 낮을수록 좋음)
{json.dumps(refs, ensure_ascii=False)}
- highest = 두 칸 중 높은 스킬, rollout = 매 턴 4턴 앞을 시뮬레이션해 고르는 탐색 정책 (사람이 따라 하기 어려움, 상한 기준)
목표: rollout 에 최대한 가까우면서 **사람이 읽고 따라 할 수 있는** 짧은 규칙.

## 계획 형식
{PLAN_FORMAT}
## 인격 spec (LPDL v2 — 원문 효과를 기술한 것, text/주석이 원문)
```
{spec}
```
"""


FIRST = """\
spec 을 읽고 이 인격의 핵심 자원·임계치·연계를 먼저 3~6줄로 분석해라 (어떤 상태가 되면 피해가 오르는가, 무엇을 아껴야 하는가).
그다음 서로 다른 가설의 계획 {n}개를 ```json 블록 하나에 배열로 내라. 각 계획에 "hypothesis" 를 한 문장으로 적어라."""

FEEDBACK = """\
평가 결과 ({games}판, 같은 난수):
{results}

결과를 해석해라: 어떤 규칙이 효과가 있었고(rule_fired, skill_share_by_turn, trace 를 근거로), 무엇이 틀렸나.
그다음 개선한 계획 {n}개를 ```json 배열로 내라 (지금까지 최고 계획을 다듬은 것 1개 이상 포함)."""

FINAL = """\
최종 결과:
{results}

최고 계획을 바탕으로 **운용 가이드**를 한국어 마크다운으로 써라.
- 첫 줄: 한 문장 요약
- 우선순위 규칙 (조건 → 행동, 이유) — 계획의 규칙과 일치시켜라
- 성능: 평균 처치 턴을 highest / rollout / 이 계획으로 비교 (위 수치만 인용)
- 주의점: 이 결론이 기대는 가정 (허수아비 상대, 단독)
코드 블록은 쓰지 마라."""


@dataclass
class Candidate:
    plan: P.Plan | None
    result: dict | None = None
    error: str | None = None
    round: int = 0


@dataclass
class GuideRun:
    identity_id: int
    candidates: list[Candidate] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    guide: str = ""

    @property
    def best(self) -> Candidate | None:
        ok = [c for c in self.candidates if c.result]
        return min(ok, key=lambda c: c.result["mean_kill_turn"]) if ok else None


def _brief(c: Candidate) -> dict:
    if c.error:
        return {"name": c.plan.name if c.plan else "?", "error": c.error}
    r = c.result
    return {"name": c.plan.name, "plan": c.plan.to_dict(), "mean_kill_turn": r["mean_kill_turn"],
            "kill_turn_dist": r["kill_turn_dist"], "skill_share": r["skill_share"],
            "skill_share_by_turn": r["skill_share_by_turn"], "rule_fired": r["rule_fired"], "trace": r["trace"]}


def run_guide(rt: Runtime, battle: Battle, identity_id: int, conv_factory, *, hp: float, refs: dict,
              rounds: int = 3, n_plans: int = 3, games: int = 300, log=print) -> GuideRun:
    """conv_factory(system) -> Conversation (lpdl.compiler 백엔드의 start)."""
    run = GuideRun(identity_id)
    conv = conv_factory(_system(identity_id, spec_source(rt, identity_id), hp, refs))
    me = battle.units[0]
    msg = FIRST.format(n=n_plans)
    for rnd in range(1, rounds + 1):
        log(f"[라운드 {rnd}] LLM 계획 요청")
        reply = conv.send(msg)
        run.transcript.append((msg, reply))
        plans, errors = P.parse_plans(reply)
        if not plans and not errors:
            errors = ["```json 계획 배열을 찾지 못함"]
        for e in errors:
            log(f"  형식 오류 — {e}")
        batch = []
        for pl in plans:
            c = Candidate(pl, round=rnd)
            try:
                probe = battle.clone()
                probe.units[0].hand = [("S1", 0), ("S3", 0)]
                P.check_plan(rt, probe, probe.units[0], pl)
                c.result = evaluate(rt, battle, lambda pl=pl: P.PlanPolicy(rt, pl), games=games)
            except P.PlanError as e:
                c.error = str(e)
            batch.append(c)
            log(f"  {pl.name}: " + (c.error or f"평균 처치 턴 {c.result['mean_kill_turn']}"))
        run.candidates += batch
        results = json.dumps([_brief(c) for c in batch] + [{"error": e} for e in errors], ensure_ascii=False)
        if rnd < rounds:
            msg = FEEDBACK.format(games=games, results=results, n=n_plans)
    best = run.best
    final = {"refs": refs, "best": _brief(best) if best else None,
             "all": [{"round": c.round, "name": c.plan.name if c.plan else "?",
                      "mean_kill_turn": c.result["mean_kill_turn"] if c.result else None, "error": c.error}
                     for c in run.candidates]}
    log("[최종] 가이드 작성 요청")
    msg = (f"마지막 라운드 결과:\n{results}\n\n" if rounds else "") + FINAL.format(results=json.dumps(final, ensure_ascii=False))
    run.guide = conv.send(msg)
    run.transcript.append((msg, run.guide))
    return run


def save_guide(run: GuideRun, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "guide.md").write_text(run.guide, encoding="utf-8")
    best = run.best
    if best:
        (out_dir / "best_plan.json").write_text(json.dumps({**best.plan.to_dict(), "result": best.result},
                                                           ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "candidates.json").write_text(json.dumps(
        [{"round": c.round, "plan": c.plan.to_dict() if c.plan else None, "error": c.error,
          "mean_kill_turn": c.result["mean_kill_turn"] if c.result else None} for c in run.candidates],
        ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "transcript.md").write_text("\n\n".join(f"## >>> 요청\n{a}\n\n## <<< 응답\n{b}" for a, b in run.transcript),
                                           encoding="utf-8")


# ── 조언 모드 ────────────────────────────────────────────────────────────────
ADVISE = """\
너는 Limbus Company 인격 운용 조언자다. 아래 시뮬레이션 결과만 근거로, 이번 턴에 무엇을 쓸지 한국어로 3~6줄로 설명해라.
- 추천 행동과 그 이유 (점수 = 앞으로 처치까지 예상 턴 수, 낮을수록 좋음. 차이를 턴 단위로 말해라)
- 추천이 "이번 턴 가장 센 스킬"과 다르면 무엇을 위해 아끼거나 쌓는지 spec 근거로 설명
- 수치는 아래 결과만 인용

{rules}
## 인격 spec
```
{spec}
```

## 현재 상태
{state}

## 후보별 점수 (롤아웃 {rollouts}회 × {horizon}턴, 평균 예상 처치 턴)
{scores}
가장 센 스킬 기준 선택: {greedy}
"""


def advise(rt: Runtime, state: dict, conv_factory=None, *, horizon: int = 4, rollouts: int = 64) -> dict:
    b = battle_from_state(state)
    me = b.units[0]
    pol = RolloutPolicy(rt, horizon=horizon, rollouts=rollouts)
    pick = pol(b, me)

    def label(c: Choice) -> str:
        if c.card == "DEF":
            return "DEF"
        k = me.hand[c.card]
        k = me.skill_override.get(k, k)
        return k[0] + (f" variant {k[1]}" if k[1] else "") + f" (칸 {c.card})"

    scores = {label(c): round(v, 2) for c, v in sorted(pol.last.items(), key=lambda kv: kv[1])}
    out = {"pick": label(pick), "scores": scores, "greedy": label(policy_highest(b, me))}
    if conv_factory is not None:
        conv = conv_factory("You are a concise game strategy advisor. Answer in Korean.")
        out["explanation"] = conv.send(ADVISE.format(
            rules=GAME_RULES, spec=spec_source(rt, state["identity"]), state=json.dumps(state, ensure_ascii=False),
            rollouts=rollouts, horizon=horizon, scores=json.dumps(scores, ensure_ascii=False), greedy=out["greedy"]))
    return out

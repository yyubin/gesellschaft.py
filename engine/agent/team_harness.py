"""팀 가이드 루프 (docs/08 M3): 팀 계획 생성 → 평가 (여러 보스 체력) → 규칙 빼 보기 → 피드백 → 팀 운용 가이드."""
from __future__ import annotations

import json
import re

from engine.runtime.interp import Runtime
from engine.runtime.state import Battle

from . import plan as P
from . import team as T
from .harness import GAME_RULES, Candidate, GuideRun, _board, paired
from .tools import spec_source

TEAM_RULES = """\
## 팀전 규칙 (시뮬레이터에 구현된 것)
- 보스: 속도 주사위 여러 개, 스킬을 순서대로 순환, 주사위마다 살아 있는 아군 중 랜덤 대상. **선택 전에 공개** (incoming 등으로 조건에 씀)
- 합 = 가로채기 포함: 아군이 보스 주사위 E 를 대상으로 하고 내 속도 ≥ E 속도면, E 가 누구를 노렸든 그 아군과 합.
  여럿이면 마지막으로 고른 아군. 아니면 각자 일방 공격. 합 패배 측은 [공격 시작 전] 없음 (#204 심판!도 안 나감)
- 수비 스킬: 방어·회피·반격은 자기 속도에 행동하지 않고 이번 턴 자신에게 처음 들어오는 공격에 반응
  (반격 = 피해를 받은 뒤 일방 공격, 방어 = 보호막, 회피 = 코인마다 비교·한 번 맞으면 이후 계속 맞음). 합 가능 반격은 공격 스킬
- 흐트러짐 피해(진동 폭발 등)는 보스의 다음 흐트러짐 선을 앞당김. 흐트러지면 남은 행동 취소·다음 턴 행동 불가, 물리 피해 ×2 이상
- 점수 = 처치 턴 (패배면 50) + 아군 사망 1명당 10 + 아군 체력 비율 손실 1.0 당 2. **낮을수록 좋음**
"""

FIRST = """\
세 인격의 spec 을 읽고 팀 시너지(누가 누구를 밀어주나, 핵심 자원·임계치, 누가 합을 잡고 누가 반격으로 받을지)를 5~8줄로 분석해라.
그다음 서로 다른 가설의 팀 계획 {n}개를 ```json 배열로 내라. 각 계획에 hypothesis 한 문장."""

FEEDBACK = """\
평가 결과 (보스 체력 시나리오별 {games}판, 같은 난수, 계획끼리 판별로 짝지어 비교):
{results}

## 지금까지 순위
{board}

## 규칙 빼 보기 (이번 상위 계획)
{ablation}
- delta = 그 규칙을 뺀 계획 − 원래 계획 (점수). delta < 0 이면 해로운 규칙. |delta| < 2·se_delta 면 불분명 → 빼라

해석하고 (rule_fired, skill_share, clash_win, dead, trace 근거), 개선한 팀 계획 {n}개를 ```json 배열로 내라.
최고 계획을 다듬은 것 1개 이상 포함, 이미 평가한 계획은 다시 내지 마라."""

FINAL = """\
최종 결과:
{results}

최고 팀 계획을 바탕으로 **팀 운용 가이드**를 한국어 마크다운으로 써라.
- 첫 줄: 한 문장 요약
- 인격별 우선순위 규칙 (조건 → 행동·대상, 이유) — 최고 계획과 정확히 일치. 빼 보기 delta 를 근거로
- 팀 시너지가 실제로 나타난 지점 (수치 근거)
- 성능: 기준(highest) / 팀 탐색 정책 / 이 계획 비교 (위 수치만, se 함께)
- 시도했지만 효과가 없거나 해로웠던 아이디어
- 주의점 (가상 보스, 고정 위력 스킬, 이 세 인격 조합 전제)
코드 블록은 쓰지 마라."""


def _system(ids: list[int], specs: str, boss_desc: str, refs: dict) -> str:
    return f"""너는 Limbus Company 팀 운용 전략가다. 시뮬레이터로 팀 #{', #'.join(map(str, ids))} 의 운용 규칙을 찾는다.
네가 쓰는 팀 계획은 엔진이 실행·평가한다. 수치는 평가 결과에서만 인용하라.

{GAME_RULES}
{TEAM_RULES}
## 보스
{boss_desc}

## 기준 성능 (점수, 낮을수록 좋음)
{json.dumps(refs, ensure_ascii=False)}
- highest = 높은 스킬 + 기본 대상 선택, team_rollout = 매 턴 3턴 앞을 시뮬레이션해 고르는 탐색 정책 (사람이 따라 하기 어려움)

## 계획 형식 (인격 하나)
{P.__doc__}
## 팀 계획 형식
{T.__doc__}
## 인격 spec (LPDL v2)
```
{specs}
```
"""


def parse_team_plans(text: str) -> tuple[list[T.TeamPlan], list[str]]:
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S) or [text]
    plans, errors = [], []
    for b in blocks:
        try:
            d = json.loads(b)
        except json.JSONDecodeError as e:
            errors.append(f"JSON 파싱 실패: {e}")
            continue
        for x in d if isinstance(d, list) else d.get("plans", [d]) if isinstance(d, dict) else []:
            try:
                plans.append(T.TeamPlan.from_dict(x))
            except P.PlanError as e:
                errors.append(f"{x.get('name', '?') if isinstance(x, dict) else '?'}: {e}")
    return plans, errors


def _eval(rt, battles, tp: T.TeamPlan, games: int, cache: dict) -> dict:
    k = json.dumps(tp.to_dict()["units"], ensure_ascii=False, sort_keys=True)
    if k not in cache:
        cache[k] = T.evaluate_team(rt, battles, lambda: T.TeamPlanPolicy(rt, tp), games=games)
    return cache[k]


def _check(rt, battles, tp: T.TeamPlan):
    """조건식 이름 오류 미리 확인: 첫 시나리오에서 한 턴 진행해 의도가 있는 상태로 평가."""
    import random
    from engine.runtime import deal_hand
    from engine.runtime.turn import begin_turn
    b = battles[0].clone()
    rng = random.Random(0)
    for u in b.units:
        if u.side == "ally":
            deal_hand(u, rng)
    begin_turn(rt, b, rng)
    for u in b.units:
        pl = tp.units.get(u.identity_id)
        if pl is not None:
            try:
                P.check_plan(rt, b, u, pl)
            except P.PlanError as e:
                raise P.PlanError(f"units[{u.identity_id}]: {e}") from None


def ablate_team(rt, battles, c: Candidate, games: int, cache: dict) -> list:
    out = []
    for iid, i in c.plan.rules:
        r = c.plan.units[iid].rules[i]
        d, se = paired(_eval(rt, battles, c.plan.without(iid, i), games, cache), c.result)
        out.append({"unit": iid, "rule": i, "when": r.when, "use": r.use, "avoid": r.avoid, "target": r.target,
                    "fired": c.result["rule_fired"].get(f"{iid}.rules[{i}]", 0), "delta": d, "se_delta": se})
    return out


def _brief(c: Candidate, run: GuideRun) -> dict:
    if c.error:
        return {"name": c.plan.name if c.plan else "?", "error": c.error}
    r = c.result
    out = {"name": c.plan.name, "plan": c.plan.to_dict(), "score": r["mean_kill_turn"], "se": r["se"],
           "vs_highest": dict(zip(("diff", "se"), paired(r, run.baseline)))}
    out.update({k: r[k] for k in ("by_scenario", "kill_turn", "win", "dead", "hp_left", "clashes_per_game",
                                  "clash_win", "skill_share", "rule_fired", "trace")})
    return out


def run_team_guide(rt: Runtime, battles: list[Battle], ids: list[int], conv_factory, *, boss_desc: str, refs: dict,
                   rounds: int = 4, n_plans: int = 3, games: int = 100, ablate_top: int = 2, log=print) -> GuideRun:
    run = GuideRun(ids[0])
    cache: dict = {}
    run.baseline = _eval(rt, battles, T.TeamPlan("highest", {}), games, cache)
    specs = "\n\n".join(spec_source(rt, i) for i in ids)
    conv = conv_factory(_system(ids, specs, boss_desc, refs))
    msg = FIRST.format(n=n_plans)
    results = "[]"
    for rnd in range(1, rounds + 1):
        log(f"[라운드 {rnd}] LLM 팀 계획 요청")
        reply = conv.send(msg)
        run.transcript.append((msg, reply))
        plans, errors = parse_team_plans(reply)
        if not plans and not errors:
            errors = ["```json 계획 배열을 찾지 못함"]
        for e in errors:
            log(f"  형식 오류 — {e}")
        batch = []
        for tp in plans:
            c = Candidate(tp, round=rnd)
            try:
                _check(rt, battles, tp)
                c.result = _eval(rt, battles, tp, games, cache)
            except P.PlanError as e:
                c.error = str(e)
            batch.append(c)
            log(f"  {tp.name}: " + (c.error or f"점수 {c.result['mean_kill_turn']} ± {c.result['se']} "
                                     f"(처치 {c.result['kill_turn']}턴, 사망 {c.result['dead']}, 합 승률 {c.result['clash_win']})"))
        run.candidates += batch
        ok = sorted((c for c in batch if c.result), key=lambda c: c.result["mean_kill_turn"])
        for c in ok[:ablate_top]:
            c.ablation = ablate_team(rt, battles, c, games, cache)
            log(f"  빼 보기 {c.plan.name}: " + ", ".join(f"{a['unit']}.r{a['rule']} {a['delta']:+.2f}" for a in c.ablation))
        results = json.dumps([_brief(c, run) for c in batch] + [{"error": e} for e in errors], ensure_ascii=False)
        abl = json.dumps([{"name": c.plan.name, "rules": c.ablation} for c in ok[:ablate_top]], ensure_ascii=False)
        if rnd < rounds:
            msg = FEEDBACK.format(games=games, results=results, board=_board(run), ablation=abl, n=n_plans)
    best = run.best
    if best is not None and best.ablation is None:
        best.ablation = ablate_team(rt, battles, best, games, cache)
    final = {"refs": refs, "best": {**_brief(best, run), "ablation": best.ablation} if best else None,
             "leaderboard": json.loads(_board(run, top=10))}
    log("[최종] 팀 가이드 작성 요청")
    msg = f"마지막 라운드 결과:\n{results}\n\n" + FINAL.format(results=json.dumps(final, ensure_ascii=False))
    run.guide = conv.send(msg)
    run.transcript.append((msg, run.guide))
    return run

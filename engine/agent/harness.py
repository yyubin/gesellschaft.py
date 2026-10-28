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
from .tools import battle_from_state, evaluate_set, paired, spec_source

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


def _system(identity_id: int, spec: str, hps: list, refs: dict) -> str:
    return f"""너는 Limbus Company 인격 운용 전략가다. 시뮬레이터를 도구로 써서 인격 #{identity_id} 의 최적 운용 규칙을 찾는다.
네가 쓰는 계획은 엔진이 실제로 실행·평가한다. 수치(턴 수, 비율)는 반드시 평가 결과에서만 인용하고 추측하지 마라.

{GAME_RULES}
시나리오: 인격 #{identity_id} 단독 vs 허수아비, 체력 {" / ".join(f"{h:g}" for h in hps)} 각각.
**평가값 = 시나리오 평균 처치 턴** (by_scenario 에 각 값). 특정 체력·턴 수에만 맞는 규칙(예: turn >= 7)은
다른 체력에서 손해를 보므로 피하고, 자원·상태 조건으로 써라. 분포·스킬 비율·trace 는 detail_scenario 기준.

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
평가 결과 ({games}판, 같은 난수 — 계획끼리 판별로 짝지어 비교):
{results}

## 지금까지 순위 (전체 라운드)
{board}

## 규칙 빼 보기 (이번 라운드 상위 계획)
{ablation}
- delta = 그 규칙을 뺀 계획 − 원래 계획 (평균 처치 턴). **delta < 0 이면 그 규칙이 해롭다** (빼면 빨라짐).
  |delta| 가 se_delta 의 2배보다 작으면 효과가 불분명 → 빼는 쪽이 낫다 (규칙은 적을수록 좋음)
- vs_highest / vs_best 도 같은 방식의 짝지은 차이 (음수 = 더 빠름)

결과를 해석해라: 어떤 규칙이 효과가 있었고 어떤 규칙이 해로웠나 (규칙 빼 보기, rule_fired, skill_share_by_turn, trace 근거).
그다음 개선한 계획 {n}개를 ```json 배열로 내라.
- 해롭거나 불분명한 규칙은 빼라. 지금까지 최고 계획을 다듬은 것 1개 이상 포함
- 이미 평가한 것과 같은 계획은 다시 내지 마라"""

FINAL = """\
최종 결과:
{results}

최고 계획을 바탕으로 **운용 가이드**를 한국어 마크다운으로 써라.
- 첫 줄: 한 문장 요약
- 우선순위 규칙 (조건 → 행동, 이유) — 최고 계획의 규칙과 정확히 일치시켜라. 규칙 빼 보기에서 확인된 효과(delta)를 근거로
- 성능: 평균 처치 턴을 highest / rollout / 이 계획으로 비교 (위 수치만 인용, se 를 함께)
- 시도했지만 효과가 없거나 해로웠던 아이디어 (있으면)
- 주의점: 이 결론이 기대는 가정 (허수아비 상대, 단독)
코드 블록은 쓰지 마라."""


@dataclass
class Candidate:
    plan: P.Plan | None
    result: dict | None = None
    error: str | None = None
    round: int = 0
    ablation: list | None = None


def _key(plan) -> str:
    d = plan.to_dict()
    if "units" in d:                                   # 팀 계획
        return json.dumps(d["units"], ensure_ascii=False, sort_keys=True)
    return json.dumps({"rules": d["rules"], "default": d["default"], "target": d.get("default_target")},
                      ensure_ascii=False, sort_keys=True)


@dataclass
class GuideRun:
    identity_id: int
    candidates: list[Candidate] = field(default_factory=list)
    transcript: list[tuple[str, str]] = field(default_factory=list)
    guide: str = ""
    baseline: dict | None = None         # 규칙 없는 계획 (= highest) 평가

    def ranked(self) -> list[Candidate]:
        seen, out = set(), []
        for c in sorted((c for c in self.candidates if c.result), key=lambda c: c.result["mean_kill_turn"]):
            if _key(c.plan) not in seen:
                seen.add(_key(c.plan))
                out.append(c)
        return out

    @property
    def best(self) -> Candidate | None:
        """평균이 가장 낮은 계획과 짝지은 차이가 오차(2 se) 안인 계획 중 규칙이 가장 적은 것."""
        r = self.ranked()
        if not r:
            return None
        top = r[0]

        def close(c):
            d, se = paired(c.result, top.result)
            return c is top or d <= 2 * se
        return min((c for c in r if close(c)), key=lambda c: (len(c.plan.rules), c.result["mean_kill_turn"]))


def _brief(c: Candidate, run: GuideRun | None = None) -> dict:
    if c.error:
        return {"name": c.plan.name if c.plan else "?", "error": c.error}
    r = c.result
    out = {"name": c.plan.name, "plan": c.plan.to_dict(), "mean_kill_turn": r["mean_kill_turn"], "se": r["se"]}
    if "by_scenario" in r:
        out["by_scenario"] = r["by_scenario"]
    if run is not None and run.baseline is not None:
        out["vs_highest"] = dict(zip(("diff", "se"), paired(r, run.baseline)))
    out.update({k: r[k] for k in ("kill_turn_dist", "skill_share", "skill_share_by_turn", "rule_fired", "trace")})
    return out


def _board(run: GuideRun, top: int = 6) -> str:
    r = run.ranked()
    rows = []
    for c in r[:top]:
        d = paired(c.result, r[0].result)
        rows.append({"round": c.round, "name": c.plan.name, "rules": len(c.plan.rules),
                     "mean_kill_turn": c.result["mean_kill_turn"], "vs_best": {"diff": d[0], "se": d[1]}})
    return json.dumps(rows, ensure_ascii=False)


def _eval_cached(rt, battles: list, plan: P.Plan, games: int, cache: dict) -> dict:
    k = _key(plan)
    if k not in cache:
        cache[k] = evaluate_set(rt, battles, lambda: P.PlanPolicy(rt, plan), games=games)
    return cache[k]


def ablate(rt: Runtime, battle: Battle, c: Candidate, games: int, cache: dict) -> list:
    """규칙을 하나씩 뺀 계획을 평가 → 그 규칙의 기여 (delta = 뺀 것 − 원래, 음수면 해로운 규칙)."""
    out = []
    for i, r in enumerate(c.plan.rules):
        sub = P.Plan(f"{c.plan.name} - rules[{i}]", [x for j, x in enumerate(c.plan.rules) if j != i], c.plan.default)
        d, se = paired(_eval_cached(rt, battle, sub, games, cache), c.result)
        out.append({"rule": i, "when": r.when, "use": r.use, "avoid": r.avoid,
                    "fired": c.result["rule_fired"].get(f"rules[{i}]", 0), "delta": d, "se_delta": se})
    return out


def run_guide(rt: Runtime, battle: Battle | list[Battle], identity_id: int, conv_factory, *, refs: dict,
              rounds: int = 4, n_plans: int = 3, games: int = 300, ablate_top: int = 2, log=print) -> GuideRun:
    """conv_factory(system) -> Conversation (lpdl.compiler 백엔드의 start)."""
    run = GuideRun(identity_id)
    cache: dict = {}
    battles = battle if isinstance(battle, list) else [battle]
    battle = battles  # 아래 평가 호출은 시나리오 목록을 받음
    hps = [next(u for u in b.units if u.side == "enemy").max_hp for b in battles]
    run.baseline = _eval_cached(rt, battles, P.Plan("highest", [], "highest"), games, cache)
    conv = conv_factory(_system(identity_id, spec_source(rt, identity_id), hps, refs))
    msg = FIRST.format(n=n_plans)
    results = "[]"
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
                probe = battles[0].clone()
                probe.units[0].hand = [("S1", 0), ("S3", 0)]
                P.check_plan(rt, probe, probe.units[0], pl)
                dup = _key(pl) in cache
                c.result = _eval_cached(rt, battle, pl, games, cache)
            except P.PlanError as e:
                c.error = str(e)
            batch.append(c)
            log(f"  {pl.name}: " + (c.error or f"평균 처치 턴 {c.result['mean_kill_turn']} ± {c.result['se']}"
                                     + (" (이미 평가한 계획)" if dup else "")))
        run.candidates += batch
        ok = sorted((c for c in batch if c.result), key=lambda c: c.result["mean_kill_turn"])
        for c in ok[:ablate_top]:
            c.ablation = ablate(rt, battle, c, games, cache)
            log(f"  빼 보기 {c.plan.name}: " + ", ".join(f"r{a['rule']} {a['delta']:+.2f}" for a in c.ablation))
        results = json.dumps([_brief(c, run) for c in batch] + [{"error": e} for e in errors], ensure_ascii=False)
        abl = json.dumps([{"name": c.plan.name, "rules": c.ablation} for c in ok[:ablate_top]], ensure_ascii=False)
        if rnd < rounds:
            msg = FEEDBACK.format(games=games, results=results, board=_board(run), ablation=abl, n=n_plans)
    best = run.best
    if best is not None and best.ablation is None:
        best.ablation = ablate(rt, battle, best, games, cache)
    final = {"refs": refs, "best": {**_brief(best, run), "ablation": best.ablation} if best else None,
             "leaderboard": json.loads(_board(run, top=10)),
             "errors": [{"round": c.round, "name": c.plan.name, "error": c.error} for c in run.candidates if c.error]}
    log("[최종] 가이드 작성 요청")
    msg = f"마지막 라운드 결과:\n{results}\n\n" + FINAL.format(results=json.dumps(final, ensure_ascii=False))
    run.guide = conv.send(msg)
    run.transcript.append((msg, run.guide))
    return run


def save_guide(run: GuideRun, out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "guide.md").write_text(run.guide, encoding="utf-8")
    best = run.best
    if best:
        res = {k: v for k, v in best.result.items() if k != "turns"}
        (out_dir / "best_plan.json").write_text(json.dumps({**best.plan.to_dict(), "result": res, "ablation": best.ablation},
                                                           ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "candidates.json").write_text(json.dumps(
        [{"round": c.round, "plan": c.plan.to_dict() if c.plan else None, "error": c.error,
          "mean_kill_turn": c.result["mean_kill_turn"] if c.result else None,
          "se": c.result["se"] if c.result else None, "ablation": c.ablation} for c in run.candidates],
        ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "transcript.md").write_text("\n\n".join(f"## >>> 요청\n{a}\n\n## <<< 응답\n{b}" for a, b in run.transcript),
                                           encoding="utf-8")


# ── 계획 개선 루프 (탐색이 계획과 다르게 고른 장면 → LLM 이 규칙화) ─────────────────
REFINE_FIRST = """\
현재 최고 계획과 평가 결과:
{current}

이 계획을 기준 정책으로 쓰는 탐색(롤아웃, 처치까지 끝까지 시뮬레이션)이 판을 진행하면서 **계획과 다르게 고른 장면**을 모았다.
탐색 정책 성능 (같은 장면 수집 판): {rollout}
{deviations}
- gain = 탐색이 추정한 이득 (계획의 선택 − 탐색의 선택, 처치 턴). 탐색 추정이라 노이즈가 있다 — 여러 장면에서 반복되는 패턴만 믿어라
- patterns = (계획 → 탐색) 카드 쌍별 횟수·평균 이득, samples = 이득이 큰 장면의 상태

장면들의 공통 조건을 찾아 규칙으로 만들어라 (예: "S3 를 쓰려던 장면 중 target.진동.count 가 낮을 때 탐색은 S2").
1. 패턴 분석 3~6줄
2. 현재 계획에 규칙을 추가·수정한 계획 {n}개를 ```json 배열로. 각각 hypothesis 에 어떤 패턴을 반영했는지 적어라"""

REFINE_NEXT = """\
평가 결과 ({games}판 × 시나리오):
{results}

## 지금까지 순위
{board}

## 규칙 빼 보기 (이번 상위 계획)
{ablation}

## 새 최고 계획 기준으로 다시 모은 탐색과의 차이
탐색 정책 성능: {rollout}
{deviations}

해석하고, 개선한 계획 {n}개를 ```json 배열로 내라. 해롭거나 불분명한 규칙은 빼라. 이미 평가한 계획은 다시 내지 마라."""


def _dev_text(summary: dict) -> str:
    return json.dumps({k: summary[k] for k in ("decisions", "deviations", "deviation_turns", "patterns", "samples")},
                      ensure_ascii=False)


def run_refine(rt: Runtime, battles: list[Battle], identity_id: int, conv_factory, start: P.Plan, *, refs: dict,
               iterations: int = 3, n_plans: int = 3, games: int = 300, dev_games: int = 10, rollouts: int = 32,
               ablate_top: int = 2, log=print) -> GuideRun:
    from .distill import collect, summarize

    run = GuideRun(identity_id)
    cache: dict = {}
    hps = [next(u for u in b.units if u.side == "enemy").max_hp for b in battles]
    run.baseline = _eval_cached(rt, battles, P.Plan("highest", [], "highest"), games, cache)
    seed_c = Candidate(start, _eval_cached(rt, battles, start, games, cache), round=0)
    seed_c.ablation = ablate(rt, battles, seed_c, games, cache)
    run.candidates.append(seed_c)
    log(f"[시작] {start.name}: {seed_c.result['mean_kill_turn']} {seed_c.result.get('by_scenario', '')}")
    conv = conv_factory(_system(identity_id, spec_source(rt, identity_id), hps, refs))
    results, abl = "[]", "[]"
    run.rollout_refs = []
    for it in range(1, iterations + 1):
        best = run.best
        log(f"[반복 {it}] 탐색 차이 수집 — 기준 계획 {best.plan.name}")
        # 반복마다 새 시드 (최고 계획이 그대로여도 다른 장면을 모음)
        summ = summarize(collect(rt, battles, best.plan, games=dev_games, rollouts=rollouts, seed=1000 * it * 10))
        run.rollout_refs.append({"policy": "rollout (처치까지 끝까지, 기준 정책 = 이 계획)", "plan": best.plan.name,
                                 "plan_turns": best.result.get("by_scenario"),
                                 "rollout_turns": summ["rollout_kill_turn"]})
        log(f"  결정 {summ['decisions']}, 차이 {summ['deviations']}, 탐색 처치 턴 {summ['rollout_kill_turn']}")
        if it == 1:
            cur = {**_brief(best, run), "ablation": best.ablation}
            msg = REFINE_FIRST.format(current=json.dumps(cur, ensure_ascii=False), rollout=summ["rollout_kill_turn"],
                                      deviations=_dev_text(summ), n=n_plans)
        else:
            msg = REFINE_NEXT.format(games=games, results=results, board=_board(run), ablation=abl,
                                     rollout=summ["rollout_kill_turn"], deviations=_dev_text(summ), n=n_plans)
        reply = conv.send(msg)
        run.transcript.append((msg, reply))
        plans, errors = P.parse_plans(reply)
        for e in errors:
            log(f"  형식 오류 — {e}")
        batch = []
        for pl in plans:
            c = Candidate(pl, round=it)
            try:
                probe = battles[0].clone()
                probe.units[0].hand = [("S1", 0), ("S3", 0)]
                P.check_plan(rt, probe, probe.units[0], pl)
                c.result = _eval_cached(rt, battles, pl, games, cache)
            except P.PlanError as e:
                c.error = str(e)
            batch.append(c)
            log(f"  {pl.name}: " + (c.error or f"{c.result['mean_kill_turn']} ± {c.result['se']} "
                                     f"{c.result.get('by_scenario', '')}"))
        run.candidates += batch
        ok = sorted((c for c in batch if c.result), key=lambda c: c.result["mean_kill_turn"])
        for c in ok[:ablate_top]:
            c.ablation = ablate(rt, battles, c, games, cache)
            log(f"  빼 보기 {c.plan.name}: " + ", ".join(f"r{a['rule']} {a['delta']:+.2f}" for a in c.ablation))
        results = json.dumps([_brief(c, run) for c in batch] + [{"error": e} for e in errors], ensure_ascii=False)
        abl = json.dumps([{"name": c.plan.name, "rules": c.ablation} for c in ok[:ablate_top]], ensure_ascii=False)
    best = run.best
    if best.ablation is None:
        best.ablation = ablate(rt, battles, best, games, cache)
    final = {"refs": refs, "start": {"name": start.name, "mean_kill_turn": seed_c.result["mean_kill_turn"]},
             "best": {**_brief(best, run), "ablation": best.ablation},
             "rollout_vs_plan": run.rollout_refs, "leaderboard": json.loads(_board(run, top=10))}
    log("[최종] 가이드 작성 요청")
    msg = f"마지막 결과:\n{results}\n\n" + FINAL.format(results=json.dumps(final, ensure_ascii=False)) + \
        "\n- 탐색(rollout_vs_plan)과 최고 계획의 남은 차이도 적어라 (규칙으로 못 잡은 부분)"
    run.guide = conv.send(msg)
    run.transcript.append((msg, run.guide))
    return run


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

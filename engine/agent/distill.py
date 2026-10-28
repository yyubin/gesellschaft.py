"""계획 개선 루프용: 계획을 기준 정책으로 한 롤아웃(끝까지)이 계획과 다르게 고른 장면 수집 (docs/08 §9).

판은 롤아웃의 선택으로 진행한다 (개선된 정책이 실제로 가는 상태에서 차이를 모음).
장면마다: 상태 요약, 계획의 선택, 롤아웃의 선택, 이득 = 롤아웃 추정 처치 턴 차이 (계획 선택 − 롤아웃 선택).
"""
from __future__ import annotations

import random
from collections import Counter, defaultdict

from engine.runtime import Battle, RolloutPolicy, deal_hand, run_turn, stage_enter
from engine.runtime.interp import Runtime
from engine.runtime.state import Unit
from engine.runtime.turn import Choice, battle_over

from . import plan as P


def _card(u: Unit, c: Choice) -> str:
    if c.card == "DEF":
        k = u.skill_override.get(("DEF", 0), ("DEF", 0))
    else:
        k = u.hand[c.card]
        k = u.skill_override.get(k, k)
    return k[0] + (f"v{k[1]}" if k[1] else "")


def snapshot(b: Battle, u: Unit) -> dict:
    foe = next((e for e in b.enemies_of(u) if e.hp > 0), None)
    r = lambda x: round(x, 2)
    out = {"turn": b.turn, "hand": [u.hand[i][0] for i in range(len(u.hand))],
           "self": {**{f"{k}.potency": r(s.potency) for k, s in u.statuses.items()},
                    **{f"{k}.count": r(s.count) for k, s in u.statuses.items()},
                    **{k: r(v) for k, v in u.values.items()}, "sanity": r(u.sanity)}}
    if foe is not None:
        out["target"] = {"hp_ratio": r(foe.hp_ratio),
                         **{f"{k}.potency": r(s.potency) for k, s in foe.statuses.items()},
                         **{f"{k}.count": r(s.count) for k, s in foe.statuses.items()},
                         **{k: r(v) for k, v in foe.values.items()}}
    return out


def collect(rt: Runtime, battles: list[Battle], plan: P.Plan, *, games: int = 10, rollouts: int = 32,
            seed: int = 1000, max_turns: int = 20) -> dict:
    """시나리오마다 games 판. seed 는 평가용 seed(0~)와 겹치지 않게 1000 부터."""
    decisions, deviations, kills = 0, [], []
    for bi, battle in enumerate(battles):
        hp = next(u for u in battle.units if u.side == "enemy").max_hp
        for g in range(games):
            s = seed + bi * 1000 + g
            rng = random.Random(s)
            b = battle.clone()
            for u in b.units:
                if u.side == "ally":
                    deal_hand(u, rng)
            stage_enter(rt, b)
            plan_pol = P.PlanPolicy(rt, plan)
            roll = RolloutPolicy(rt, horizon=None, rollouts=rollouts, base=P.PlanPolicy(rt, plan), seed=s)

            def policy(bt, u):
                nonlocal decisions
                cp = plan_pol(bt, u)
                cr = roll(bt, u)
                if roll.last:
                    decisions += 1
                    if _card(u, cp) != _card(u, cr) and cp in roll.last:
                        deviations.append({"hp": hp, "state": snapshot(bt, u), "plan": _card(u, cp),
                                           "rollout": _card(u, cr), "gain": round(roll.last[cp] - roll.last[cr], 3)})
                return cr

            t = max_turns + 1
            for turn in range(1, max_turns + 1):
                run_turn(rt, b, rng, policy)
                if battle_over(b):
                    t = turn
                    break
            kills.append((hp, t))
    by_hp = defaultdict(list)
    for hp, t in kills:
        by_hp[hp].append(t)
    return {"decisions": decisions, "deviations": deviations,
            "rollout_kill_turn": {f"hp {h:g}": round(sum(v) / len(v), 3) for h, v in sorted(by_hp.items())}}


def summarize(data: dict, samples: int = 15, min_gain: float = 0.05) -> dict:
    """(계획 → 롤아웃) 패턴별 횟수·평균 이득 + 이득 큰 장면 샘플."""
    devs = [d for d in data["deviations"] if d["gain"] >= min_gain]
    pat = defaultdict(list)
    for d in devs:
        pat[(d["plan"], d["rollout"])].append(d["gain"])
    patterns = sorted(({"plan": p, "rollout": r, "count": len(g), "mean_gain": round(sum(g) / len(g), 3)}
                       for (p, r), g in pat.items()), key=lambda x: -x["count"] * x["mean_gain"])
    top = sorted(devs, key=lambda d: -d["gain"])[:samples]
    turns = Counter(d["state"]["turn"] for d in devs)
    return {"decisions": data["decisions"], "deviations": len(devs),
            "rollout_kill_turn": data["rollout_kill_turn"], "patterns": patterns,
            "deviation_turns": dict(sorted(turns.items())), "samples": top}

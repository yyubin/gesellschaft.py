"""팀 계획·평가 (docs/08 M3 하네스).

팀 계획 JSON
    {"name": "...", "hypothesis": "...",
     "units": {"202": {<계획: rules / default / default_target>}, "203": {...}, "204": {...}}}
units 키 = 인격 번호. 없는 인격은 기본 계획 (highest + auto).

점수 = 판마다 (처치 턴, 패배면 cap) + 사망 1명당 10 + 아군 체력 비율 손실 1.0 당 2  — 낮을수록 좋음 (Objective 와 같은 가중치)
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field

from engine.runtime import Battle, deal_hand, run_turn, stage_enter
from engine.runtime.interp import Runtime
from engine.runtime.policy import Objective
from engine.runtime.state import Unit
from engine.runtime.turn import battle_over

from . import plan as P

DEFAULT_PLAN = {"name": "highest", "rules": []}


@dataclass
class TeamPlan:
    name: str
    units: dict[int, P.Plan]
    hypothesis: str = ""

    @staticmethod
    def from_dict(d: dict) -> "TeamPlan":
        if not isinstance(d, dict) or not isinstance(d.get("units"), dict):
            raise P.PlanError('팀 계획은 {"name", "units": {"<인격 번호>": <계획>}} 객체여야 함')
        units = {}
        for k, v in d["units"].items():
            try:
                iid = int(k)
            except ValueError:
                raise P.PlanError(f"units 키는 인격 번호 (받은 값 {k!r})") from None
            try:
                units[iid] = P.Plan.from_dict({"name": str(iid), **v})
            except P.PlanError as e:
                raise P.PlanError(f"units[{k}]: {e}") from None
        return TeamPlan(d.get("name", "team plan"), units, d.get("hypothesis", ""))

    def to_dict(self) -> dict:
        out = {"name": self.name, "hypothesis": self.hypothesis, "units": {}}
        for iid, pl in self.units.items():
            d = pl.to_dict()
            d.pop("name", None)
            d.pop("hypothesis", None)
            out["units"][str(iid)] = d
        return out

    @property
    def rules(self) -> list:
        """(인격, 규칙 번호) 목록 — 빼 보기·규칙 수 집계용."""
        return [(iid, i) for iid, pl in self.units.items() for i in range(len(pl.rules))]

    def without(self, iid: int, i: int) -> "TeamPlan":
        pl = self.units[iid]
        sub = P.Plan(pl.name, [r for j, r in enumerate(pl.rules) if j != i], pl.default, pl.hypothesis, pl.default_target)
        return TeamPlan(f"{self.name} - {iid}.rules[{i}]", {**self.units, iid: sub}, self.hypothesis)


@dataclass
class TeamPlanPolicy:
    """run_turn 의 joint 정책. 유닛마다 자기 인격 계획으로 카드·대상."""
    rt: Runtime
    plan: TeamPlan
    _pols: dict = field(default_factory=dict)

    def _pol(self, u: Unit) -> P.PlanPolicy:
        if u.identity_id not in self._pols:
            pl = self.plan.units.get(u.identity_id) or P.Plan.from_dict(DEFAULT_PLAN)
            self._pols[u.identity_id] = P.PlanPolicy(self.rt, pl)
        return self._pols[u.identity_id]

    def joint(self, battle: Battle, actors: list[Unit]) -> dict:
        return {u.name: self._pol(u)(battle, u) for u in actors}

    def __call__(self, battle: Battle, u: Unit):
        return self._pol(u)(battle, u)

    @property
    def fired(self) -> dict:
        return {iid: dict(p.fired) for iid, p in self._pols.items()}


def play_team(rt: Runtime, battle: Battle, policy, seed: int, max_turns: int = 30, objective: Objective | None = None):
    obj = objective or Objective()
    rng = random.Random(seed)
    b = battle.clone()
    for u in b.units:
        if u.side == "ally":
            deal_hand(u, rng)
    stage_enter(rt, b)
    start = {u.name: u.hp_ratio for u in b.units}
    usage, clashes, wins = Counter(), 0, 0
    trace = []
    t = max_turns
    for t in range(1, max_turns + 1):
        log = run_turn(rt, b, rng, policy)
        for a in log.actions:
            u = next(x for x in b.units if x.name == a.unit)
            if u.side == "ally":
                usage[(u.identity_id, a.skill[0] + (f"v{a.skill[1]}" if a.skill[1] else ""), a.kind)] += 1
                if a.kind == "clash":
                    clashes += 1
                    wins += a.clash["winner"] == a.unit
        for e in b.log:
            if e[0] == "use_skill":
                usage[(next(x.identity_id for x in b.units if x.name == e[1]), "use_skill", "extra")] += 1
        b.log.clear()
        trace.append({"boss_hp": round(sum(max(0, u.hp) for u in b.units if u.side == "enemy")),
                      "ally_hp": round(sum(max(0, u.hp) for u in b.units if u.side == "ally"))})
        if battle_over(b):
            break
    won = all(u.hp <= 0 for u in b.units if u.side == "enemy")
    allies = [u for u in b.units if u.side == "ally"]
    surv = obj.survival(b, "ally", start)
    return {"score": (t if won else obj.cap) + surv, "turns": t, "won": won,
            "dead": sum(u.hp <= 0 for u in allies), "hp": sum(max(0, u.hp) / u.max_hp for u in allies) / len(allies),
            "clashes": clashes, "clash_wins": wins, "usage": usage, "trace": trace}


def _se(xs):
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1) / n) ** 0.5


def evaluate_team(rt: Runtime, battles: list[Battle], make_policy, *, games: int = 200, seed: int = 0) -> dict:
    """시나리오마다 games 판. mean_kill_turn = 점수 평균 (하네스 호환 이름), turns = 판별 점수 (paired 비교용)."""
    scores, by, fired = [], {}, {}
    usage, clashes, wins, n_all = Counter(), 0, 0, 0
    agg = {"turns": 0.0, "won": 0.0, "dead": 0.0, "hp": 0.0}
    traces: list[list] = []
    for b in battles:
        label = next(u.name for u in b.units if u.side == "enemy") + f" hp {next(u.max_hp for u in b.units if u.side == 'enemy'):g}"
        sc = []
        for g in range(games):
            pol = make_policy()
            r = play_team(rt, b, pol, seed + g)
            sc.append(r["score"])
            for k in agg:
                agg[k] += r[k]
            usage.update(r["usage"])
            clashes += r["clashes"]
            wins += r["clash_wins"]
            n_all += 1
            traces.append(r["trace"])
            for iid, f in getattr(pol, "fired", {}).items():
                for k, v in f.items():
                    key = f"{iid}." + ("default" if k == -1 else f"rules[{k}]")
                    fired[key] = fired.get(key, 0) + v
        by[label] = round(sum(sc) / len(sc), 3)
        scores += sc
    tot = Counter()
    for (iid, _, _), v in usage.items():
        tot[iid] += v
    share = {}
    for (iid, sk, kind), v in sorted(usage.items()):
        share.setdefault(str(iid), {})[f"{sk}/{kind}"] = round(v / tot[iid], 3)
    trace = [{"turn": t + 1,
              "boss_hp": round(sum(tr[t]["boss_hp"] for tr in traces if len(tr) > t) / max(1, sum(len(tr) > t for tr in traces))),
              "ally_hp": round(sum(tr[t]["ally_hp"] for tr in traces if len(tr) > t) / max(1, sum(len(tr) > t for tr in traces)))}
             for t in range(8)]
    return {"games": n_all, "mean_kill_turn": round(sum(scores) / len(scores), 3), "se": round(_se(scores), 3),
            "turns": scores, "by_scenario": by,
            "kill_turn": round(agg["turns"] / n_all, 2), "win": round(agg["won"] / n_all, 3),
            "dead": round(agg["dead"] / n_all, 3), "hp_left": round(agg["hp"] / n_all, 3),
            "clashes_per_game": round(clashes / n_all, 2), "clash_win": round(wins / max(1, clashes), 3),
            "skill_share": share, "rule_fired": fired, "trace": trace}

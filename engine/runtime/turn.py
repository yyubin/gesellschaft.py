"""M2: 턴 진행 — 덱·손패, 속도, 속도순 행동, 흐트러짐 회복 (docs/07 §8).

확인된 규칙 (사용자 2026-10-28)
- 덱 = S1×3, S2×2, S3×1 을 섞음. 슬롯 2칸에 보이고 턴마다 그중 1장 사용 → 쓴 칸만 덱 다음 장으로 보충
- 덱이 비면 6장을 다시 섞음
- 속도: 턴 시작 시 speed_min~speed_max 정수 균등 랜덤, 인격당 1개
- 흐트러짐: 흐트러진 턴의 남은 행동 취소, 다음 턴 행동 불가, 그 턴 종료 시 회복
"""
from __future__ import annotations

import random
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

from engine.params import DEFAULT, Params

from .action import combat_end, combat_start, run_action, stage_enter, turn_end, turn_start
from .interp import Runtime, new_mods
from .state import Battle, Unit

DECK = (("S1", 3), ("S2", 2), ("S3", 1))
DEF = ("DEF", 0)


@dataclass
class Choice:
    card: int | str            # 손패 칸 번호, 또는 "DEF" (아래 칸 대신 수비 스킬)
    target: str


Policy = Callable[[Battle, Unit], Choice]


@dataclass
class ActionLog:
    unit: str
    skill: tuple               # 실제 사용 스킬 (교체 반영)
    target: str
    speed: int
    damage: float


@dataclass
class TurnLog:
    turn: int
    actions: list = field(default_factory=list)
    cancelled: list = field(default_factory=list)     # 흐트러짐으로 취소된 유닛


# ── 덱·손패 ────────────────────────────────────────────────────────────────
def _shuffled(rng: random.Random) -> list:
    cards = [(slot, 0) for slot, n in DECK for _ in range(n)]
    rng.shuffle(cards)
    return cards


def _draw(u: Unit, rng: random.Random) -> tuple:
    if not u.deck:
        u.deck = _shuffled(rng)
    return u.deck.pop()


def deal_hand(u: Unit, rng: random.Random):
    u.deck = _shuffled(rng)
    u.hand = [_draw(u, rng), _draw(u, rng)]


def play(u: Unit, card: int | str, rng: random.Random) -> tuple:
    """카드 사용 → 그 칸만 다음 장으로 보충. 수비 스킬은 아래 칸(1) 대신 생성되므로 아래 칸을 쓴다 [확인 필요]."""
    if card == "DEF":
        u.hand[1] = _draw(u, rng)
        return DEF
    key = u.hand[card]
    u.hand[card] = _draw(u, rng)
    return key


# ── 정책 (M2: 고정 규칙) ──────────────────────────────────────────────────────
def first_enemy(battle: Battle, u: Unit) -> str:
    return next(e.name for e in battle.enemies_of(u) if e.hp > 0)


def policy_highest(battle: Battle, u: Unit) -> Choice:
    """두 칸 중 높은 스킬 (S3 > S2 > S1, 같으면 위 칸)."""
    i = max(range(len(u.hand)), key=lambda j: (u.hand[j][0], -j))
    return Choice(i, first_enemy(battle, u))


def policy_top(battle: Battle, u: Unit) -> Choice:
    return Choice(0, first_enemy(battle, u))


# ── 턴 ────────────────────────────────────────────────────────────────────
def _can_act(battle: Battle, u: Unit) -> bool:
    return u.hand is not None and u.hp > 0 and u.stagger_until is None


def run_turn(rt: Runtime, battle: Battle, rng: random.Random, policy: Policy = policy_highest,
             params: Params = DEFAULT) -> TurnLog:
    """턴 시작 → 속도 → 스킬 선택 → 전투 시작 → 속도순 행동 → 전투 종료 → 턴 종료.
    M2: 모든 행동은 일방 공격 (합 짝짓기는 M3)."""
    log = TurnLog(battle.turn)
    turn_start(rt, battle)
    by = {u.name: u for u in battle.units}
    actors = [u for u in battle.units if _can_act(battle, u)]
    for u in actors:                                            # 상시 효과의 speed_min/max (아르뮈르 +2 등) 반영
        m = new_mods()
        rt.run_static(battle, u, m)
        lo, hi = u.speed_min + int(m["speed_min"]), u.speed_max + int(m["speed_max"])
        u.speed = rng.randint(lo, max(lo, hi))
    picks = {}
    for u in actors:
        c = policy(battle, u)
        picks[u.name] = (play(u, c.card, rng), c.target)
    battle.resonance = dict(Counter(u.skills[u.skill_override.get(k, k)].sin for u in actors
                                    for k in [picks[u.name][0]] if u.skills[u.skill_override.get(k, k)].sin))
    combat_start(rt, battle, {name: [key] for name, (key, _) in picks.items()})

    toss = lambda p: rng.random() < p
    for u in sorted(actors, key=lambda x: -x.speed):           # 안정 정렬: 같은 속도는 입력 순서
        key, tname = picks[u.name]
        target = by[tname]
        if u.hp <= 0 or u.stagger_until is not None:
            log.cancelled.append(u.name)
            continue
        if target.hp <= 0:
            continue
        r = run_action(rt, battle, u, key, target, toss, params)
        battle.used_sins.append(u.skills[r.skill].sin)
        log.actions.append(ActionLog(u.name, r.skill, tname, u.speed, r.damage))

    combat_end(rt, battle)
    for u in battle.units:
        if u.stagger_until == battle.turn:
            u.stagger, u.stagger_until = 0, None
    # E.G.O 자원: 이번 턴 사용분은 다음 턴부터 보유 조건에 반영 (docs/rules 1장)
    for s in battle.used_sins:
        if s:
            battle.sin_owned[s] = battle.sin_owned.get(s, 0) + 1
    battle.used_sins.clear()
    turn_end(rt, battle)
    return log


def simulate_turns(rt: Runtime, battle: Battle, turns: int, *, runs: int = 1000, seed: int = 0,
                   policy: Policy = policy_highest, observe: Callable[[Battle], dict] | None = None,
                   params: Params = DEFAULT) -> list[list[dict]]:
    """몬테카를로: run 마다 battle 복제 → 전장 등장 → 덱 섞기 → turns 턴.
    반환 [run][turn] = {"log": TurnLog, **observe(battle)} (observe 는 턴 종료 후 상태)."""
    out = []
    for r in range(runs):
        rng = random.Random(seed + r)
        b = battle.clone()
        for u in b.units:
            if u.hand is not None or u.side == "ally":
                deal_hand(u, rng)
        stage_enter(rt, b)
        rows = []
        for _ in range(turns):
            log = run_turn(rt, b, rng, policy, params)
            rows.append({"log": log, **(observe(b) if observe else {})})
        out.append(rows)
    return out


def mean_by_turn(runs: list[list[dict]], f: Callable[[dict], float]) -> list[float]:
    n = len(runs)
    return [sum(f(run[t]) for run in runs) / n for t in range(len(runs[0]))]

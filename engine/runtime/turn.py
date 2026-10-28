"""M2/M3: 턴 진행 — 덱·손패, 속도, 적 의도, 합 짝짓기, 속도순 행동, 흐트러짐 회복 (docs/07 §8-9).

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

from .action import combat_end, combat_start, defense_power, run_action, run_clash, stage_enter, turn_end, turn_start
from .interp import Runtime, new_mods
from .state import Battle, Unit

DECK = (("S1", 3), ("S2", 2), ("S3", 1))
DEF = ("DEF", 0)
PASSIVE_DEF = {"방어", "회피", "반격"}       # 상대 공격에 반응하는 수비 — 공격하지 않는 적에게는 행동 없음


def attacks(rt: Runtime, u: Unit, key: tuple) -> bool:
    """이 스킬이 스스로 공격하는가. 수비 스킬은 합 가능 반격(flag clashable_counter)만 공격으로 취급.
    M2/P1: 상대가 공격하지 않으므로 방어·회피·일반 반격은 [전투 시작시] 효과만 내고 행동하지 않음."""
    sk = u.skills[key]
    if sk.slot != "DEF" or sk.atk_type not in PASSIVE_DEF:
        return True
    ast = rt.skill_ast(u, sk)
    return ast is not None and "clashable_counter" in ast.flags


@dataclass(frozen=True)
class Choice:
    card: int | str            # 손패 칸 번호, 또는 "DEF" (아래 칸 대신 수비 스킬)
    target: str
    die: int = 0               # 대상의 속도 주사위 번호 (보스처럼 주사위가 여럿일 때 — 합 가로채기 대상)


Policy = Callable[[Battle, Unit], Choice]


@dataclass(frozen=True)
class Intent:
    """적 주사위 하나의 이번 턴 행동 — 플레이어가 선택하기 전에 공개 (사용자 확인 2026-10-28)."""
    unit: str
    die: int
    skill: tuple
    target: str
    speed: int


@dataclass
class ActionLog:
    unit: str
    skill: tuple               # 실제 사용 스킬 (교체 반영)
    target: str
    speed: int
    damage: float
    kind: str = "one-sided"    # one-sided | clash | defend (공격하지 않는 수비)
    clash: dict | None = None  # 합: {"with", "winner", "rounds"}


@dataclass
class TurnLog:
    turn: int
    actions: list = field(default_factory=list)
    cancelled: list = field(default_factory=list)     # 흐트러짐·사망으로 취소된 유닛
    shields: dict = field(default_factory=dict)       # 전투 시작 직후 보호막 (턴 종료 시 사라지므로 여기서 기록)


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
    """카드 사용 → 그 칸만 다음 장으로 보충. 수비 스킬은 아래 칸(1) 대신 생성 → 아래 칸 소모 (사용자 확인)."""
    if card == "DEF":
        u.hand[1] = _draw(u, rng)
        return DEF
    key = u.hand[card]
    u.hand[card] = _draw(u, rng)
    return key


# ── 정책 (고정 규칙) ────────────────────────────────────────────────────────
def first_enemy(battle: Battle, u: Unit) -> str:
    return next(e.name for e in battle.enemies_of(u) if e.hp > 0)


def _strength(battle: Battle, it: Intent) -> float:
    sk = next(x for x in battle.units if x.name == it.unit).skills[it.skill]
    return sk.base + sk.coin_power * sk.coins


def choose_target(battle: Battle, u: Unit) -> tuple[str, int]:
    """대상 주사위 고르기: 나를 노리는 적 주사위 중 내가 같거나 빠른 것 → 합으로 막기,
    없으면 다른 아군을 노리는 것 중 가로챌 수 있는 것 (가장 센 스킬), 없으면 첫 적의 주사위 0 (일방 공격)."""
    mine = [it for it in battle.intents if it.speed <= u.speed]
    for pool in ([it for it in mine if it.target == u.name], mine):
        if pool:
            it = max(pool, key=lambda it: _strength(battle, it))
            return it.unit, it.die
    return first_enemy(battle, u), 0


def policy_highest(battle: Battle, u: Unit) -> Choice:
    """두 칸 중 높은 스킬 (S3 > S2 > S1, 같으면 위 칸) + choose_target."""
    i = max(range(len(u.hand)), key=lambda j: (u.hand[j][0], -j))
    return Choice(i, *choose_target(battle, u))


def policy_top(battle: Battle, u: Unit) -> Choice:
    return Choice(0, *choose_target(battle, u))


# ── 턴 ────────────────────────────────────────────────────────────────────
def _can_act(battle: Battle, u: Unit) -> bool:
    return u.hp > 0 and u.stagger_until is None


def _roll(rt: Runtime, battle: Battle, u: Unit, rng: random.Random):
    m = new_mods()                                              # 상시 효과의 speed_min/max (아르뮈르 +2 등) 반영
    rt.run_static(battle, u, m)
    lo, hi = u.speed_min + int(m["speed_min"]), u.speed_max + int(m["speed_max"])
    u.speeds = [rng.randint(lo, max(lo, hi)) for _ in range(max(1, u.dice))]
    u.speed = u.speeds[0]


def begin_turn(rt: Runtime, battle: Battle, rng: random.Random) -> list[Unit]:
    """턴 시작 → 속도 → 적 의도 공개. 반환 = 이번 턴 고를 아군 (선택 단계 직전 상태)."""
    turn_start(rt, battle)
    actors = [u for u in battle.units if u.hand is not None and _can_act(battle, u)]
    enemies = [u for u in battle.units if u.pattern and _can_act(battle, u)]
    for u in actors + enemies:
        _roll(rt, battle, u, rng)
    battle.intents = []
    for e in enemies:
        foes = [x for x in battle.enemies_of(e) if x.hp > 0]
        if not foes:
            continue
        for d in range(e.dice):                                # 패턴 순환, 대상은 살아 있는 아군 중 균등 랜덤
            pos = battle.pattern_pos.get(e.name, 0)
            battle.pattern_pos[e.name] = pos + 1
            battle.intents.append(Intent(e.name, d, e.pattern[pos % len(e.pattern)], rng.choice(foes).name,
                                         e.speeds[d]))
    return actors


@dataclass
class _Act:
    unit: Unit
    die: int
    key: tuple
    target: str
    tdie: int
    speed: int
    ally: bool
    pair: "_Act | None" = None
    done: bool = False


def resolve_turn(rt: Runtime, battle: Battle, rng: random.Random, actors: list[Unit], choices: dict[str, Choice],
                 params: Params = DEFAULT) -> TurnLog:
    """선택 반영(카드 사용) → 합 짝짓기 → 공명 집계 → 전투 시작 → 속도순 행동 → 전투 종료 → 턴 종료.

    합 (규칙 3.2, 사용자 확인 2026-10-28 '가로채기 포함'): 아군이 적 주사위 E 를 대상으로 하고 속도 ≥ E 이면,
    E 가 누구를 노렸든 그 아군과 합. 여럿이면 마지막으로 E 를 고른 아군. 아니면 각자 일방 공격.
    """
    log = TurnLog(battle.turn)
    by = {u.name: u for u in battle.units}
    acts: list[_Act] = []
    for u in actors:
        c = choices[u.name]
        key = play(u, c.card, rng)
        acts.append(_Act(u, 0, key, c.target, c.die, u.speed, True))
    for it in battle.intents:
        e = by[it.unit]
        if _can_act(battle, e):
            acts.append(_Act(e, it.die, it.skill, it.target, 0, it.speed, False))

    allies = [a for a in acts if a.ally]
    sins = [a.unit.skills[a.unit.skill_override.get(a.key, a.key)].sin for a in allies]
    battle.resonance = dict(Counter(s for s in sins if s))
    # [확인 필요] 최대 공명 수 정의 — 지금은 이번 턴 같은 속성 스킬 수의 최댓값
    battle.max_resonance = max(battle.resonance.values(), default=0)
    combat_start(rt, battle, {a.unit.name: [a.key] for a in allies})
    log.shields = {u.name: u.shield for u in battle.units}
    # 합 짝짓기는 [전투 시작시] 스킬 교체(#204 묵시 → S3 variant 1) 뒤에
    for e in (a for a in acts if not a.ally):
        cands = [a for a in acts if a.ally and a.target == e.unit.name and a.tdie == e.die and a.speed >= e.speed
                 and attacks(rt, a.unit, a.unit.skill_override.get(a.key, a.key)) and a.pair is None]
        if cands:
            a = cands[-1]
            a.pair, e.pair = e, a

    toss = lambda p: rng.random() < p
    rt.current_toss = toss
    # 공격하지 않는 수비 스킬 (방어·회피·반격): 자기 속도에 행동하지 않고, 이번 턴 자신에게 처음 들어오는 공격에 반응
    guard = {a.unit.name: a for a in acts if a.ally and not attacks(rt, a.unit, a.unit.skill_override.get(a.key, a.key))}
    for a in guard.values():
        a.done = True
        sk = a.unit.skills[a.unit.skill_override.get(a.key, a.key)]
        battle.used_sins.append(sk.sin)
        if sk.atk_type == "회피":                    # 이번 턴 나를 때리는 적의 모든 스킬에 적용 (사용자 확인 2026-10-28)
            a.unit.evade_skill = sk
    for a in sorted(acts, key=lambda x: -x.speed):             # 안정 정렬: 같은 속도는 아군 → 적, 입력 순서
        if a.done:
            continue
        a.done = True
        u = a.unit
        key = u.skill_override.get(a.key, a.key)
        if not _can_act(battle, u):
            log.cancelled.append(u.name)
            continue
        target = by[a.target]
        if target.hp <= 0:
            continue
        p = a.pair
        if p is not None and not p.done and _can_act(battle, p.unit):
            p.done = True
            o = run_clash(rt, battle, u, a.key, p.unit, p.key, toss, params)
            for x, other in ((a, p), (p, a)):
                if x.ally:
                    battle.used_sins.append(x.unit.skills[x.unit.skill_override.get(x.key, x.key)].sin)
                won = o.winner == x.unit.name
                log.actions.append(ActionLog(
                    x.unit.name, x.unit.skill_override.get(x.key, x.key), other.unit.name, x.speed,
                    o.result.damage if (won and o.result) else 0.0, kind="clash",
                    clash={"with": other.unit.name, "winner": o.winner, "rounds": o.rounds}))
            continue
        g = guard.get(target.name) if not a.ally else None
        if g is not None and g.unit.skills[g.unit.skill_override.get(g.key, g.key)].atk_type != "회피":
            guard.pop(target.name)                              # 방어·반격은 처음 들어오는 공격 하나에만
        gkey = g.unit.skill_override.get(g.key, g.key) if g else None
        if g is not None and g.unit.skills[gkey].atk_type == "방어" and _can_act(battle, g.unit):
            sh = defense_power(g.unit.skills[gkey], g.unit, toss, params)    # 방어: 위력만큼 보호막 후 피격
            g.unit.shield += sh
            log.actions.append(ActionLog(g.unit.name, gkey, u.name, g.speed, 0.0, kind="guard", clash={"shield": sh}))
        r = run_action(rt, battle, u, a.key, target, toss, params)
        if a.ally:
            battle.used_sins.append(u.skills[r.skill].sin)
        log.actions.append(ActionLog(u.name, r.skill, a.target, a.speed, r.damage))
        if g is not None and g.unit.skills[gkey].atk_type == "반격" and _can_act(battle, g.unit) and u.hp > 0:
            rc = run_action(rt, battle, g.unit, g.key, u, toss, params)       # 반격: 피해를 받은 뒤 일방 공격
            log.actions.append(ActionLog(g.unit.name, rc.skill, u.name, g.speed, rc.damage, kind="counter"))
    for g in guard.values():                                    # 공격받지 않은 수비 스킬 (회피는 여기서 기록)
        log.actions.append(ActionLog(g.unit.name, g.unit.skill_override.get(g.key, g.key), "", g.speed, 0.0,
                                     kind="defend"))
        g.unit.evade_skill = None
    rt.current_toss = None

    combat_end(rt, battle)
    for u in battle.units:
        if u.stagger_until == battle.turn:
            u.stagger, u.stagger_until = 0, None
    # E.G.O 자원: 이번 턴 사용분은 다음 턴부터 보유 조건에 반영 (docs/rules 1장)
    for s in battle.used_sins:
        if s:
            battle.sin_owned[s] = battle.sin_owned.get(s, 0) + 1
    battle.used_sins.clear()
    battle.intents = []
    turn_end(rt, battle)
    return log


def run_turn(rt: Runtime, battle: Battle, rng: random.Random, policy: Policy = policy_highest,
             params: Params = DEFAULT) -> TurnLog:
    """턴 시작 → 속도 → 스킬 선택 (정책) → resolve_turn."""
    actors = begin_turn(rt, battle, rng)
    if hasattr(policy, "joint"):                              # 팀 결합 선택 (TeamRolloutPolicy, 팀 계획)
        choices = policy.joint(battle, actors)
    else:
        choices = {u.name: policy(battle, u) for u in actors}
    return resolve_turn(rt, battle, rng, actors, choices, params)


def battle_over(battle: Battle) -> bool:
    return (all(u.hp <= 0 for u in battle.units if u.side == "enemy")
            or all(u.hp <= 0 for u in battle.units if u.side == "ally"))


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

"""M1: 일방 공격 1회 실행 + 코인 결과 정확 열거 (docs/07 §3-4)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from engine.coins import heads_prob
from engine.damage import HitContext, hit_damage
from engine.params import DEFAULT, Params

from .interp import Action, Runtime
from .state import Battle, Unit

Toss = Callable[[float], bool]


def _apply_pending(rt: Runtime, battle: Battle):
    now = [p for p in battle.pending if p[0] <= battle.turn]
    battle.pending = [p for p in battle.pending if p[0] > battle.turn]
    by_name = {u.name: u for u in battle.units}
    for _, kind, uname, res, x, other in now:
        if kind == "gain":
            rt.change(battle, by_name[uname], res, x, source=by_name.get(other))
        elif kind == "convert":
            rt.convert(battle, by_name[uname], res, by_name[other], x)


def stage_enter(rt: Runtime, battle: Battle):
    rt.emit(battle, "stage_enter")


def turn_start(rt: Runtime, battle: Battle):
    _apply_pending(rt, battle)
    rt.emit(battle, "turn_start")


def combat_start(rt: Runtime, battle: Battle, slotted: dict[str, list[tuple]] | None = None):
    """전투 시작시: 패시브·키워드 + 이번 턴 슬롯에 잡힌 스킬의 [전투 시작시] 블록."""
    rt.emit(battle, "combat_start")
    for u in battle.units:
        for key in (slotted or {}).get(u.name, []):
            skill = u.skills[u.skill_override.get(key, key)]
            rt._action_seq += 1
            pseudo = Action(rt._action_seq, u, None, skill)
            ast = rt.skill_ast(u, skill)
            if ast is None:
                continue
            from .interp import Frame
            base = Frame(battle, u, action=pseudo, aliases=rt.aliases_for(u))
            for b in ast.blocks:
                rt._run_block(b, base, "combat_start", key=f"{u.name}/{ast.ref.slot}{ast.ref.variant}")


def combat_end(rt: Runtime, battle: Battle):
    rt.emit(battle, "combat_end")


# 턴 종료 시 내장 감소 (M1: 키워드 정의에 감소 규칙이 없는 표준 상태만)
BUILTIN_TURN_END_DECAY = {"진동": 1, "충전": 1}


def turn_end(rt: Runtime, battle: Battle):
    rt.emit(battle, "turn_end")
    for u in battle.units:
        for kw, n in BUILTIN_TURN_END_DECAY.items():
            if kw in u.statuses:
                rt.change(battle, u, ("status", kw, "count"), -n, source=None)
        u.skill_override.clear()
    battle.turn += 1


@dataclass
class ActionResult:
    damage: float
    coin_damage: dict
    coin_power: dict
    heads: dict
    skill: tuple


def run_action(rt: Runtime, battle: Battle, actor: Unit, skill_key: tuple, target: Unit, toss: Toss,
               params: Params = DEFAULT) -> ActionResult:
    key = actor.skill_override.get(skill_key, skill_key)
    rt._action_seq += 1
    a = Action(rt._action_seq, actor, target, actor.skills[key])

    rt.emit(battle, "before_use", action=a)
    if a.replace_to:
        a.skill = actor.skills[a.replace_to]
    rt.emit(battle, "use", action=a)

    skill_ast = rt.skill_ast(actor, a.skill)
    if skill_ast is not None:
        rt.run_static(battle, actor, a.mods, action=a, skill=skill_ast)
    rt.run_static(battle, actor, a.mods, action=a)                 # 공격자 패시브·키워드 상시 효과
    rt.run_static(battle, target, a.target_mods)                   # 대상 상시 효과 (방어 레벨 등)

    rt.emit(battle, "before_attack", action=a)
    rt.emit(battle, "ally_before_attack", actor=actor)

    sk = a.skill
    heads: dict[int, bool] = {}
    k = 1
    while k <= sk.coins:
        a.coin = k
        cm = a.coin_mods[k]
        if skill_ast is not None and k not in heads:
            coin_ast = next((c for c in skill_ast.coins if c.index == k), None)
            if coin_ast is not None:
                rt.run_static(battle, actor, cm, action=a, coin=coin_ast)
        p = min(1.0, max(0.0, heads_prob(actor.sanity) + (a.mods["heads_prob"] + cm["heads_prob"]) / 100))
        h = toss(p)
        heads[k] = h
        a.coin_heads[k] = h
        rt.emit(battle, "coin_flip", action=a)
        rt.emit(battle, "head" if h else "tail", action=a)
        head_sum = sum(sk.coin_power + a.mods["coin_power"] + a.coin_mods[j]["coin_power"]
                       for j in range(1, k + 1) if heads.get(j))
        power = max(params.min_power,
                    sk.base + a.mods["base_power"] + head_sum + a.mods["final_power"] + cm["final_power"])
        a.coin_power[k] = power
        level = sk.level + a.mods["atk_level"]
        ctx = HitContext(
            phys_res=target.phys_res.get(sk.atk_type, "보통"),
            sin_res=target.sin_res.get(sk.sin, "보통"),
            offense_level=level,
            defense_level=(target.def_level or level) + a.target_mods["def_level"],
            dmg_pct=(a.mods["damage_pct"] + cm["damage_pct"] + a.target_mods["taken_damage_pct"]) / 100,
            flat_pre=a.mods["damage_flat"] + cm["damage_flat"],
        )
        dmg = hit_damage(power, ctx, params)
        a.coin_damage[k] = a.coin_damage.get(k, 0) + dmg
        rt.damage(target, dmg, a)
        rt.emit(battle, "hit", action=a)
        rt.emit(battle, "head_hit" if h else "tail_hit", action=a)
        rt.emit(battle, "hit_taken", action=a, target=target)
        rt.emit(battle, "ally_hit_taken", actor=actor, target=target)
        if a.reuse > 0:                 # 이 코인 재사용: 같은 코인을 다시 토스해 적중
            a.reuse -= 1
            continue
        k += 1
    a.coin = None
    rt.emit(battle, "attack_end", action=a)
    rt.emit(battle, "ally_attack_end", actor=actor)
    for event, body, frame in a.deferred:
        if event == "attack_end":
            rt.exec_body(body, frame)
    return ActionResult(a.damage, dict(a.coin_damage), dict(a.coin_power), dict(a.coin_heads), a.skill.key)


# ── 정확 열거 ────────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    prob: float
    battle: Battle
    result: object
    heads: tuple


def enumerate_outcomes(fn: Callable[[Toss], tuple[Battle, object]]) -> list[Outcome]:
    """fn(toss) 를 코인 결과 경우마다 다시 실행해 모든 분기를 확률과 함께 돌려준다.

    코인 수가 실행 중에 바뀌어도(재사용 등) 동작한다: 앞 결과를 고정한 접두사로 재실행하고,
    새로 나온 토스 위치마다 뒷면 분기를 추가한다.
    """
    out: list[Outcome] = []
    stack: list[list[bool]] = [[]]
    while stack:
        prefix = stack.pop()
        trace: list[tuple[bool, float]] = []

        def toss(p: float) -> bool:
            i = len(trace)
            v = prefix[i] if i < len(prefix) else True
            trace.append((v, p))
            return v

        battle, result = fn(toss)
        prob = 1.0
        for v, p in trace:
            prob *= p if v else (1 - p)
        out.append(Outcome(prob, battle, result, tuple(v for v, _ in trace)))
        for i in range(len(prefix), len(trace)):
            stack.append([v for v, _ in trace[:i]] + [False])
    return out


def expect(outcomes: list[Outcome], f: Callable[[Outcome], float]) -> float:
    return sum(o.prob * f(o) for o in outcomes)


def distribution(outcomes: list[Outcome], f: Callable[[Outcome], float]) -> dict:
    d = defaultdict(float)
    for o in outcomes:
        d[round(f(o), 6)] += o.prob
    return dict(sorted(d.items()))


def simulate_action(rt: Runtime, battle: Battle, actor: str, skill_key: tuple, target: str,
                    params: Params = DEFAULT) -> list[Outcome]:
    """battle 은 그대로 두고, 복제본에서 행동 1회를 코인 경우마다 실행."""
    def fn(toss):
        b = battle.clone()
        by = {u.name: u for u in b.units}
        r = run_action(rt, b, by[actor], skill_key, by[target], toss, params)
        return b, r
    return enumerate_outcomes(fn)

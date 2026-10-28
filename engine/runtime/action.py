"""M1: 일방 공격 1회 실행 + 코인 결과 정확 열거 (docs/07 §3-4)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from engine.coins import heads_prob
from engine.damage import HitContext, hit_damage
from engine.params import DEFAULT, Params

from .interp import Action, Runtime, new_mods
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
            if pseudo.replace_to:                   # [전투 시작시] replace this → 이번 턴 그 슬롯 스킬 교체
                u.skill_override[key] = pseudo.replace_to


def combat_end(rt: Runtime, battle: Battle):
    rt.emit(battle, "combat_end")


def turn_end(rt: Runtime, battle: Battle):
    """턴 종료시. 표준 상태의 감소·화상 피해는 lpdlc-v2 keywords/standard.lpdl 의 turn_end 블록."""
    rt.emit(battle, "turn_end")
    for u in battle.units:
        u.skill_override.clear()
        u.shield = 0            # 보호막은 다음 턴으로 넘어가지 않음 (사용자 확인 2026-10-28). next_turn 보호막은 다음 턴 시작에 들어옴
    battle.turn += 1


@dataclass
class ActionResult:
    damage: float
    coin_damage: dict
    coin_power: dict
    heads: dict
    skill: tuple
    crits: dict = field(default_factory=dict)      # 코인 번호 → 치명타 여부


def choose(toss: Toss, probs: list[float]) -> int:
    """범주형 선택을 이진 토스 연쇄로 분해 (열거기는 이진 토스만 안다). 확률 0/1 인 갈래는 토스하지 않는다."""
    rest = sum(probs)
    last = max(i for i, p in enumerate(probs) if p > 0)
    for i, p in enumerate(probs):
        if p <= 0:
            continue
        if i == last or p >= rest - 1e-12:
            return i
        if toss(p / rest):
            return i
        rest -= p
    return last


def _chance(toss: Toss, p: float) -> bool:
    return p >= 1 or (p > 0 and toss(p))


def check_stagger(battle: Battle, u: Unit):
    """직접 피해(코인 적중) 뒤 흐트러짐 선 판정 (docs/rules 4장). 한 번에 깨진 선 수 = 흐트러짐 단계.
    흐트러진 턴의 남은 행동 취소 + 다음 턴 행동 불가 → 다음 턴 종료 시 회복 (사용자 확인 2026-10-28)."""
    broken = [x for x in u.stagger_lines if u.hp <= u.max_hp * x / 100]
    if not broken or u.hp <= 0:
        return
    u.stagger_lines = [x for x in u.stagger_lines if x not in broken]
    u.stagger = min(3, max(u.stagger, len(broken)))
    u.stagger_until = battle.turn + 1
    battle.log.append(("stagger", u.name, battle.turn, u.stagger))


def _begin(rt: Runtime, battle: Battle, actor: Unit, skill_key: tuple, target: Unit) -> tuple[Action, object]:
    """[사용전] → [사용시] → 상시 효과. 일방 공격 직전 / 합 시작 전 (스킬을 '사용'하는 시점)."""
    key = actor.skill_override.get(skill_key, skill_key)
    rt._action_seq += 1
    a = Action(rt._action_seq, actor, target, actor.skills[key])
    rt.current_action = a

    rt.emit(battle, "before_use", action=a)
    if a.replace_to:
        a.skill = actor.skills[a.replace_to]
    rt.emit(battle, "use", action=a)

    skill_ast = rt.skill_ast(actor, a.skill)
    if skill_ast is not None:
        rt.run_static(battle, actor, a.mods, action=a, skill=skill_ast)
    rt.run_static(battle, actor, a.mods, action=a)                 # 공격자 패시브·키워드 상시 효과
    rt.run_static(battle, target, a.target_mods)                   # 대상 상시 효과 (방어 레벨 등)
    return a, skill_ast


def _attack(rt: Runtime, battle: Battle, a: Action, skill_ast, target: Unit, toss: Toss, params: Params,
            coins: list[int], clash_count: int = 0) -> ActionResult:
    """공격 단계: coins(남은 코인 번호)를 순서대로 토스해 적중. 코인 k 위력 = 기본 + 남은 코인 중 k번째까지 앞면 합.

    [공격 시작 전] 은 여기서 발생 — 일방 공격 직전, 합은 승리 후 공격 직전 (패자는 발생하지 않음).
    """
    actor, sk = a.actor, a.skill
    a.last_coin = coins[-1] if coins else None
    rt.emit(battle, "before_attack", action=a)
    rt.emit(battle, "ally_before_attack", actor=actor)
    heads: dict[int, bool] = {}
    crits: dict[int, bool] = {}
    i = 0
    while i < len(coins):
        k = coins[i]
        a.coin = k
        cm = a.coin_mods[k]
        if k not in heads:
            if skill_ast is not None:
                coin_ast = next((c for c in skill_ast.coins if c.index == k), None)
                if coin_ast is not None:
                    rt.run_static(battle, actor, cm, action=a, coin=coin_ast)
            # [코인 시작 시]: 공격할 때만 (합 라운드에는 없음), 코인당 1회 — 소모도 1회 (사용자 확인 2026-10-28)
            rt.emit(battle, "coin_start", action=a)
        p =min(1.0, max(0.0, heads_prob(actor.sanity) + (a.mods["heads_prob"] + cm["heads_prob"]) / 100))
        h = toss(p)
        heads[k] = h
        a.coin_heads[k] = h
        rt.emit(battle, "coin_flip", action=a)
        rt.emit(battle, "head" if h else "tail", action=a)
        head_sum = sum(sk.coin_power + a.mods["coin_power"] + a.coin_mods[j]["coin_power"]
                       for j in coins[:i + 1] if heads.get(j))
        power = max(params.min_power,
                    sk.base + a.mods["base_power"] + head_sum + a.mods["final_power"] + cm["final_power"])
        a.coin_power[k] = power
        # 치명타: 확률은 spec 의 modify crit_chance (호흡 = keywords/standard.lpdl)
        crit = _chance(toss, (a.mods["crit_chance"] + cm["crit_chance"]) / 100)
        crits[k] = crit
        level = sk.level + a.mods["atk_level"]
        ctx = HitContext(
            phys_res=target.phys_res.get(sk.atk_type, "보통"),
            sin_res=target.sin_res.get(sk.sin, "보통"),
            stagger=target.stagger,
            offense_level=level,
            defense_level=(target.def_level or level) + a.target_mods["def_level"],
            crit=crit,
            clash_count=clash_count,
            dmg_pct=(a.mods["damage_pct"] + cm["damage_pct"] + a.target_mods["taken_damage_pct"]) / 100,
            flat_pre=a.mods["damage_flat"] + cm["damage_flat"],
        )
        dmg = hit_damage(power, ctx, params)
        a.coin_pct[k] = params.dmg_pct_base + ctx.dmg_pct
        a.coin_damage[k] = a.coin_damage.get(k, 0) + dmg
        rt.damage(target, dmg, a)
        check_stagger(battle, target)
        rt.emit(battle, "hit", action=a)
        rt.emit(battle, "head_hit" if h else "tail_hit", action=a)
        if crit:
            rt.emit(battle, "crit", action=a)
        rt.emit(battle, "hit_taken", action=a, target=target)
        rt.emit(battle, "ally_hit_taken", actor=actor, target=target)
        if a.reuse > 0:                 # 이 코인 재사용: 같은 코인을 다시 토스해 적중
            a.reuse -= 1
            continue
        i += 1
    a.coin = None
    rt.emit(battle, "attack_end", action=a)
    rt.emit(battle, "ally_attack_end", actor=actor)
    for event, body, frame in a.deferred:
        if event == "attack_end":
            rt.exec_body(body, frame)
    rt.current_action = None
    return ActionResult(a.damage, dict(a.coin_damage), dict(a.coin_power), dict(a.coin_heads), a.skill.key, crits)


def run_action(rt: Runtime, battle: Battle, actor: Unit, skill_key: tuple, target: Unit, toss: Toss,
               params: Params = DEFAULT) -> ActionResult:
    """일방 공격 1회."""
    a, skill_ast = _begin(rt, battle, actor, skill_key, target)
    return _attack(rt, battle, a, skill_ast, target, toss, params, list(range(1, a.skill.coins + 1)))


# ── 합 (docs/rules 3장) ──────────────────────────────────────────────────────
@dataclass
class ClashOutcome:
    winner: str | None              # 승자 이름. None = 끝나지 않음 (위력 고정 동점, max_rounds)
    rounds: int                     # 합 비교 횟수 (동점 포함)
    remaining: list                 # 승자의 남은 코인 번호
    result: ActionResult | None     # 승자의 공격 결과
    log: list = field(default_factory=list)   # 라운드별 ("A"|"B"|"=", A 남은 코인 수, B 남은 코인 수)


def _clash_power_dist(rt: Runtime, battle: Battle, a: Action, skill_ast, left: list[int], params: Params) -> dict:
    """남은 코인을 모두 토스했을 때 합 위력 분포. 코인마다 위력·앞면 확률이 달라도 되게 합성곱으로 계산."""
    sk = a.skill
    dist = {sk.base + a.mods["base_power"] + a.mods["final_power"] + a.mods["clash_power"]: 1.0}
    for k in left:
        cm = new_mods()
        for name, v in a.coin_mods[k].items():
            cm[name] += v
        coin_ast = next((c for c in skill_ast.coins if c.index == k), None) if skill_ast is not None else None
        if coin_ast is not None:                                   # 코인 상시 효과 (현재 상태 기준, 버림 복사본)
            rt.run_static(battle, a.actor, cm, action=a, coin=coin_ast)
        p = min(1.0, max(0.0, heads_prob(a.actor.sanity) + (a.mods["heads_prob"] + cm["heads_prob"]) / 100))
        cp = sk.coin_power + a.mods["coin_power"] + cm["coin_power"]
        nxt: dict = defaultdict(float)
        for v, q in dist.items():
            if p > 0:
                nxt[v + cp] += q * p
            if p < 1:
                nxt[v] += q * (1 - p)
        dist = nxt
    out: dict = defaultdict(float)
    for v, q in dist.items():
        out[max(params.min_power, v)] += q
    return out


def run_clash(rt: Runtime, battle: Battle, a_unit: Unit, a_key: tuple, b_unit: Unit, b_key: tuple, toss: Toss,
              params: Params = DEFAULT, max_rounds: int = 100) -> ClashOutcome:
    """A 와 B 가 서로를 대상으로 합 → 승자가 남은 코인으로 패자를 공격.

    라운드마다 양쪽 남은 코인을 모두 토스, 위력이 낮은 쪽 첫 코인 파괴,
    동점이면 둘 다 보존하고 다시 비교. 라운드 결과는 승/패/동점 세 갈래로만 열거한다 (위력 분포로 확률 계산).
    합 중 코인 판정: 스킬·코인 효과는 발동하지 않고(공격 단계에서만), 패시브·키워드(출혈 등)만 반응한다.
    """
    a, a_ast = _begin(rt, battle, a_unit, a_key, b_unit)
    b, b_ast = _begin(rt, battle, b_unit, b_key, a_unit)
    rt.emit(battle, "clash_start", action=a)
    rt.emit(battle, "clash_start", action=b)

    la, lb = list(range(1, a.skill.coins + 1)), list(range(1, b.skill.coins + 1))
    rounds, log = 0, []
    while la and lb and rounds < max_rounds:
        for x, left in ((a, la), (b, lb)):
            for k in left:
                x.coin = k
                rt.emit(battle, "coin_flip", action=x, skill_blocks=False)
            x.coin = None
        da = _clash_power_dist(rt, battle, a, a_ast, la, params)
        db = _clash_power_dist(rt, battle, b, b_ast, lb, params)
        w = l = t = 0.0
        for pa, qa in da.items():
            for pb, qb in db.items():
                if pa > pb:
                    w += qa * qb
                elif pa < pb:
                    l += qa * qb
                else:
                    t += qa * qb
        rounds += 1
        if t >= 1 - 1e-12:                 # 위력이 고정이고 같음 → 영원히 동점
            log.append(("=", len(la), len(lb)))
            break
        r = choose(toss, [w, l, t])
        if r == 0:
            lb.pop(0)
        elif r == 1:
            la.pop(0)
        log.append(("A" if r == 0 else "B" if r == 1 else "=", len(la), len(lb)))

    if bool(la) == bool(lb):
        return ClashOutcome(None, rounds, [], None, log)
    win, lose, w_ast, left = (a, b, a_ast, la) if la else (b, a, b_ast, lb)
    # [확인 필요] 정신력 변화 시점: 합 종료 직후, 공격 전 (공격 단계 앞면 확률에 반영됨). 패배 측 변화는 미정 → 0
    gain = params.sanity_clash_win_base * (1 + params.sanity_clash_step * (rounds - 1))
    rt.change(battle, win.actor, ("field", "sanity"), gain, source=win.actor)
    rt.emit(battle, "clash_win", action=win)
    rt.emit(battle, "clash_lose", action=lose)
    res = _attack(rt, battle, win, w_ast, lose.actor, toss, params, left, clash_count=rounds)
    return ClashOutcome(win.actor.name, rounds, left, res, log)


# ── 정확 열거 ────────────────────────────────────────────────────────────────
@dataclass
class Outcome:
    prob: float
    battle: Battle
    result: object
    heads: tuple


def enumerate_outcomes(fn: Callable[[Toss], tuple[Battle, object]], min_prob: float = 0.0) -> list[Outcome]:
    """fn(toss) 를 코인 결과 경우마다 다시 실행해 모든 분기를 확률과 함께 돌려준다.

    코인 수가 실행 중에 바뀌어도(재사용 등) 동작한다: 앞 결과를 고정한 접두사로 재실행하고,
    새로 나온 토스 위치마다 뒷면 분기를 추가한다.
    min_prob > 0 이면 도달 확률이 그보다 작은 분기를 버린다 (합의 연속 동점처럼 끝없는 분기용).
    버린 확률은 1 - Σ prob 로 확인한다.
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
        reach = 1.0
        for i, (v, p) in enumerate(trace):
            if i >= len(prefix) and reach * (1 - p) > min_prob:
                stack.append([x for x, _ in trace[:i]] + [False])
            reach *= p if v else (1 - p)
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


def simulate_clash(rt: Runtime, battle: Battle, a: str, a_key: tuple, b: str, b_key: tuple,
                   params: Params = DEFAULT, min_prob: float = 1e-7) -> list[Outcome]:
    """battle 은 그대로 두고, 복제본에서 합 1회(+ 승자 공격)를 경우마다 실행. result = ClashOutcome."""
    def fn(toss):
        bt = battle.clone()
        by = {u.name: u for u in bt.units}
        return bt, run_clash(rt, bt, by[a], a_key, by[b], b_key, toss, params)
    return enumerate_outcomes(fn, min_prob)

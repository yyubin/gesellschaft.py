"""LPDL v2 실행기: AST 블록을 이벤트 핸들러로 실행한다 (docs/07 §2, §4).

- 같은 이벤트의 블록은 적힌 순서대로 실행 (R1)
- modify 는 즉시 적용하지 않고 현재 프레임의 Mods(행동/코인/이벤트)에 쌓는다
- limit / budget / every / next_turn / defer 상태는 Battle 에 둔다 (복제 가능한 데이터만)
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from lpdl import ast as A

from .state import BUILTIN_KINDS, CHARGE_KINDS, Battle, SkillData, Status, Unit

ACTION_EVENTS = {"before_use", "use", "before_attack", "attack_end", "kill", "clash_start", "clash_win",
                 "clash_lose", "coin_flip", "hit", "head_hit", "tail_hit", "crit", "head", "tail"}
COIN_EVENTS = {"coin_flip", "hit", "head_hit", "tail_hit", "crit", "head", "tail", "clash_win", "clash_lose"}
RESOURCE_EVENTS = {"gained", "consumed", "applied", "burst"}
UNIT_FIELDS = {"hp", "max_hp", "hp_ratio", "shield", "sanity", "speed", "level", "formation"}
ALLY_EVENTS = {"ally_hit_taken", "ally_before_attack", "ally_attack_end"}
SINS = ("분노", "색욕", "나태", "탐식", "우울", "오만", "질투")
PHYS = {"slash": "참격", "pierce": "관통", "blunt": "타격"}


class RuntimeError_(Exception):
    pass


def new_mods() -> defaultdict:
    return defaultdict(float)


@dataclass
class Event:
    name: str
    unit: Unit | None = None             # 자원·상태의 주인 (gained/consumed/applied/burst)
    resource: tuple | None = None        # ("status", kw, field) | ("value", kw) | ("field", name)
    keyword: str | None = None
    amount: float = 0
    overflow: float = 0                  # 획득량 중 최대치 초과분
    source: Unit | None = None           # 효과를 일으킨 유닛 (by)
    source_skill: SkillData | None = None
    mods: defaultdict = field(default_factory=new_mods)


@dataclass
class Action:
    id: int
    actor: Unit
    target: Unit | None
    skill: SkillData
    mods: defaultdict = field(default_factory=new_mods)              # 스킬 전체
    coin_mods: dict = field(default_factory=lambda: defaultdict(new_mods))   # 코인 번호 → Mods
    target_mods: defaultdict = field(default_factory=new_mods)       # 대상 쪽 (방어 레벨, 받는 피해)
    replace_to: tuple | None = None
    deferred: list = field(default_factory=list)                 # (event, body, frame)
    coin: int | None = None
    reuse: int = 0                                              # reuse_coin 대기 횟수
    coin_heads: dict = field(default_factory=dict)
    coin_power: dict = field(default_factory=dict)
    coin_damage: dict = field(default_factory=dict)
    damage: float = 0


@dataclass
class Frame:
    battle: Battle
    self_: Unit
    target: Unit | None = None
    attacker: Unit | None = None
    action: Action | None = None
    ev: Event | None = None
    scope: str = "action"                # action | coin | event | static | target_static
    aliases: dict = field(default_factory=dict)
    locals: dict = field(default_factory=dict)
    block_key: str = ""

    def child(self, **kw) -> "Frame":
        f = Frame(**{**self.__dict__, "locals": dict(self.locals)})
        for k, v in kw.items():
            setattr(f, k, v)
        return f


class Runtime:
    """로드된 LPDL 문서 + 훅 레지스트리. 상태(Battle)는 갖지 않는다."""

    def __init__(self, doc: A.Document, hooks: dict[str, Callable] | None = None):
        self.identities = {i.id: i for i in doc.identities}
        self.keywords = {k.name: k for k in doc.keywords}
        self.hooks = dict(hooks or {})
        self._action_seq = 0

    # ── 키워드 메타 ──
    def kind(self, kw: str) -> str:
        k = self.keywords.get(kw)
        if k is not None:
            return str(k.props.get("kind", "status"))
        return BUILTIN_KINDS.get(kw, "status")

    def prop(self, kw: str, name: str):
        k = self.keywords.get(kw)
        return k.props.get(name) if k is not None else None

    def aliases_for(self, unit: Unit, extra: dict | None = None) -> dict:
        ident = self.identities.get(unit.identity_id)
        a = dict(ident.uses) if ident else {}
        if extra:
            a.update(extra)
        return a

    # ── 이벤트 디스패치 ──
    def emit(self, battle: Battle, name: str, *, actor: Unit | None = None, target: Unit | None = None,
             action: Action | None = None, ev: Event | None = None, units: list[Unit] | None = None):
        """핸들러 순서: 행동 스킬 블록 → 현재 코인 블록 → (편성 순) 유닛별 패시브 → 보유 키워드 정의."""
        if action is not None:
            skill = self.skill_ast(action.actor, action.skill)
            if skill is not None:
                base = Frame(battle, action.actor, target=action.target, action=action, ev=ev,
                             aliases=self.aliases_for(action.actor))
                for b in skill.blocks:
                    self._run_block(b, base, name, key=f"{action.actor.name}/{skill.ref.slot}{skill.ref.variant}")
                if name in COIN_EVENTS and action.coin is not None:
                    for c in skill.coins:
                        if c.index == action.coin:
                            for b in c.blocks:
                                self._run_block(b, base.child(scope="coin"), name,
                                                key=f"{action.actor.name}/{skill.ref.slot}{skill.ref.variant}/c{c.index}")
        for u in sorted(units or battle.units, key=lambda x: (x.side != "ally", x.formation)):
            if name in ACTION_EVENTS and (action is None or u is not action.actor):
                continue
            if name == "hit_taken" and u is not target:
                continue
            if name in ALLY_EVENTS:
                subject = target if name == "ally_hit_taken" else actor
                if subject is None or u is subject or u.side != subject.side:
                    continue
                frame = Frame(battle, u, target=subject, attacker=actor if name == "ally_hit_taken" else None,
                              ev=ev, aliases=self.aliases_for(u))
            else:
                frame = Frame(battle, u, target=(action.target if action and u is action.actor else target),
                              attacker=(action.actor if action and name == "hit_taken" else None),
                              action=(action if action and u is action.actor else None), ev=ev,
                              aliases=self.aliases_for(u))
            ident = self.identities.get(u.identity_id)
            if ident is not None:
                for p in ident.passives:
                    if not self._passive_active(battle, p):
                        continue
                    pf = frame.child(aliases={**frame.aliases, **p.uses})
                    for b in p.blocks:
                        self._run_block(b, pf, name, key=f"{u.name}/p:{p.name}")
            for kw_name, kw in self.keywords.items():
                if kw.blocks and self._holds(u, kw_name):
                    kf = frame.child(aliases={**frame.aliases, **kw.uses})
                    for b in kw.blocks:
                        self._run_block(b, kf, name, key=f"{u.name}/k:{kw_name}")

    def _holds(self, u: Unit, kw: str) -> bool:
        return kw in u.statuses or kw in u.values

    def _passive_active(self, battle: Battle, p: A.Passive) -> bool:
        if battle.force_passives or p.requires is None:
            return True
        src = battle.sin_owned if p.requires.type == "hold" else battle.resonance
        return all(src.get(s, 0) >= p.requires.count for s in p.requires.sins)

    def skill_ast(self, unit: Unit, skill: SkillData) -> A.Skill | None:
        ident = self.identities.get(unit.identity_id)
        if ident is None:
            return None
        for s in ident.skills:
            if (s.ref.slot, s.ref.variant) == skill.key:
                return s
        return None

    def _run_block(self, b, frame: Frame, event: str, key: str):
        if isinstance(b, A.Always) or b.event != event:
            return
        key = f"{key}@{b.line}"
        f = frame.child(block_key=key, scope=frame.scope)
        if event in RESOURCE_EVENTS:
            if not self._event_matches(b, f):
                return
            f = f.child(scope="event")
        if b.by is not None:
            src = frame.ev.source if frame.ev else (frame.action.actor if frame.action else None)
            who = self.eval(b.by, f)
            who = who if isinstance(who, list) else [who]
            if src is None or not any(src is w for w in who):
                return
        if b.when is not None and not self.truthy(self.eval(b.when, f)):
            return
        if b.limit is not None and not self._take_limit(b.limit, f, key):
            return
        if b.every is not None:
            n = self.eval(b.every, f)
            acc_key = (key, f.self_.name)
            before = f.battle.accum.get(acc_key, 0)
            after = before + (f.ev.amount if f.ev else 0)
            f.battle.accum[acc_key] = after
            for _ in range(int(after // n) - int(before // n)):
                self.exec_body(b.body, f)
            return
        self.exec_body(b.body, f)

    def _event_matches(self, b: A.On, f: Frame) -> bool:
        ev = f.ev
        if ev is None or not b.args:
            return False
        a = b.args[0]
        if isinstance(a, A.Ref) and len(a.path) == 1:              # 키워드 이름만: 아무 유닛
            return self.resolve_kw(a.path[0], f) == ev.keyword
        unit, res = self.resolve_ref(a, f)
        return unit is ev.unit and res == ev.resource

    def _take_limit(self, lim: A.Limit, f: Frame, key: str) -> bool:
        period = {"turn": f.battle.turn, "battle": 0, "use": f.action.id if f.action else -1}[lim.period]
        tgt = None
        if lim.per_target:
            t = f.attacker or f.target
            tgt = t.name if t else None
        k = (key, f.self_.name, lim.period, period, tgt)
        n = f.battle.limits.get(k, 0)
        if n >= lim.times:
            return False
        f.battle.limits[k] = n + 1
        return True

    def run_static(self, battle: Battle, unit: Unit, frame_mods: defaultdict, *, action: Action | None = None,
                   skill: A.Skill | None = None, coin: A.Coin | None = None, scope: str = "static"):
        """always 블록을 실행해 unit 의 수정치를 frame_mods 에 쌓는다.

        skill/coin 이 없으면 전장의 모든 유닛의 패시브·키워드 always 를 본다:
        자기 것은 그대로, 다른 유닛 것은 `to` 대상에 unit 이 포함될 때만 (오라).
        """
        def frame_for(owner: Unit, uses: dict) -> Frame:
            f = Frame(battle, owner, target=action.target if action else None,
                      action=action if owner is unit else None, scope=scope,
                      aliases={**self.aliases_for(owner), **(uses or {})})
            f.locals["__mods__"] = frame_mods
            f.locals["__aura_for__"] = unit
            return f

        if coin is not None or skill is not None:
            for b in (coin or skill).blocks:
                if isinstance(b, A.Always):
                    self.exec_body(b.body, frame_for(unit, {}))
            return
        for owner in battle.units:
            blocks: list = []
            ident = self.identities.get(owner.identity_id)
            if ident:
                for p in ident.passives:
                    if self._passive_active(battle, p):
                        blocks += [(b, p.uses) for b in p.blocks]
            for name, kw in self.keywords.items():
                if self._holds(owner, name):
                    blocks += [(b, kw.uses) for b in kw.blocks]
            for b, uses in blocks:
                if isinstance(b, A.Always):
                    self.exec_body(b.body, frame_for(owner, uses))

    # ── 문장 ──
    def exec_body(self, stmts: list, f: Frame):
        for s in stmts:
            self.exec(s, f)

    def exec(self, s, f: Frame):
        match s:
            case A.Text() | A.Note():
                pass
            case A.Unparsed():
                f.battle.log.append(("unparsed", s.text[:60]))
            case A.Let():
                f.locals[s.name] = self.eval(s.value, f)
            case A.Gain():
                unit, res = self.resolve_ref(s.ref, f)
                amt = self._capped(self.eval(s.amount, f), s.opts, f)
                if s.opts.budget:
                    amt = self._spend_budget(f, s.opts.budget, amt)
                if s.opts.next_turn:
                    f.battle.pending.append((f.battle.turn + 1, "gain", unit.name, res, amt, f.self_.name))
                else:
                    self.change(f.battle, unit, res, amt, source=f.self_, frame=f)
            case A.Apply():
                kw = self.resolve_kw(s.keyword, f)
                pot = self.eval(s.potency, f) if s.potency is not None else 0
                cnt = self.eval(s.count, f) if s.count is not None else None
                for u in self._units(self.eval(s.target, f)):
                    self.apply_status(f.battle, u, kw, pot, cnt, source=f.self_, frame=f)
            case A.Consume():
                unit, res = self.resolve_ref(s.ref, f)
                have = self.get(unit, res)
                if s.mode == "all":
                    n = have
                else:
                    want = self.eval(s.amount, f)
                    n = min(have, want) if s.mode == "up_to" else (want if have >= want else 0)
                n = max(0, n)
                if n > 0:
                    self.change(f.battle, unit, res, -n, source=f.self_, frame=f, consume=True)
                if s.bind:
                    f.locals[s.bind] = n
            case A.Modify():
                amt = self._capped(self.eval(s.amount, f), s.opts, f)
                self._modify(s.target, amt, s.opts, f)
            case A.Burst():
                kw = self.resolve_kw(s.keyword, f)
                times = int(self.eval(s.times, f)) if s.times is not None else 1
                for u in self._units(self.eval(s.target, f)):
                    for _ in range(times):
                        self.burst(f.battle, u, kw, source=f.self_, frame=f)
            case A.Deal():
                amt = self.eval(s.amount, f)
                for u in self._units(self.eval(s.target, f)):
                    from engine.params import DEFAULT
                    if s.damage_type in PHYS:
                        amt_u = amt * (1 + DEFAULT.res_delta[u.phys_res.get(PHYS[s.damage_type], "보통")])
                    elif s.damage_type in SINS:
                        amt_u = amt * (1 + DEFAULT.res_delta[u.sin_res.get(s.damage_type, "보통")])
                    else:
                        amt_u = amt
                    if s.damage_type == "stagger":
                        u.stagger_damage += amt_u
                    elif s.damage_type == "sanity":
                        u.sanity -= amt_u
                    else:
                        self.damage(u, amt_u, f.action)
            case A.Replace():
                dest = (s.dest.slot, s.dest.variant)
                if s.source is None:
                    if f.action is not None:
                        f.action.replace_to = dest
                else:
                    f.self_.skill_override[(s.source.slot, s.source.variant)] = dest
            case A.Convert():
                su, sres = self.resolve_ref(s.source, f)
                du, dres = self.resolve_ref(s.dest, f)
                if s.opts.next_turn:
                    f.battle.pending.append((f.battle.turn + 1, "convert", su.name, sres, dres, du.name))
                else:
                    self.convert(f.battle, su, sres, du, dres)
            case A.ReuseCoin():
                if f.action is not None:
                    f.action.reuse += int(self.eval(s.times, f)) if s.times is not None else 1
            case A.UseSkill():
                f.battle.log.append(("use_skill-pending", f.self_.name, (s.skill.slot, s.skill.variant)))   # M2
            case A.Retype():
                src, dst = self.resolve_kw(s.source, f), self.resolve_kw(s.dest, f)
                for u in self._units(self.eval(s.target, f)):
                    st = u.statuses.pop(src, None)
                    if st is not None:
                        cur = u.statuses.setdefault(dst, Status())
                        cur.potency += st.potency
                        cur.count += st.count
            case A.Hook():
                fn = self.hooks.get(s.name)
                if fn is None:
                    f.battle.log.append(("hook-missing", s.name))
                else:
                    fn(self, f, *[self.eval(a, f) for a in s.args])
            case A.When():
                for cond, body in s.branches:
                    if self.truthy(self.eval(cond, f)):
                        self.exec_body(body, f.child())
                        return
                self.exec_body(s.otherwise, f.child())
            case A.Defer():
                if f.action is not None:
                    f.action.deferred.append((s.event, s.body, f))

    def _capped(self, v, opts: A.StmtOpts, f: Frame):
        if opts.cap is not None:
            v = min(v, self.eval(opts.cap, f))
        return v

    def _spend_budget(self, f: Frame, name: str, amt: float) -> float:
        lim = self._budget_limit(f, name)
        if lim is None or amt <= 0:
            return amt
        k = (f.self_.name, name, f.battle.turn if lim.period == "turn" else 0)
        used = f.battle.budgets.get(k, 0)
        amt = max(0, min(amt, lim.times - used))
        f.battle.budgets[k] = used + amt
        return amt

    def _budget_limit(self, f: Frame, name: str):
        for kw in self.keywords.values():
            if name in kw.budgets:
                return kw.budgets[name]
        ident = self.identities.get(f.self_.identity_id)
        if ident:
            if name in ident.budgets:
                return ident.budgets[name]
            for p in ident.passives:
                if name in p.budgets:
                    return p.budgets[name]
        return None

    def _modify(self, target: str, amt: float, opts: A.StmtOpts, f: Frame):
        if "__mods__" in f.locals:                         # always 블록
            who = f.locals.get("__aura_for__")
            if opts.to is not None:
                if who is None or not any(who is x for x in self._units(self.eval(opts.to, f))):
                    return
            elif who is not None and f.self_ is not who:
                return
            f.locals["__mods__"][target] += amt
            return
        if f.scope == "event" and f.ev is not None:
            if target == "amount":
                f.ev.amount += amt
            else:
                f.ev.mods[target] += amt
            return
        a = f.action
        if a is None:
            f.battle.log.append(("modify-no-action", target, amt))
            return
        if f.scope == "coin" and a.coin is not None:
            a.coin_mods[a.coin][target] += amt
        elif opts.coins and opts.coins != "all":
            n = a.skill.coins
            idx = {"first": 1, "last": n, "current": a.coin or 1}.get(opts.coins)
            a.coin_mods[idx if idx else int(opts.coins)][target] += amt
        else:
            a.mods[target] += amt

    # ── 자원 변경 원시 동작 ──
    def get(self, unit: Unit, res: tuple) -> float:
        kind = res[0]
        if kind == "field":
            return getattr(unit, res[1])
        if kind == "value":
            return unit.values.get(res[1], 0)
        s = unit.statuses.get(res[1])
        return getattr(s, res[2]) if s else 0

    def change(self, battle: Battle, unit: Unit, res: tuple, delta: float, *, source: Unit | None,
               frame: Frame | None = None, consume: bool = False) -> float:
        if delta == 0:
            return 0
        kind = res[0]
        if delta > 0 and not consume:
            # gained 는 적용 전에 발생: 핸들러가 획득 전 상태를 보고 ev.amount 를 바꿀 수 있다 ("12 미만일 때 획득량 +1")
            ev = Event("gained", unit=unit, resource=res, keyword=res[1] if kind != "field" else None,
                       amount=delta, overflow=self._overflow(unit, res, delta), source=source,
                       source_skill=frame.action.skill if frame and frame.action else None)
            self.emit(battle, "gained", ev=ev)
            delta = ev.amount
            if delta <= 0:
                return 0
        if kind == "field":
            name = res[1]
            old = getattr(unit, name)
            new = old + delta
            if name == "sanity":
                new = max(-45, min(45, new))
            if name in ("shield", "hp"):
                new = max(0, new)
            setattr(unit, name, new)
            actual = new - old
        elif kind == "value":
            kw = res[1]
            old = unit.values.get(kw, 0)
            mx = self.prop(kw, "max")
            new = max(0, old + delta)
            if mx is not None:
                new = min(new, float(mx))
            unit.values[kw] = new          # 0 이어도 보유 상태 유지 (코트 0). 제거는 convert 로만 (R4)
            actual = new - old
        else:
            kw, fld = res[1], res[2]
            s = unit.statuses.get(kw)
            kkind = self.kind(kw)
            if s is None:
                if delta <= 0 or (fld == "count" and kkind not in CHARGE_KINDS):
                    return 0
                s = unit.statuses[kw] = Status()
            old = getattr(s, fld)
            new = max(0, old + delta)
            mx = self.prop(kw, f"{fld}_max")
            if mx is not None:
                new = min(new, float(mx))
            setattr(s, fld, new)
            actual = new - old
            if kkind not in CHARGE_KINDS and s.count <= 0:
                unit.statuses.pop(kw, None)
        if consume:
            # consumed 는 적용 후에 발생: "0이 되면 전환" 같은 조건이 소모 후 상태를 본다
            ev = Event("consumed", unit=unit, resource=res, keyword=res[1] if kind != "field" else None,
                       amount=-delta, source=source,
                       source_skill=frame.action.skill if frame and frame.action else None)
            self.emit(battle, "consumed", ev=ev)
        return actual

    def _overflow(self, unit: Unit, res: tuple, delta: float) -> float:
        if res[0] == "value":
            mx = self.prop(res[1], "max")
        elif res[0] == "status":
            mx = self.prop(res[1], f"{res[2]}_max")
        else:
            return 0
        return 0 if mx is None else max(0, self.get(unit, res) + delta - float(mx))

    def apply_status(self, battle: Battle, unit: Unit, kw: str, potency: float, count: float | None, *,
                     source: Unit | None, frame: Frame | None = None):
        s = unit.statuses.get(kw)
        if s is None:
            s = unit.statuses[kw] = Status(potency=0, count=0)
            count = 1 if count is None else count
        else:
            count = 0 if count is None else count
        pmax, cmax = self.prop(kw, "potency_max"), self.prop(kw, "count_max")
        s.potency = s.potency + potency if pmax is None else min(float(pmax), s.potency + potency)
        s.count = s.count + count if cmax is None else min(float(cmax), s.count + count)
        ev = Event("applied", unit=unit, resource=("status", kw, "potency"), keyword=kw, amount=potency,
                   source=source, source_skill=frame.action.skill if frame and frame.action else None)
        self.emit(battle, "applied", ev=ev)

    def burst(self, battle: Battle, unit: Unit, kw: str, *, source: Unit | None, frame: Frame | None = None):
        s = unit.statuses.get(kw)
        if s is None or s.potency <= 0:
            return 0
        ev = Event("burst", unit=unit, resource=("status", kw, "potency"), keyword=kw, amount=s.potency,
                   source=source, source_skill=frame.action.skill if frame and frame.action else None)
        self.emit(battle, "burst", ev=ev)
        pct = ev.mods["stagger_damage_pct"]
        if frame is not None and frame.action is not None:
            pct += frame.action.mods["stagger_damage_pct"]
        dmg = ev.amount * (1 + pct / 100)
        unit.stagger_damage += dmg
        battle.log.append(("burst", unit.name, kw, ev.amount, pct, dmg))
        return dmg

    def convert(self, battle: Battle, su: Unit, sres: tuple, du: Unit, dres: tuple):
        """A 의 수치를 B 로 옮기고 A 를 제거 (docs/07 §4 [확인 필요]). 0 이어도 전환한다 (R4)."""
        if sres[0] == "value" and sres[1] not in su.values:
            return
        v = self.get(su, sres)
        if sres[0] == "value":
            su.values.pop(sres[1], None)
        else:
            self.change(battle, su, sres, -v, source=None)
        if dres[0] == "value":
            du.values.setdefault(dres[1], 0)
        if v > 0:
            self.change(battle, du, dres, v, source=None)

    def damage(self, unit: Unit, amt: float, action: Action | None):
        absorbed = min(unit.shield, amt)
        unit.shield -= absorbed
        unit.hp = max(0, unit.hp - (amt - absorbed))
        unit.damage_taken += amt
        if action is not None:
            action.damage += amt

    # ── 참조 해석 ──
    def resolve_kw(self, name: str, f: Frame) -> str:
        return f.aliases.get(name, name)

    def _unit_root(self, root: str, f: Frame) -> Unit | None:
        if root in f.locals and isinstance(f.locals[root], Unit):
            return f.locals[root]
        return {"self": f.self_, "target": f.target, "attacker": f.attacker,
                "it": f.locals.get("it")}.get(root)

    def resolve_ref(self, r: A.Ref, f: Frame) -> tuple[Unit, tuple]:
        p = r.path
        unit = self._unit_root(p[0], f)
        if unit is None:
            raise RuntimeError_(f"{'.'.join(p)}: 유닛 없음 (line {r.line})")
        name = p[1]
        if name in UNIT_FIELDS:
            return unit, ("field", name)
        kw = self.resolve_kw(name, f)
        if len(p) == 3:
            return unit, ("status", kw, p[2])
        return unit, ("value", kw)

    def eval(self, e, f: Frame):
        match e:
            case None:
                return None
            case A.Num():
                return e.value
            case A.Str():
                return e.value
            case A.Name():
                return e.value
            case A.SkillRef():
                return (e.slot, e.variant)
            case A.ListExpr():
                return [self.eval(i, f) for i in e.items]
            case A.Ref():
                return self._eval_ref(e, f)
            case A.Unary():
                v = self.eval(e.operand, f)
                return (not self.truthy(v)) if e.op == "not" else -v
            case A.BinOp():
                if e.op == "and":
                    return self.truthy(self.eval(e.left, f)) and self.truthy(self.eval(e.right, f))
                if e.op == "or":
                    return self.truthy(self.eval(e.left, f)) or self.truthy(self.eval(e.right, f))
                a, b = self.eval(e.left, f), self.eval(e.right, f)
                return {"+": lambda: a + b, "-": lambda: a - b, "*": lambda: a * b,
                        "/": lambda: a / b if b else 0, ">=": lambda: a >= b, "<=": lambda: a <= b,
                        "==": lambda: a == b, "!=": lambda: a != b, ">": lambda: a > b, "<": lambda: a < b}[e.op]()
            case A.Is():
                subj = self.eval(e.subject, f)
                if isinstance(subj, Unit):
                    c = getattr(e.category, "value", None)
                    return {"ally": subj.side == f.self_.side, "enemy": subj.side != f.self_.side,
                            "main_target": f.action is not None and subj is f.action.target}.get(c, False)
                return self._is(subj, e.category)
            case A.Call():
                return self._call(e, f)
        raise RuntimeError_(f"평가할 수 없는 식: {e!r}")

    def _eval_ref(self, r: A.Ref, f: Frame):
        p = r.path
        if p[0] in f.locals and (len(p) == 1 or not isinstance(f.locals[p[0]], Unit)):
            return f.locals[p[0]]
        if p[0] in f.locals:                                   # 지역 변수에 담긴 유닛의 필드
            return self.get(*self.resolve_ref(r, f))
        if p[0] in ("self", "target", "attacker", "it"):
            unit = self._unit_root(p[0], f)
            if len(p) == 1:
                return unit
            if unit is None:
                return 0
            return self.get(*self.resolve_ref(r, f))
        if p[0] == "allies":
            return f.battle.allies_of(f.self_)
        if p[0] == "enemies":
            return f.battle.enemies_of(f.self_)
        if p[0] == "skill":
            if f.ev is not None and f.ev.source_skill is not None and f.action is None:
                return f.ev.source_skill
            return f.action.skill if f.action else (f.ev.source_skill if f.ev else None)
        if p[0] == "ev":
            ev = f.ev
            return {"amount": ev.amount, "source": ev.source_skill, "overflow": ev.overflow}[p[1]] if ev else 0
        if p[0] == "team":
            return {"max_resonance": f.battle.max_resonance, "absolute_resonance": f.battle.absolute_resonance}[p[1]]
        if p[0] == "coin":
            a = f.action
            c = a.coin
            return {"final_damage": a.coin_damage.get(c, 0), "power": a.coin_power.get(c, 0),
                    "index": c, "is_head": a.coin_heads.get(c, False)}[p[1]]
        if len(p) == 1:                     # 키워드 이름 (has 등의 인자)
            return self.resolve_kw(p[0], f)
        raise RuntimeError_(f"알 수 없는 참조 {'.'.join(p)} (line {r.line})")

    @staticmethod
    def _is(subject, cat) -> bool:
        if not isinstance(subject, SkillData):
            return False
        if isinstance(cat, A.SkillRef):
            return subject.key == (cat.slot, cat.variant)
        c = cat.value
        basic_attack = subject.slot in ("S1", "S2", "S3")
        return {
            "basic_attack": basic_attack,
            "basic_skill": basic_attack or subject.slot == "DEF",
            "attack": subject.atk_type in ("참격", "관통", "타격"),
            "defense": subject.slot == "DEF",
            "ego": subject.slot == "EGO",
            "counter": subject.atk_type == "반격",
            "evade": subject.atk_type == "회피",
            "guard": subject.atk_type in ("방어", "가드"),
            "slash": subject.atk_type == "참격",
            "pierce": subject.atk_type == "관통",
            "blunt": subject.atk_type == "타격",
        }.get(c, False)

    def _call(self, c: A.Call, f: Frame):
        fn = c.func
        if fn in ("pick", "count_of"):
            group = self.eval(c.args[0], f) or []
            cond = c.kwargs.get("where") if fn == "pick" else (c.args[1] if len(c.args) > 1 else None)
            out = [u for u in group if cond is None or self.truthy(self.eval(cond, f.child(locals={**f.locals, "it": u})))]
            if fn == "count_of":
                return len(out)
            by, prefer = c.kwargs.get("by"), c.kwargs.get("prefer")
            desc = self.truthy(self.eval(c.kwargs["desc"], f)) if "desc" in c.kwargs else False

            def sort_key(u):
                fu = f.child(locals={**f.locals, "it": u})
                pref = 0 if prefer is None or self.truthy(self.eval(prefer, fu)) else 1
                if by is None:
                    v = u.formation
                elif isinstance(by, A.Ref) and len(by.path) == 1 and by.path[0] in UNIT_FIELDS:
                    v = getattr(u, by.path[0])
                else:
                    v = self.eval(by, fu)
                return (pref, -v if desc else v)
            out.sort(key=sort_key)
            n = int(self.eval(c.kwargs["n"], f)) if "n" in c.kwargs else len(out)
            out = out[:n]
            return out[0] if n == 1 and out else (None if n == 1 else out)
        args = [self.eval(a, f) for a in c.args]
        match fn:
            case "min":
                return min(args)
            case "max":
                return max(args)
            case "floor":
                return math.floor(args[0])
            case "ceil":
                return math.ceil(args[0])
            case "per":
                return math.floor(args[0] / args[1]) if args[1] else 0
            case "pct":
                return args[0] * args[1] / 100
            case "has":
                return isinstance(args[0], Unit) and args[0].has(args[1])
            case "has_affiliation":
                return isinstance(args[0], Unit) and args[1] in args[0].affiliations
            case "keyword_identity":
                return isinstance(args[0], Unit) and args[1] in args[0].keyword_tags
            case "has_kind":
                return isinstance(args[0], Unit) and any(
                    self.kind(k) == args[1] and (s.count > 0 or s.potency > 0) for k, s in args[0].statuses.items())
        raise RuntimeError_(f"함수 {fn}")

    @staticmethod
    def truthy(v) -> bool:
        return bool(v)

    @staticmethod
    def _units(v) -> list[Unit]:
        if v is None:
            return []
        return v if isinstance(v, list) else [v]

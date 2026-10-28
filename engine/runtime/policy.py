"""탐색 정책 P1: 롤아웃 (docs/08 §2).

목표 = 적 처치까지 걸리는 턴 수 + 생존 패널티 (낮을수록 좋음, 사용자 확인 2026-10-28).
후보 행동마다 K 회 × H 턴(기본 4)을 기준 정책으로 굴려 평균 목표값이 가장 낮은 것을 고른다.
- 결정화: 롤아웃마다 남은 덱을 다시 섞음 (플레이어는 덱 순서를 모름, 구성만 앎)
- 공통 난수: 같은 k 번째 롤아웃은 후보끼리 같은 시드 → 후보 간 차이의 분산 감소
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from engine.params import DEFAULT, Params

from .interp import Runtime
from .state import Battle, Unit
from .turn import DEF, Choice, Policy, battle_over, policy_highest, resolve_turn, run_turn



@dataclass
class Objective:
    """처치 턴 수 (분수: 마지막 턴에 남은 체력 비율만큼) + 아군 사망·체력 손실 패널티."""
    dead_penalty: float = 10.0          # 아군 1명 사망 = 10턴
    hp_loss_penalty: float = 2.0        # 아군 전체 체력 비율 손실 1.0 (한 명분) = 2턴
    cap: float = 50.0                   # 피해가 0 이면 처치 불가 → 상한

    def enemy_hp(self, b: Battle) -> float:
        return sum(max(0, u.hp) for u in b.units if u.side == "enemy")

    def survival(self, b: Battle, side: str, start_ratio: dict) -> float:
        allies = [u for u in b.units if u.side == side]
        dead = sum(1 for u in allies if u.hp <= 0 < start_ratio[u.name])
        lost = sum(max(0.0, start_ratio[u.name] - u.hp_ratio) for u in allies if u.hp > 0)
        return self.dead_penalty * dead + self.hp_loss_penalty * lost


def legal_choices(rt: Runtime, battle: Battle, u: Unit) -> list[Choice]:
    """칸 0/1 (같은 카드면 하나만) + 수비 스킬 × 살아 있는 적.
    수비 스킬은 공격하지 않아도 [전투 시작시] 효과나 교체(#204 가호 100 → S3 variant 1)로 의미가 있을 수 있어 항상 후보."""
    # 대상 = (적, 주사위). 주사위마다 합 가로채기 여부가 달라서 따로 후보
    targets = [(e.name, d) for e in battle.enemies_of(u) if e.hp > 0 for d in range(e.dice if e.pattern else 1)]
    cards: list = []
    for i, key in enumerate(u.hand):
        if key not in [u.hand[j] for j in cards]:
            cards.append(i)
    if DEF in u.skills:
        cards.append("DEF")
    return [Choice(c, t, d) for c in cards for t, d in targets]


def determinize(battle: Battle, rng: random.Random) -> Battle:
    b = battle.clone()
    for u in b.units:
        if u.hand is not None:
            rng.shuffle(u.deck)
    return b


@dataclass
class RolloutPolicy:
    """Policy 호환 (battle, unit) → Choice. 선택 단계(begin_turn 직후) 상태에서 호출된다.
    P1: 단독 인격 기준 — 같은 턴 다른 아군은 롤아웃 안에서 기준 정책으로 고른다 (팀 결합 선택은 P3)."""
    rt: Runtime
    horizon: int | None = 4             # None = 처치(또는 max_turns)까지 끝까지 굴림 — 큰 한 방 뒤 외삽 오차 없음
    rollouts: int = 32
    max_turns: int = 30
    base: Policy = policy_highest
    objective: Objective = field(default_factory=Objective)
    seed: int = 0
    params: Params = DEFAULT
    last: dict = field(default_factory=dict)        # 마지막 결정의 후보별 평균 목표값 (설명·노하우 수집용)
    _calls: int = 0

    def __call__(self, battle: Battle, u: Unit) -> Choice:
        cands = legal_choices(self.rt, battle, u)
        if len(cands) == 1:
            self.last = {}
            return cands[0]
        self._calls += 1
        scores = {}
        for c in cands:
            tot = 0.0
            for k in range(self.rollouts):
                rng = random.Random((self.seed, self._calls, k).__hash__())
                tot += self._rollout(battle, u.name, c, rng)
            scores[c] = tot / self.rollouts
        self.last = scores
        return min(cands, key=lambda c: scores[c])

    def _rollout(self, battle: Battle, me: str, c: Choice, rng: random.Random) -> float:
        b = determinize(battle, rng)
        obj = self.objective
        side = next(u.side for u in b.units if u.name == me)
        start = {u.name: u.hp_ratio for u in b.units}
        actors = [u for u in b.units if u.hand is not None and u.hp > 0 and u.stagger_until is None]
        choices = {u.name: c if u.name == me else self.base(b, u) for u in actors}
        t, hp_before = 1, obj.enemy_hp(b)
        log = resolve_turn(self.rt, b, rng, actors, choices, self.params)
        while True:
            rem = obj.enemy_hp(b)
            # 행동 피해 합 (체력 0 에서 잘리지 않은 값) — 처치 턴 안의 분수 계산용. 0 이면 체력 감소분
            dmg = max(sum(a.damage for a in log.actions), hp_before - rem)
            if rem <= 0:                    # t 턴 안에서 처치: 그 턴 시작 체력 / 그 턴 피해 만큼만 사용
                kill = t - 1 + (hp_before / dmg if dmg > 0 else 1)
                break
            if battle_over(b) or t >= (self.horizon or self.max_turns):   # 지평 밖: 마지막 턴 피해 속도로 남은 체력 추정
                kill = t + (rem / dmg if dmg > 0 else obj.cap)
                break
            hp_before = rem
            log = run_turn(self.rt, b, rng, self.base, self.params)
            t += 1
        return min(obj.cap, kill) + obj.survival(b, side, start)


# ── M3: 팀 결합 선택 ─────────────────────────────────────────────────────────
def team_choices(rt: Runtime, battle: Battle, u: Unit) -> list[Choice]:
    """legal_choices 를 대상 주사위 기준으로 줄임: 합이 되는 주사위(내 속도 ≥) 는 각각, 일방 공격이 되는 주사위는 대표 하나.
    공격하지 않는 수비 스킬(방어·회피·반격)은 대상과 무관 → 하나."""
    from .turn import attacks
    speeds = {(it.unit, it.die): it.speed for it in battle.intents}
    out, seen = [], set()
    for c in legal_choices(rt, battle, u):
        key = DEF if c.card == "DEF" else u.hand[c.card]
        key = u.skill_override.get(key, key)
        if not attacks(rt, u, key):
            sig = (c.card, "passive")
        else:
            sp = speeds.get((c.target, c.die))
            sig = (c.card, (c.target, c.die)) if sp is not None and u.speed >= sp else (c.card, "one-sided")
        if sig not in seen:
            seen.add(sig)
            out.append(c)
    return out


@dataclass
class TeamRolloutPolicy:
    """팀 결합 선택: 기준 정책으로 전원 선택 → 한 명씩 (나머지 고정) 후보를 롤아웃으로 비교해 교체 (좌표 상승, passes 회).
    목표 = 처치 턴 (분수) + 생존 패널티 (Objective). 아군 전멸이면 cap.
    run_turn 이 joint() 를 부른다. 단일 유닛 호출(__call__)도 지원."""
    rt: Runtime
    horizon: int | None = 3             # None = 끝까지
    rollouts: int = 8
    base: Policy = policy_highest
    objective: Objective = field(default_factory=Objective)
    passes: int = 1
    seed: int = 0
    max_turns: int = 30
    params: Params = DEFAULT
    last: dict = field(default_factory=dict)        # 유닛 → {Choice: 평균 목표값}
    _calls: int = 0

    def __call__(self, battle: Battle, u: Unit) -> Choice:
        actors = [x for x in battle.units if x.hand is not None and x.hp > 0 and x.stagger_until is None]
        return self.joint(battle, actors)[u.name]

    def joint(self, battle: Battle, actors: list[Unit]) -> dict:
        self._calls += 1
        current = {u.name: self.base(battle, u) for u in actors}
        self.last = {}
        for _ in range(self.passes):
            for u in actors:
                cands = team_choices(self.rt, battle, u)
                if current[u.name] not in cands:
                    cands = [current[u.name], *cands]
                if len(cands) <= 1:
                    continue
                scores = {}
                for c in cands:
                    trial = {**current, u.name: c}
                    tot = 0.0
                    for k in range(self.rollouts):
                        rng = random.Random((self.seed, self._calls, k).__hash__())
                        tot += self._sim(battle, trial, rng)
                    scores[c] = tot / self.rollouts
                self.last[u.name] = scores
                current[u.name] = min(cands, key=lambda c: scores[c])
        return current

    def _sim(self, battle: Battle, choices: dict, rng: random.Random) -> float:
        b = determinize(battle, rng)
        obj = self.objective
        side = "ally"
        start = {u.name: u.hp_ratio for u in b.units}
        actors = [u for u in b.units if u.name in choices]
        t, hp_before = 1, obj.enemy_hp(b)
        log = resolve_turn(self.rt, b, rng, actors, choices, self.params)
        allies = [u for u in b.units if u.side == side]
        while True:
            rem = obj.enemy_hp(b)
            names = {u.name for u in allies}
            dmg = max(sum(a.damage for a in log.actions if a.unit in names), hp_before - rem)
            if rem <= 0:
                kill = t - 1 + (hp_before / dmg if dmg > 0 else 1)
                break
            if all(u.hp <= 0 for u in allies):
                kill = obj.cap
                break
            if t >= (self.horizon or self.max_turns):
                kill = t + (rem / dmg if dmg > 0 else obj.cap)
                break
            hp_before = rem
            log = run_turn(self.rt, b, rng, self.base, self.params)
            t += 1
        return min(obj.cap, kill) + obj.survival(b, side, start)

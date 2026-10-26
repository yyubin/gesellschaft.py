"""전투 상태 모델 (docs/07 §2-3)."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

# charge 계열: 횟수가 0이어도 상태가 남고(위력 유지), 상태가 없을 때 횟수 증가로 생성된다 (docs/07 §4)
CHARGE_KINDS = {"charge", "special_charge"}


@dataclass
class Status:
    potency: float = 0
    count: float = 0


@dataclass
class SkillData:
    slot: str
    variant: int
    name: str
    sin: str | None
    atk_type: str              # 참격 / 관통 / 타격 / 방어 / 회피 / 반격
    base: int
    coin_power: int
    coins: int
    level: int                 # 공격 레벨 (level_text 앞 숫자)

    @property
    def key(self) -> tuple[str, int]:
        return (self.slot, self.variant)


@dataclass
class Unit:
    name: str
    identity_id: int | None = None
    side: str = "ally"                         # ally | enemy
    max_hp: float = 100
    hp: float = 100
    shield: float = 0
    sanity: float = 0
    has_sanity: bool = True                    # False = 정신력 없는 적 (정신력 피해 → 우울 피해)
    speed: float = 0
    def_level: float = 0
    formation: int = 0
    phys_res: dict = field(default_factory=lambda: {"참격": "보통", "관통": "보통", "타격": "보통"})
    sin_res: dict = field(default_factory=dict)
    affiliations: set = field(default_factory=set)
    keyword_tags: set = field(default_factory=set)     # 인격 키워드 분류 ("충전 인격" 판정)
    skills: dict = field(default_factory=dict)         # (slot, variant) → SkillData
    statuses: dict = field(default_factory=dict)       # 이름 → Status
    values: dict = field(default_factory=dict)         # 수치형 키워드 이름 → 값
    stagger_damage: float = 0                          # M1: 받은 흐트러짐 피해 누적 (지표)
    damage_taken: float = 0
    skill_override: dict = field(default_factory=dict)  # this_turn 스킬 교체 {(slot,var): (slot,var)}

    @property
    def hp_ratio(self) -> float:
        return self.hp / self.max_hp if self.max_hp else 0

    # ── 키워드 접근 ──
    def status(self, name: str) -> Status | None:
        return self.statuses.get(name)

    def has(self, name: str) -> bool:
        s = self.statuses.get(name)
        if s is not None and (s.count > 0 or s.potency > 0):
            return True
        return name in self.values


@dataclass
class Battle:
    units: list[Unit]
    turn: int = 1
    sin_owned: dict = field(default_factory=dict)      # 팀 E.G.O 자원 (보유 조건)
    resonance: dict = field(default_factory=dict)      # 이번 턴 공명 수 (공명 조건)
    max_resonance: int = 0                             # 최대 공명 수 [확인 필요] 정의
    absolute_resonance: int = 0
    force_passives: bool = False                       # 분석용: 패시브 조건 무시
    # 실행기 내부 상태
    limits: dict = field(default_factory=dict)         # (block id, period key, target) → 횟수
    budgets: dict = field(default_factory=dict)        # (unit, budget, turn) → 사용량
    accum: dict = field(default_factory=dict)          # every: (block id, unit) → 누적량
    pending: list = field(default_factory=list)        # next_turn: (turn, callable)
    log: list = field(default_factory=list)

    def allies_of(self, u: Unit) -> list[Unit]:
        return sorted((x for x in self.units if x.side == u.side), key=lambda x: x.formation)

    def enemies_of(self, u: Unit) -> list[Unit]:
        return [x for x in self.units if x.side != u.side]

    def clone(self) -> "Battle":
        return copy.deepcopy(self)

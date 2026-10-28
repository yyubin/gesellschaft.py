"""적 시나리오 (docs/08 M3). 실제 적 데이터가 없어 고정 위력 스킬로 구성한 단순 적.

보스: 체력 큼, 속도 주사위 여러 개, 스킬을 순서대로 순환, 대상은 살아 있는 아군 중 균등 랜덤 (선택 전 공개).
수치는 르누아르 3인(#202~#204: 체력 ≈ 280, 방어 레벨 65, 스킬 레벨 67~69)과 비슷한 체급으로 맞춘 기본값.
"""
from __future__ import annotations

from .state import SkillData, Unit


def boss(name: str = "보스", *, hp: float = 1200, dice: int = 2, speed: tuple = (2, 6), level: int = 67,
         def_level: int = 65, stagger_lines: tuple = (70, 40), res: str = "보통",
         skills: list | None = None, pattern: list | None = None) -> Unit:
    """skills: [(이름, 공격 유형, 속성, 기본 위력, 코인 위력, 코인 수)]. pattern: 스킬 이름 순환 목록."""
    skills = skills or [
        ("베기", "참격", "분노", 5, 4, 2),
        ("찌르기", "관통", "색욕", 4, 3, 3),
        ("내려찍기", "타격", "우울", 8, 4, 3),
    ]
    data = {}
    for i, (nm, atk, sin, base, cp, coins) in enumerate(skills, start=1):
        data[(f"E{i}", 0)] = SkillData(f"E{i}", 0, nm, sin, atk, base=base, coin_power=cp, coins=coins, level=level)
    by_name = {sd.name: k for k, sd in data.items()}
    pat = [by_name[p] for p in pattern] if pattern else [("E1", 0), ("E2", 0), ("E1", 0), ("E3", 0)]
    return Unit(name=name, side="enemy", max_hp=hp, hp=hp, def_level=def_level, has_sanity=False,
                speed_min=speed[0], speed_max=speed[1], dice=dice, pattern=pat, skills=data,
                stagger_lines=list(stagger_lines), phys_res={"참격": res, "관통": res, "타격": res})

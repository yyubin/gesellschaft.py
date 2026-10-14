"""패시브 발동 조건: 보유 / 공명 (docs/rules 1장)."""
from collections import Counter
from typing import Iterable


def passive_active(cond_type: str | None, cond_sins: Iterable[str], cond_count: int | None,
                   owned: dict, selected_sins: Iterable[str | None]) -> bool:
    """
    owned: 팀 공용 E.G.O 자원 보유량 {속성: 개수} (턴 시작 시점)
    selected_sins: 이번 턴 팀 전체가 사용하는 스킬들의 죄악 속성 (속성 없는 스킬은 None)
    복수 속성 조건은 각 속성이 각각 N 이상.
    """
    sins = list(cond_sins)
    if not cond_type or not sins:
        return True                      # 조건 없는 패시브
    n = cond_count or 0
    if cond_type == "보유":
        return all(owned.get(s, 0) >= n for s in sins)
    if cond_type == "공명":
        cnt = Counter(s for s in selected_sins if s)
        return all(cnt[s] >= n for s in sins)
    raise ValueError(f"unknown cond_type: {cond_type}")


def gain_resources(owned: dict, used_sins: Iterable[str | None]) -> dict:
    """스킬 사용 시 해당 속성 자원 +1 (속성 없는 스킬은 자원 없음)."""
    out = dict(owned)
    for s in used_sins:
        if s:
            out[s] = out.get(s, 0) + 1
    return out

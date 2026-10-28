"""LPDL `hook 이름(...)` 의 Python 구현 (docs/07 §2). 시그니처: fn(rt, frame, *args).

고정 어휘로 표현하지 못한 효과만 여기에 둔다. 단독 vs 허수아비에서 결과에 영향이 없는 훅은 NOOP 으로
명시 등록해 'hook-missing' 로그와 구분한다 (팀·합·피격이 들어오는 M3 에서 구현).
"""
from __future__ import annotations

import math

from engine.params import DEFAULT

VIBRATION = "진동"
NAIL = "약속된 못"


def _target(f):
    return f.target or (f.action.target if f.action else None)


def _vib_nail(rt, u) -> float:
    s = u.statuses.get(rt.redirect(u, VIBRATION))
    return (s.potency if s else 0) + u.values.get(NAIL, 0)


# ── #204 오트쿠튀르::르누아르 브랜드 매니저 ─────────────────────────────────────
def coin_power_per_all_targets_vibration_nail_sum(rt, f, per, cap):
    """[사용시] 모든 공격 대상의 (진동 위력 + 약속된 못) 합 per 당 코인 위력 +1 (최대 cap). 대상 1명 기준."""
    t = _target(f)
    if t is not None and f.action is not None:
        f.action.mods["coin_power"] += min(math.floor(_vib_nail(rt, t) / per), cap)


def vibration_preserve_burst_sloth_damage(rt, f, base, step, cap):
    """진동 - 보존 폭발 시 위력의 (base + 폭발 일으킨 유닛의 충전 계열 위력 × step)% 나태 피해 (최대 cap%)."""
    ev, u = f.ev, f.self_
    if ev is None:
        return
    src = ev.source
    p = 0.0
    if src is not None:
        p = max((s.potency for k, s in src.statuses.items() if k == "충전" or "충전" in rt.ancestors(k)), default=0)
    amt = ev.amount * min(base + p * step, cap) / 100
    amt *= 1 + DEFAULT.res_delta[u.sin_res.get("나태", "보통")]
    rt.damage(u, amt, rt.current_action)


def mark_skill_as_charge_consuming(rt, f):
    """이 스킬을 '충전 횟수를 소모하는 스킬'로 표시. 보존 신역 보유자가 있으면 적이 받는 피해 +10%
    (르누아르 아군 스킬이면 2배) — charge_consuming_skill_taken_damage_bonus 의 효과를 여기서 적용.
    근사: 표시된 뒤의 코인부터 적용 (못 부여는 코인 적중 시라 그 코인 피해는 이미 계산됨). [확인 필요]"""
    a = rt.current_action
    if a is None or getattr(a, "charge_consuming", False):
        return
    a.charge_consuming = True
    holders = [u for u in f.battle.units if u.side == a.actor.side and u.values.get("보존 신역", 0) > 0]
    if holders:
        a.mods["damage_pct"] += 10 * (2 if "르누아르" in a.actor.affiliations else 1)


def _noop(rt, f, *args):
    pass


NOOP = [
    "charge_consuming_skill_taken_damage_bonus",   # mark_skill_as_charge_consuming 이 적용
    "sanctuary_ally_shield_on_skill_use",          # 아군 보호막 — 피격 없는 허수아비전에서 무의미
    "speed_floor", "vanish_if_identity_absent", "all_coins_unbreakable", "stagger_immune",
    "no_defense_skill_trigger", "no_external_reuse", "target_hp_floor_until_skill_end",
    "treat_as_attack_skill_when_replacing_defense", "trigger_on_one_sided_attack",
]

HOOKS = {
    "coin_power_per_all_targets_vibration_nail_sum": coin_power_per_all_targets_vibration_nail_sum,
    "vibration_preserve_burst_sloth_damage": vibration_preserve_burst_sloth_damage,
    "mark_skill_as_charge_consuming": mark_skill_as_charge_consuming,
    **{n: _noop for n in NOOP},
}

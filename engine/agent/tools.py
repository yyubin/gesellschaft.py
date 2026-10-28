"""에이전트 도구: 계획·정책 평가, 인격 spec 원문, 상태 → 전투 (docs/08 §3). 숫자는 전부 여기서 나온다."""
from __future__ import annotations

import random
import re
from collections import Counter
from pathlib import Path

from engine.runtime import Battle, deal_hand, run_turn, stage_enter
from engine.runtime.interp import Runtime
from engine.runtime.loader import SPEC_DIRS, dummy, load_unit
from engine.runtime.state import Status
from engine.runtime.turn import DECK, Policy, battle_over

TRACE_TURNS = 8


def play_game(rt: Runtime, battle: Battle, policy: Policy, seed: int, max_turns: int, observe=None):
    """한 판: 덱 섞기 → 전장 등장 → 처치 또는 max_turns. 반환 (처치 턴 | max_turns+1, 턴별 사용 스킬, 턴별 관측)."""
    rng = random.Random(seed)
    b = battle.clone()
    for u in b.units:
        if u.side == "ally":
            deal_hand(u, rng)
    stage_enter(rt, b)
    skills, obs = [], []
    for t in range(1, max_turns + 1):
        log = run_turn(rt, b, rng, policy)
        skills.append([a.skill for a in log.actions])
        if observe:
            obs.append(observe(b))
        if battle_over(b):
            return t, skills, obs
    return max_turns + 1, skills, obs


def skill_name(k) -> str:
    return k[0] + (f"v{k[1]}" if k[1] else "")


def _observe(b: Battle) -> dict:
    me, foe = b.units[0], next(u for u in b.units if u.side == "enemy")
    row = {"적 체력": foe.hp}
    for k, s in me.statuses.items():
        row[f"{k}.potency"], row[f"{k}.count"] = s.potency, s.count
    for k, v in me.values.items():
        row[k] = v
    for k, s in foe.statuses.items():
        row[f"적 {k}.potency"], row[f"적 {k}.count"] = s.potency, s.count
    return row


def evaluate(rt: Runtime, battle: Battle, make_policy, *, games: int = 300, seed: int = 0,
             max_turns: int = 20) -> dict:
    """make_policy() 로 판마다 새 정책 (규칙 발동 횟수 집계 위해). 같은 seed 면 정책끼리 같은 난수열 (공통 난수)."""
    turns, usage, by_turn, traces = [], Counter(), {}, {}
    fired = Counter()
    for g in range(games):
        pol = make_policy()
        t, skills, obs = play_game(rt, battle, pol, seed + g, max_turns, _observe)
        turns.append(t)
        fired.update(getattr(pol, "fired", {}))
        for i, row in enumerate(skills):
            for k in row:
                usage[skill_name(k)] += 1
                if i < TRACE_TURNS:
                    by_turn.setdefault(i + 1, Counter())[skill_name(k)] += 1
        for i, row in enumerate(obs[:TRACE_TURNS]):
            for k, v in row.items():
                traces.setdefault(k, [[] for _ in range(TRACE_TURNS)])[i].append(v)
    n, tot = len(turns), sum(usage.values()) or 1
    dist = Counter(turns)
    return {
        "games": n,
        "mean_kill_turn": round(sum(turns) / n, 3),
        "se": round(_se(turns), 3),                 # 평균의 표준오차
        "turns": turns,                             # 판별 처치 턴 (같은 seed 끼리 짝지은 비교용, LLM 에게는 안 보냄)
        "kill_turn_dist": {str(t): round(c / n, 3) for t, c in sorted(dist.items())},
        "skill_share": {k: round(v / tot, 3) for k, v in sorted(usage.items())},
        "skill_share_by_turn": {t: {k: round(v / sum(c.values()), 2) for k, v in sorted(c.items())}
                                for t, c in sorted(by_turn.items())},
        # 턴 종료 후 평균 (그 턴까지 살아 있는 판만)
        "trace": {k: [round(sum(v) / len(v), 2) if v else None for v in rows] for k, rows in sorted(traces.items())},
        "rule_fired": {("default" if k == -1 else f"rules[{k}]"): v for k, v in sorted(fired.items())},
    }


def _se(xs: list) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return (sum((x - m) ** 2 for x in xs) / (n - 1) / n) ** 0.5


def paired(a: dict, b: dict) -> tuple[float, float]:
    """같은 seed 로 돈 두 평가의 차이 (a − b) 평균과 표준오차. 공통 난수라 개별 se 보다 훨씬 작다."""
    d = [x - y for x, y in zip(a["turns"], b["turns"])]
    return round(sum(d) / len(d), 3), round(_se(d), 3)


def evaluate_set(rt: Runtime, battles: list[Battle], make_policy, *, games: int = 300, seed: int = 0) -> dict:
    """여러 시나리오(적 체력 등)에서 평가해 합친다 — 한 시나리오에 맞춘 규칙(과적합)을 걸러내기 위함.
    mean_kill_turn = 시나리오 평균, turns = 시나리오별 판을 이어 붙임 (같은 순서라 paired 비교 가능).
    분포·스킬 비율·trace 는 가운데 시나리오 기준 (detail_scenario)."""
    rs = [evaluate(rt, b, make_policy, games=games, seed=seed) for b in battles]
    if len(rs) == 1:
        return rs[0]
    labels = [f"hp {next(u for u in b.units if u.side == 'enemy').max_hp:g}" for b in battles]
    mid = len(rs) // 2
    out = dict(rs[mid])
    turns = [t for r in rs for t in r["turns"]]
    fired = Counter()
    for r in rs:
        fired.update(r["rule_fired"])
    out.update({
        "mean_kill_turn": round(sum(r["mean_kill_turn"] for r in rs) / len(rs), 3),
        "se": round(sum(r["se"] ** 2 for r in rs) ** 0.5 / len(rs), 3),
        "turns": turns,
        "by_scenario": {lab: r["mean_kill_turn"] for lab, r in zip(labels, rs)},
        "detail_scenario": labels[mid],
        "rule_fired": dict(fired),
    })
    return out


# ── 인격 spec 원문 ───────────────────────────────────────────────────────────
def spec_source(rt: Runtime, identity_id: int) -> str:
    """인격 LPDL 원문 + 그 인격이 쓰는 비표준 키워드 정의 (examples/keyword)."""
    files = [p for d in SPEC_DIRS for p in Path(d).rglob("*.lpdl")]
    ident = next((p.read_text(encoding="utf-8") for p in files
                  if re.search(rf"^identity {identity_id}\b", p.read_text(encoding="utf-8"), re.M)), None)
    if ident is None:
        raise KeyError(f"identity {identity_id} 의 LPDL spec 없음")
    parts = [ident]
    for p in files:
        if "keywords" in p.parts:                   # 표준 키워드는 프롬프트에 요약으로
            continue
        text = p.read_text(encoding="utf-8")
        names = re.findall(r'^keyword "([^"]+)"', text, re.M)
        if names and any(n in ident for n in names) and text not in parts:
            parts.append(text)
    return "\n\n".join(parts)


# ── 상태 → 전투 (조언 모드) ──────────────────────────────────────────────────
def battle_from_state(state: dict) -> Battle:
    """조언 입력 JSON → 선택 단계 직전 상태.
    {"identity": 202, "turn": 3, "hand": ["S1", "S3"], "used_since_shuffle": ["S2", "S1"],
     "self": {"hp": 200, "sanity": 10, "statuses": {"보존": [3, 12]}, "values": {"코트": 50}},
     "enemy": {"hp": 300, "statuses": {"진동": [6, 2]}}, "overrides": {"S3": 1}, "sin_owned": {"나태": 3}}
    덱 = 6장 − 손패 − 셔플 이후 사용한 카드 (순서는 모름 → 롤아웃에서 다시 섞음)."""
    me = load_unit(state["identity"])
    s = state.get("self", {})
    for k in ("hp", "sanity", "shield", "speed"):
        if k in s:
            setattr(me, k, s[k])
    me.statuses = {k: Status(*v) for k, v in s.get("statuses", {}).items()}
    me.values = dict(s.get("values", {}))
    me.hand = [(c, 0) for c in state["hand"]]
    # 이번 턴 스킬 교체 (턴 시작 효과로 이미 바뀐 것, 예: {"S3": 1} = S3 → S3 variant 1)
    me.skill_override = {(c, 0): (c, v) for c, v in state.get("overrides", {}).items()}
    full = Counter((slot, 0) for slot, n in DECK for _ in range(n))
    full.subtract(Counter((c, 0) for c in state["hand"] + state.get("used_since_shuffle", [])))
    if any(v < 0 for v in full.values()):
        raise ValueError("손패 + 사용한 카드가 덱 구성(S1×3, S2×2, S3×1)을 넘음")
    me.deck = list(full.elements())
    e = state.get("enemy", {})
    foe = dummy(hp=e.get("hp", 10**9))
    foe.statuses = {k: Status(*v) for k, v in e.get("statuses", {}).items()}
    b = Battle([me, foe], turn=state.get("turn", 1))
    b.sin_owned = dict(state.get("sin_owned", {}))
    return b

"""정책 비교: 인격 1명 vs 체력 HP 허수아비, 처치 턴 수 분포 (docs/08 P1).

    python scripts/policy_eval.py 202 --hp 300 --games 100 [--horizon 4 --rollouts 32]
"""
import argparse
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.runtime import Battle, RolloutPolicy, deal_hand, policy_highest, policy_top, run_turn, stage_enter  # noqa: E402
from engine.runtime.loader import dummy, load_runtime, load_unit  # noqa: E402
from engine.runtime.turn import battle_over, policy_highest as _hi  # noqa: E402


def play_game(rt, battle, policy, seed, max_turns, on_decision=None):
    rng = random.Random(seed)
    b = battle.clone()
    for u in b.units:
        if u.side == "ally":
            deal_hand(u, rng)
    stage_enter(rt, b)
    skills = []
    for t in range(1, max_turns + 1):
        log = run_turn(rt, b, rng, policy)
        skills.append([a.skill for a in log.actions])
        if on_decision:
            on_decision(t, log)
        if battle_over(b):
            return t, skills
    return max_turns + 1, skills


def name(k):
    return k[0] + (f"v{k[1]}" if k[1] else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("identity", type=int)
    ap.add_argument("--hp", type=float, default=300)
    ap.add_argument("--games", type=int, default=100)
    ap.add_argument("--max-turns", type=int, default=20)
    ap.add_argument("--horizon", type=int, default=4, help="0 = 처치까지 끝까지 굴림")
    ap.add_argument("--base", type=Path, default=None, help="롤아웃 기준 정책으로 쓸 계획 JSON (기본 highest)")
    ap.add_argument("--rollouts", type=int, default=32)
    ap.add_argument("--only", choices=["highest", "top", "rollout"], action="append")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rt = load_runtime()
    battle = Battle([load_unit(args.identity), dummy(hp=args.hp)])
    base = policy_highest
    if args.base is not None:
        import json
        from engine.agent import plan as P
        base = P.PlanPolicy(rt, P.Plan.from_dict(json.loads(args.base.read_text(encoding="utf-8"))))
    pols = {"highest": policy_highest, "top": policy_top,
            "rollout": RolloutPolicy(rt, horizon=args.horizon or None, rollouts=args.rollouts, base=base)}
    print(f"#{args.identity} vs 허수아비 체력 {args.hp:g}, {args.games}판 (같은 시드), 롤아웃 H={args.horizon or '끝까지'} "
          f"K={args.rollouts} 기준 정책={args.base.name if args.base else 'highest'}")
    for pname in args.only or pols:
        t0 = time.time()
        res = [play_game(rt, battle, pols[pname], g, args.max_turns) for g in range(args.games)]
        turns = [r[0] for r in res]
        dist = Counter(turns)
        usage = Counter(name(k) for _, sk in res for row in sk for k in row)
        tot = sum(usage.values())
        print(f"\n[{pname}] 평균 처치 턴 {sum(turns) / len(turns):.2f}  ({time.time() - t0:.0f}s)")
        print("  분포: " + ", ".join(f"{t}턴 {n / len(turns):.0%}" for t, n in sorted(dist.items())))
        print("  스킬: " + ", ".join(f"{k} {n / tot:.0%}" for k, n in sorted(usage.items())))


if __name__ == "__main__":
    main()

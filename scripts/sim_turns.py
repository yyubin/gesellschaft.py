"""M2: 인격 1명 × 여러 턴 vs 허수아비, 턴별 평균 자원·피해 (docs/07 §8).

    python scripts/sim_turns.py 202 --turns 6 --runs 2000 [--policy highest|top]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.runtime import Battle, mean_by_turn, policy_highest, policy_top, simulate_turns  # noqa: E402
from engine.runtime.loader import dummy, load_runtime, load_unit  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("identity", type=int)
    ap.add_argument("--turns", type=int, default=6)
    ap.add_argument("--runs", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--policy", choices=["highest", "top"], default="highest")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    rt = load_runtime()
    me = load_unit(args.identity)
    battle = Battle([me, dummy()])

    def observe(b):
        u, foe = b.units
        row = {"sanity": u.sanity}
        for k, s in u.statuses.items():
            row[f"{k}.위력"], row[f"{k}.횟수"] = s.potency, s.count
        for k, v in u.values.items():
            row[k] = v
        for k, s in foe.statuses.items():
            row[f"적 {k}.위력"], row[f"적 {k}.횟수"] = s.potency, s.count
        return row

    pol = {"highest": policy_highest, "top": policy_top}[args.policy]
    runs = simulate_turns(rt, battle, args.turns, runs=args.runs, seed=args.seed, policy=pol, observe=observe)
    keys = sorted({k for r in runs for row in r for k in row if k != "log"})
    print(f"#{args.identity} {me.name}  {args.runs}회 × {args.turns}턴, 정책 {args.policy} (턴 종료 후 평균)")
    print(f"{'':>14}" + "".join(f"{t + 1:>9}" for t in range(args.turns)))
    dmg = mean_by_turn(runs, lambda row: sum(a.damage for a in row["log"].actions))
    print(f"{'피해':>14}" + "".join(f"{x:9.1f}" for x in dmg))
    sh = mean_by_turn(runs, lambda row: row["log"].shields.get(me.name, 0))
    print(f"{'보호막(전투시작)':>14}" + "".join(f"{x:9.1f}" for x in sh))
    for k in keys:
        m = mean_by_turn(runs, lambda row: row.get(k, 0))
        print(f"{k:>14}" + "".join(f"{x:9.2f}" for x in m))
    skills = {}
    for r in runs:
        for t, row in enumerate(r):
            for a in row["log"].actions:
                skills.setdefault(t, {}).setdefault(a.skill, 0)
                skills[t][a.skill] += 1
    print("사용 스킬 비율")
    for t in range(args.turns):
        print(f"  {t + 1}턴: " + ", ".join(f"{s[0]}{'v' + str(s[1]) if s[1] else ''} {n / args.runs:.0%}"
                                           for s, n in sorted(skills.get(t, {}).items())))


if __name__ == "__main__":
    main()

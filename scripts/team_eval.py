"""M3: 팀 vs 보스 — 처치 턴, 생존(사망·체력), 합 승률, 스킬 비율 (docs/08 M3).

    python scripts/team_eval.py 202 203 204 --games 300 [--boss-hp 1200 --dice 2]
"""
import argparse
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.runtime import Battle, deal_hand, policy_highest, run_turn, stage_enter  # noqa: E402
from engine.runtime.loader import load_runtime, load_unit  # noqa: E402
from engine.runtime.scenarios import PRESETS, preset  # noqa: E402
from engine.runtime.turn import battle_over  # noqa: E402


def play(rt, battle, policy, seed, max_turns=30):
    rng = random.Random(seed)
    b = battle.clone()
    for u in b.units:
        if u.side == "ally":
            deal_hand(u, rng)
    stage_enter(rt, b)
    clashes, wins, usage, stag = 0, 0, Counter(), Counter()
    for t in range(1, max_turns + 1):
        log = run_turn(rt, b, rng, policy)
        for a in log.actions:
            u = next(x for x in b.units if x.name == a.unit)
            if u.side == "ally":
                usage[(u.name, a.skill[0] + (f"v{a.skill[1]}" if a.skill[1] else ""))] += 1
                if a.kind == "clash":
                    clashes += 1
                    wins += a.clash["winner"] == a.unit
        for e in b.log:
            if e[0] == "stagger":
                stag[e[1]] += 1
            elif e[0] == "use_skill":
                usage[(e[1], "심판!(추가)")] += 1
        b.log.clear()
        if battle_over(b):
            break
    allies = [u for u in b.units if u.side == "ally"]
    won = all(u.hp <= 0 for u in b.units if u.side == "enemy")
    return {"turns": t, "won": won, "dead": sum(u.hp <= 0 for u in allies),
            "hp": sum(max(0, u.hp) / u.max_hp for u in allies) / len(allies),
            "clashes": clashes, "clash_wins": wins, "usage": usage, "stagger": stag}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ids", type=int, nargs="+")
    ap.add_argument("--games", type=int, default=300)
    ap.add_argument("--preset", choices=sorted(PRESETS), default="hard")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    rt = load_runtime()
    team = [load_unit(i, formation=n) for n, i in enumerate(args.ids)]
    battle = Battle([*team, preset(args.preset)])
    rs = [play(rt, battle, policy_highest, g) for g in range(args.games)]
    n = len(rs)
    won = [r for r in rs if r["won"]]
    print(f"{' / '.join(u.name for u in team)} vs 보스 {args.preset}, {n}판, 정책 highest")
    print(f"  승리 {len(won) / n:.0%}, 처치 턴 평균 {sum(r['turns'] for r in won) / max(1, len(won)):.2f} (승리 판)")
    print(f"  아군 사망 평균 {sum(r['dead'] for r in rs) / n:.2f}, 남은 체력 평균 {sum(r['hp'] for r in rs) / n:.0%}")
    c = sum(r["clashes"] for r in rs)
    print(f"  합 {c / n:.1f}회/판, 아군 합 승률 {sum(r['clash_wins'] for r in rs) / max(1, c):.0%}")
    st = Counter()
    for r in rs:
        st.update(r["stagger"])
    print("  흐트러짐 횟수/판: " + ", ".join(f"{k} {v / n:.2f}" for k, v in st.most_common()))
    use = Counter()
    for r in rs:
        use.update(r["usage"])
    for u in team:
        tot = sum(v for (nm, _), v in use.items() if nm == u.name) or 1
        print(f"  {u.name}: " + ", ".join(f"{s} {v / tot:.0%}" for (nm, s), v in sorted(use.items()) if nm == u.name))


if __name__ == "__main__":
    main()

"""인격 덱 스킬의 합 승률 / 기대 피해 리포트 (스킬 텍스트 효과는 아직 미반영 — 순수 수치만).

사용법: python scripts/clash_report.py 204 [--uptie 4] [--enemy 4,4,3] [--sanity 0 45]
  --enemy 기본위력,코인위력,코인수  : 비교 대상 적 스킬
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.clash import ClashSide, clash  # noqa: E402
from engine.coins import expected_coin_powers  # noqa: E402
from engine.damage import HitContext, hit_damage  # noqa: E402

DB = Path(__file__).resolve().parent.parent / "data" / "gesellschaft.db"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("identity_id", type=int)
    ap.add_argument("--uptie", type=int, default=4)
    ap.add_argument("--enemy", default="4,4,3")
    ap.add_argument("--sanity", type=float, nargs="*", default=[0, 45])
    a = ap.parse_args()
    eb, ec, en = map(int, a.enemy.split(","))

    con = sqlite3.connect(DB)
    name = con.execute("SELECT sinner || ' / ' || name FROM identity WHERE id=?", (a.identity_id,)).fetchone()[0]
    rows = con.execute("""SELECT slot, name, sin, atk_type, base, coin_power, coin_num FROM skill
        WHERE owner_type='identity' AND owner_id=? AND uptie=? AND (is_deck_skill=1 OR slot='DEF')
        ORDER BY slot""", (a.identity_id, a.uptie)).fetchall()

    print(f"#{a.identity_id} {name}  (동기화 {a.uptie})   적 스킬: {eb} + {ec} x{en}, 적 정신력 0")
    print("※ 스킬/코인/패시브 텍스트 효과(위력 증가 등) 미반영. 순수 기본 수치 비교.\n")
    head = f"{'슬롯':4} {'스킬':22} {'속성/유형':10} {'위력':12}"
    for s in a.sanity:
        head += f" | 정신력{int(s):>3}: 승률  E[합]  E[피해|승]"
    print(head)
    for slot, sname, sin, atype, base, cp, cn in rows:
        line = f"{slot:4} {sname[:20]:22} {(sin or '-') + '/' + atype:10} {f'{base}+{cp}x{cn}':12}"
        for s in a.sanity:
            r = clash(ClashSide(base, cp, cn, sanity=s), ClashSide(eb, ec, en))
            # 승리 시 남은 코인 k개 → 원래 코인 중 뒤쪽 k개로 타격 (코인별 효과 미반영이라 앞에서부터 재토스와 동일)
            exp_dmg = 0.0
            for k, pk in r.a_remaining.items():
                powers = expected_coin_powers(base, cp, k, sanity=s)
                ctx = HitContext(clash_count=r.clashes_given_a_win[k])
                exp_dmg += pk * sum(hit_damage(p, ctx) for p in powers)
            cond = exp_dmg / r.win if r.win else 0
            line += f" |            {r.win:5.1%}  {r.expected_clashes:4.2f}  {cond:9.1f}"
        print(line)


if __name__ == "__main__":
    main()

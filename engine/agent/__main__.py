"""python -m engine.agent <guide|eval|advise> ... (docs/08 P2)

    guide  202 [--hp 200,300,500 --rounds 4 --plans 3 --games 300 --rollout-ref 7.64]  → data/agent/202/
    eval   202 plan.json [--hp 300]                                            → 평가 JSON
    refine 204 data/agent/204/best_plan.json [--iterations 3 --dev-games 10]   → data/agent/204/refine/
    advise state.json [--no-llm]                                               → 이번 턴 추천 + 설명
"""
import argparse
import json
import sys
from pathlib import Path

from lpdl.compiler import AnthropicBackend, ClaudeCodeBackend

from engine.runtime import Battle, policy_highest
from engine.runtime.loader import ROOT, dummy, load_runtime, load_unit

from . import plan as P
from .harness import advise, run_guide, run_refine, save_guide
from .tools import evaluate, evaluate_set


class AgentClaudeCode(ClaudeCodeBackend):
    SYSTEM = ("You are a Limbus Company strategy agent driving a battle simulator. Follow the user's instructions "
              "exactly; numbers must come only from the simulator results you are given.")


def backend(name: str, model: str | None):
    return AnthropicBackend(model=model) if name == "api" else AgentClaudeCode(model=model)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="engine.agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("guide")
    g.add_argument("identity", type=int)
    g.add_argument("--hp", default="200,300,500", help="적 체력 시나리오 (쉼표로 여러 개 → 평균)")
    g.add_argument("--rounds", type=int, default=4)
    g.add_argument("--plans", type=int, default=3)
    g.add_argument("--games", type=int, default=300)
    g.add_argument("--rollout-ref", type=float, default=None, help="롤아웃 정책 평균 처치 턴 (policy_eval.py 결과)")
    e = sub.add_parser("eval")
    e.add_argument("identity", type=int)
    e.add_argument("plan", type=Path)
    e.add_argument("--hp", default="300", help="쉼표로 여러 개 → 시나리오 평균")
    e.add_argument("--games", type=int, default=300)
    r = sub.add_parser("refine")
    r.add_argument("identity", type=int)
    r.add_argument("plan", type=Path, help="시작 계획 JSON (guide 의 best_plan.json 등)")
    r.add_argument("--hp", default="200,300,500")
    r.add_argument("--iterations", type=int, default=3)
    r.add_argument("--plans", type=int, default=3)
    r.add_argument("--games", type=int, default=300)
    r.add_argument("--dev-games", type=int, default=10, help="탐색 차이 수집 판 수 (시나리오마다)")
    a = sub.add_parser("advise")
    a.add_argument("state", type=Path)
    a.add_argument("--no-llm", action="store_true")
    a.add_argument("--rollouts", type=int, default=64)
    for p in (g, r, a):
        p.add_argument("--backend", choices=["claude-code", "api"], default="claude-code")
        p.add_argument("--model", default=None)
    args = ap.parse_args()

    rt = load_runtime()
    if args.cmd == "advise":
        state = json.loads(args.state.read_text(encoding="utf-8"))
        conv = None if args.no_llm else backend(args.backend, args.model).start
        out = advise(rt, state, conv, rollouts=args.rollouts)
        print(json.dumps({k: v for k, v in out.items() if k != "explanation"}, ensure_ascii=False, indent=2))
        if "explanation" in out:
            print("\n" + out["explanation"])
        return

    hps = [float(x) for x in str(args.hp).split(",")]
    battles = [Battle([load_unit(args.identity), dummy(hp=h)]) for h in hps]
    battle = battles[0]
    if args.cmd == "eval":
        pl = P.Plan.from_dict(json.loads(args.plan.read_text(encoding="utf-8")))
        res = evaluate_set(rt, battles, lambda: P.PlanPolicy(rt, pl), games=args.games)
        res.pop("turns")
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return

    if args.cmd == "refine":
        start = P.Plan.from_dict(json.loads(args.plan.read_text(encoding="utf-8")))
        refs = {"highest": evaluate_set(rt, battles, lambda: policy_highest, games=args.games)["mean_kill_turn"]}
        run = run_refine(rt, battles, args.identity, backend(args.backend, args.model).start, start, refs=refs,
                         iterations=args.iterations, n_plans=args.plans, games=args.games, dev_games=args.dev_games)
        out = ROOT / "data" / "agent" / str(args.identity) / "refine"
        save_guide(run, out)
        (out / "rollout_vs_plan.json").write_text(json.dumps(run.rollout_refs, ensure_ascii=False, indent=2),
                                                  encoding="utf-8")
        best = run.best
        print(f"\n최고 계획: {best.plan.name} ({best.result['mean_kill_turn']}턴) → {out}")
        return

    refs = {"highest": evaluate_set(rt, battles, lambda: policy_highest, games=args.games)["mean_kill_turn"]}
    if args.rollout_ref is not None:
        refs["rollout"] = args.rollout_ref
    print(f"기준: {refs}")
    run = run_guide(rt, battles, args.identity, backend(args.backend, args.model).start, refs=refs,
                    rounds=args.rounds, n_plans=args.plans, games=args.games)
    out = ROOT / "data" / "agent" / str(args.identity)
    save_guide(run, out)
    best = run.best
    print(f"\n최고 계획: {best.plan.name if best else '-'} "
          f"({best.result['mean_kill_turn'] if best else '-'}턴) → {out}")


if __name__ == "__main__":
    main()

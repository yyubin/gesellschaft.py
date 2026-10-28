"""python -m engine.agent <guide|eval|advise> ... (docs/08 P2)

    guide  202 --hp 300 [--rounds 3 --plans 3 --games 300 --rollout-ref 7.64]   → data/agent/202/
    eval   202 plan.json --hp 300                                              → 평가 JSON
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
from .harness import advise, run_guide, save_guide
from .tools import evaluate


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
    g.add_argument("--hp", type=float, default=300)
    g.add_argument("--rounds", type=int, default=3)
    g.add_argument("--plans", type=int, default=3)
    g.add_argument("--games", type=int, default=300)
    g.add_argument("--rollout-ref", type=float, default=None, help="롤아웃 정책 평균 처치 턴 (policy_eval.py 결과)")
    e = sub.add_parser("eval")
    e.add_argument("identity", type=int)
    e.add_argument("plan", type=Path)
    e.add_argument("--hp", type=float, default=300)
    e.add_argument("--games", type=int, default=300)
    a = sub.add_parser("advise")
    a.add_argument("state", type=Path)
    a.add_argument("--no-llm", action="store_true")
    a.add_argument("--rollouts", type=int, default=64)
    for p in (g, a):
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

    battle = Battle([load_unit(args.identity), dummy(hp=args.hp)])
    if args.cmd == "eval":
        pl = P.Plan.from_dict(json.loads(args.plan.read_text(encoding="utf-8")))
        print(json.dumps(evaluate(rt, battle, lambda: P.PlanPolicy(rt, pl), games=args.games), ensure_ascii=False, indent=2))
        return

    refs = {"highest": evaluate(rt, battle, lambda: policy_highest, games=args.games)["mean_kill_turn"]}
    if args.rollout_ref is not None:
        refs["rollout"] = args.rollout_ref
    print(f"기준: {refs}")
    run = run_guide(rt, battle, args.identity, backend(args.backend, args.model).start, hp=args.hp, refs=refs,
                    rounds=args.rounds, n_plans=args.plans, games=args.games)
    out = ROOT / "data" / "agent" / str(args.identity)
    save_guide(run, out)
    best = run.best
    print(f"\n최고 계획: {best.plan.name if best else '-'} "
          f"({best.result['mean_kill_turn'] if best else '-'}턴) → {out}")


if __name__ == "__main__":
    main()

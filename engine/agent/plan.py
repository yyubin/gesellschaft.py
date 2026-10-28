"""계획 = 사람이 읽을 수 있는 조건부 규칙 목록 (docs/08 §3). LLM 이 쓰고 엔진이 실행한다.

JSON 형식
    {"name": "보존 빌드업", "hypothesis": "...",
     "rules": [
        {"when": "self.보존.potency < 3", "use": ["S2", "S1"]},
        {"when": "self.아르뮈르 < 60", "avoid": ["S3"]},
        {"when": "self.아르뮈르 >= 60", "use": ["DEF"]}
     ],
     "default": "highest"}

- 규칙은 위에서부터. `when` 이 참인 규칙만 본다 (`when` 생략 = 항상)
- `use`: 손패에 있는 첫 카드를 쓰고 결정 끝. 없으면 다음 규칙으로
- `avoid`: 그 카드를 뒤 규칙·기본값에서 피함 (보관). 쓸 카드가 그것뿐이면 어쩔 수 없이 씀
- `default`: "highest" (S3 > S2 > S1) | "top" (위 칸) | 카드 순서 목록 ["S2", "S3", "S1"]

조건식: 파이썬 식 일부 (and or not, 비교, + - * /, in)
- self / target (첫 번째 살아 있는 적): hp, max_hp, hp_ratio, shield, sanity, speed, stagger,
  <키워드>.potency / <키워드>.count (상태형), <키워드> (수치형, 예: self.코트). 인격 spec 의 `use` 별칭 사용 가능
- turn (현재 턴), hand (손패 카드 이름 목록, hand[0] = 위 칸, hand[1] = 아래 칸 — 수비 스킬은 아래 칸 대신),
  deck (덱에 남은 카드 이름 목록 — 구성만 알고 순서는 모름. 비면 6장 다시 섞임)
- 함수: count(목록, 카드) (예: count(deck, "S3") == 0), len(목록), min, max
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field

from engine.runtime.interp import Runtime
from engine.runtime.policy import legal_choices
from engine.runtime.state import Battle, Unit
from engine.runtime.turn import Choice, choose_target

CARDS = ("S1", "S2", "S3", "DEF")
UNIT_ATTRS = {"hp", "max_hp", "hp_ratio", "shield", "sanity", "speed", "stagger"}


class PlanError(ValueError):
    pass


@dataclass
class Rule:
    when: str | None = None
    use: list = field(default_factory=list)
    avoid: list = field(default_factory=list)
    _expr: ast.AST | None = None


@dataclass
class Plan:
    name: str
    rules: list[Rule]
    default: str | list = "highest"
    hypothesis: str = ""

    @staticmethod
    def from_dict(d: dict) -> "Plan":
        if not isinstance(d, dict) or "rules" not in d:
            raise PlanError("계획은 {\"name\", \"rules\": [...], \"default\"} 객체여야 함")
        rules = []
        for i, r in enumerate(d["rules"]):
            unknown = set(r) - {"when", "use", "avoid", "note"}
            if unknown:
                raise PlanError(f"rules[{i}]: 모르는 키 {sorted(unknown)}")
            use, avoid = _cards(r.get("use", []), i), _cards(r.get("avoid", []), i)
            if not use and not avoid:
                raise PlanError(f"rules[{i}]: use 나 avoid 중 하나는 있어야 함")
            expr = None
            if r.get("when"):
                try:
                    expr = ast.parse(r["when"], mode="eval")
                except SyntaxError as e:
                    raise PlanError(f"rules[{i}].when 문법 오류: {r['when']!r} ({e.msg})") from None
            rules.append(Rule(r.get("when"), use, avoid, expr))
        default = d.get("default", "highest")
        if isinstance(default, list):
            default = _cards(default, "default")
        elif default not in ("highest", "top"):
            raise PlanError(f"default: highest | top | 카드 목록 (받은 값 {default!r})")
        return Plan(d.get("name", "plan"), rules, default, d.get("hypothesis", ""))

    def to_dict(self) -> dict:
        return {"name": self.name, "hypothesis": self.hypothesis, "default": self.default,
                "rules": [{k: v for k, v in (("when", r.when), ("use", r.use), ("avoid", r.avoid)) if v}
                          for r in self.rules]}


def _cards(xs, where) -> list:
    xs = [xs] if isinstance(xs, str) else list(xs)
    bad = [x for x in xs if x not in CARDS]
    if bad:
        raise PlanError(f"rules[{where}]: 카드 이름은 {CARDS} 중 하나 (받은 값 {bad}). "
                        "교체된 스킬(S3 variant 1 등)도 카드 이름은 원래 칸 (S3)")
    return xs


def parse_plans(text: str) -> tuple[list[Plan], list[str]]:
    """LLM 응답에서 ```json 블록 → (계획 목록, 오류 목록). 계획 하나가 틀려도 나머지는 살린다."""
    import re
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, re.S) or [text]
    plans, errors = [], []
    for b in blocks:
        try:
            d = json.loads(b)
        except json.JSONDecodeError as e:
            errors.append(f"JSON 파싱 실패: {e}")
            continue
        items = d if isinstance(d, list) else d.get("plans", [d]) if isinstance(d, dict) else []
        for x in items:
            try:
                plans.append(Plan.from_dict(x))
            except PlanError as e:
                name = x.get("name", "?") if isinstance(x, dict) else "?"
                errors.append(f"{name}: {e}")
    return plans, errors


# ── 조건식 평가 ──────────────────────────────────────────────────────────────
class _KW:
    """키워드 참조. 상태형은 .potency/.count, 수치형은 값 그대로."""
    def __init__(self, name, potency=0.0, count=0.0, value=None):
        self.name, self.potency, self.count, self.value = name, potency, count, value


class _UnitView:
    def __init__(self, u: Unit, aliases: dict, rt: Runtime):
        self.u, self.aliases, self.rt = u, aliases, rt


def _num(x, src):
    if isinstance(x, _KW):
        if x.value is None:
            raise PlanError(f"{src}: 상태형 키워드 {x.name} 은 .potency 나 .count 로 써야 함")
        return x.value
    if isinstance(x, _UnitView):
        raise PlanError(f"{src}: 유닛 자체는 값이 아님 (self.hp, self.보존.count 처럼)")
    return x


def evaluate(expr: ast.AST, env: dict, src: str = ""):
    def ev(n):
        match n:
            case ast.Expression():
                return ev(n.body)
            case ast.Constant():
                return n.value
            case ast.List() | ast.Tuple():
                return [ev(x) for x in n.elts]
            case ast.Subscript():
                base = ev(n.value)
                i = ev(n.slice)
                if not isinstance(base, list) or not isinstance(i, int):
                    raise PlanError(f"{src}: [] 는 hand[0] / hand[1] 처럼 목록과 정수에만")
                return base[i] if -len(base) <= i < len(base) else None
            case ast.Call():
                fn = n.func.id if isinstance(n.func, ast.Name) else None
                args = [ev(a) for a in n.args]
                if fn == "count" and len(args) == 2 and isinstance(args[0], list):
                    return args[0].count(args[1])
                if fn == "len" and len(args) == 1 and isinstance(args[0], list):
                    return len(args[0])
                if fn in ("min", "max") and args:
                    return (min if fn == "min" else max)(_num(a, src) for a in args)
                raise PlanError(f"{src}: 함수는 count(목록, 카드), len(목록), min, max 만")
            case ast.Name():
                if n.id in ("True", "False"):
                    return n.id == "True"
                if n.id not in env:
                    raise PlanError(f"{src}: 모르는 이름 {n.id} (self, target, turn, hand)")
                return env[n.id]
            case ast.Attribute():
                base = ev(n.value)
                if isinstance(base, _UnitView):
                    return _unit_attr(base, n.attr, src)
                if isinstance(base, _KW):
                    if base.value is not None:
                        raise PlanError(f"{src}: {base.name} 은 수치형 키워드 — .{n.attr} 없이 값으로 비교 (예: self.{n.value.attr if isinstance(n.value, ast.Attribute) else base.name} >= 50)")
                    if n.attr in ("potency", "count"):
                        return getattr(base, n.attr)
                    raise PlanError(f"{src}: {base.name}.{n.attr} — potency / count 만 가능")
                raise PlanError(f"{src}: .{n.attr} 를 쓸 수 없는 값")
            case ast.BoolOp():
                vals = (bool(_num(ev(x), src)) for x in n.values)
                return all(vals) if isinstance(n.op, ast.And) else any(vals)
            case ast.UnaryOp():
                v = _num(ev(n.operand), src)
                if isinstance(n.op, ast.Not):
                    return not v
                if isinstance(n.op, ast.USub):
                    return -v
            case ast.BinOp():
                a, b = _num(ev(n.left), src), _num(ev(n.right), src)
                ops = {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b,
                       ast.Div: lambda: a / b if b else 0}
                if type(n.op) in ops:
                    return ops[type(n.op)]()
            case ast.Compare():
                left = _num(ev(n.left), src)
                for op, right in zip(n.ops, n.comparators):
                    r = ev(right)
                    r = r if isinstance(op, (ast.In, ast.NotIn)) else _num(r, src)
                    ok = {ast.Lt: lambda: left < r, ast.LtE: lambda: left <= r, ast.Gt: lambda: left > r,
                          ast.GtE: lambda: left >= r, ast.Eq: lambda: left == r, ast.NotEq: lambda: left != r,
                          ast.In: lambda: left in r, ast.NotIn: lambda: left not in r}.get(type(op))
                    if ok is None or not ok():
                        if ok is None:
                            raise PlanError(f"{src}: 지원하지 않는 비교")
                        return False
                    left = r
                return True
        raise PlanError(f"{src}: 지원하지 않는 식 ({type(n).__name__})")
    return ev(expr)


def _unit_attr(v: _UnitView, attr: str, src: str):
    u = v.u
    if attr in UNIT_ATTRS:
        return getattr(u, attr)
    name = v.aliases.get(attr, attr)
    if v.rt.kind(name) == "value":                  # 수치형 (코트 등): 값 그대로, 없으면 0
        return _KW(name, value=u.values.get(name, 0))
    s = u.statuses.get(name)                        # 상태형·charge 형: 없으면 위력·횟수 0
    return _KW(name, s.potency, s.count) if s is not None else _KW(name)


def env_for(rt: Runtime, battle: Battle, u: Unit) -> dict:
    al = rt.aliases_for(u)
    foe = next((e for e in battle.enemies_of(u) if e.hp > 0), None)
    return {"self": _UnitView(u, al, rt), "target": _UnitView(foe, al, rt) if foe else None,
            "turn": battle.turn, "hand": [k[0] for k in u.hand], "deck": [k[0] for k in u.deck]}


# ── 실행 ────────────────────────────────────────────────────────────────────
@dataclass
class PlanPolicy:
    """Policy 호환. 계획 규칙대로 칸을 고른다. 대상 = choose_target (막을 수 있으면 합, 아니면 가로채기, 아니면 첫 적)."""
    rt: Runtime
    plan: Plan
    fired: dict = field(default_factory=dict)       # 규칙 번호 → 결정한 횟수 (-1 = default)

    def __call__(self, battle: Battle, u: Unit) -> Choice:
        c = self._card(battle, u)
        return Choice(c.card, *choose_target(battle, u))

    def _card(self, battle: Battle, u: Unit) -> Choice:
        legal = legal_choices(self.rt, battle, u)
        by_card = {}
        for c in legal:
            name = "DEF" if c.card == "DEF" else u.hand[c.card][0]
            by_card.setdefault(name, c)
        env = env_for(self.rt, battle, u)
        avoided: set = set()
        for i, r in enumerate(self.plan.rules):
            if r._expr is not None and not evaluate(r._expr, env, f"rules[{i}].when"):
                continue
            avoided |= set(r.avoid)
            for card in r.use:
                if card in by_card:
                    self.fired[i] = self.fired.get(i, 0) + 1
                    return by_card[card]
        self.fired[-1] = self.fired.get(-1, 0) + 1
        pool = {k: c for k, c in by_card.items() if k not in avoided and k != "DEF"} or \
            {k: c for k, c in by_card.items() if k != "DEF"} or by_card
        d = self.plan.default
        if isinstance(d, list):
            for card in d:
                if card in pool:
                    return pool[card]
            d = "highest"
        if d == "top":
            return min(pool.values(), key=lambda c: c.card if c.card != "DEF" else 9)
        return pool[max(pool)]                       # "S3" > "S2" > "S1"


def check_plan(rt: Runtime, battle: Battle, u: Unit, plan: Plan):
    """조건식이 이 인격·상태에서 평가되는지 미리 확인 (이름 오류를 LLM 에게 돌려주기 위함)."""
    env = env_for(rt, battle, u)
    for i, r in enumerate(plan.rules):
        if r._expr is not None:
            evaluate(r._expr, env, f"rules[{i}].when")

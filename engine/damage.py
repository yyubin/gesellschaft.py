"""피해량 공식 (docs/rules 6장)."""
from dataclasses import dataclass

from .params import DEFAULT, Params


@dataclass
class HitContext:
    phys_res: str = "보통"           # 대상의 해당 공격 유형 내성 등급
    sin_res: str = "보통"            # 대상의 해당 죄악 속성 내성 등급
    stagger: int = 0                 # 대상 흐트러짐 단계 (0 정상, 1 흐트러짐, 2 +, 3 ++). 물리 내성만 대체
    offense_level: float = 0         # 공격 레벨 (공격자)
    defense_level: float = 0         # 방어 레벨 (대상)
    crit: bool = False
    resonance: int = 0               # 죄악 공명 수
    clash_count: float = 0           # 이 공격 전 합 비교 횟수
    dmg_pct: float = 0.0             # 스킬·코인·패시브·버프·디버프 피해량 증감 합 (예: +40% → 0.4)
    flat_pre: float = 0              # 배율 전 고정 증감
    flat_post: float = 0             # 배율 후 고정 증감


def offdef_term(offense_level: float, defense_level: float, params: Params = DEFAULT) -> float:
    """[확인 필요] 공방 수식 결과값 (공식에서 빼는 값)."""
    diff = defense_level - offense_level
    return diff / (abs(diff) + params.offdef_k)


def phys_res_delta(ctx: HitContext, params: Params = DEFAULT) -> float:
    if ctx.stagger:
        return params.stagger_phys_mult[ctx.stagger] - 1
    return params.res_delta[ctx.phys_res]


def multiplier(ctx: HitContext, params: Params = DEFAULT) -> float:
    """둘째 괄호."""
    return (1
            + phys_res_delta(ctx, params)
            + params.res_delta[ctx.sin_res]
            - offdef_term(ctx.offense_level, ctx.defense_level, params)
            + (params.crit_bonus if ctx.crit else 0)
            + params.resonance_bonus.get(ctx.resonance, 0)
            + ctx.clash_count * params.clash_count_bonus)


def hit_damage(power: float, ctx: HitContext, params: Params = DEFAULT) -> float:
    return ((power + ctx.flat_pre)
            * multiplier(ctx, params)
            * (params.dmg_pct_base + ctx.dmg_pct)
            + ctx.flat_post)

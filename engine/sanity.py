"""정신력 변화 (docs/rules 2.1장). 인격별 특수 조건은 spec 훅에서 덮어쓴다."""
from .params import DEFAULT, Params


def clash_win_gain(clash_count: int, params: Params = DEFAULT) -> float:
    """합 승리 시 정신력 증가량. 2합부터 1합당 20% 증가: 1합 10, 2합 12, 3합 14."""
    return params.sanity_clash_win_base * (1 + params.sanity_clash_step * max(0, clash_count - 1))


def clamp(sanity: float, params: Params = DEFAULT) -> float:
    return min(params.sanity_max, max(params.sanity_min, sanity))

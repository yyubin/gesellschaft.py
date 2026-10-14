"""코인 판정. 앞면 확률 = 50% + 정신력 × 1% (docs/rules 2장)."""
from functools import lru_cache
from math import comb

from .params import DEFAULT, Params


def heads_prob(sanity: float) -> float:
    return min(1.0, max(0.0, 0.5 + sanity / 100))


@lru_cache(maxsize=4096)
def binom_pmf(n: int, p: float) -> tuple:
    return tuple(comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1))


def power_dist(base: int, coin_power: int, n_coins: int, sanity: float = 0,
               flat: int = 0, params: Params = DEFAULT) -> dict:
    """남은 코인 n개를 모두 토스했을 때 최종 위력 분포 {위력: 확률}."""
    out = {}
    for k, pk in enumerate(binom_pmf(n_coins, heads_prob(sanity))):
        if pk == 0:
            continue
        power = max(params.min_power, base + flat + coin_power * k)
        out[power] = out.get(power, 0.0) + pk
    return out


def expected_coin_powers(base: int, coin_power: int, n_coins: int, sanity: float = 0,
                         flat: int = 0, params: Params = DEFAULT) -> list:
    """공격 단계: 코인을 순서대로 토스할 때 k번째 코인 적중 시점의 기대 위력 목록.

    k번째 코인 위력 = 기본 + Σ(1..k번째 중 앞면 코인 위력). 하한(min_power) 때문에 기대값을 분포로 계산.
    [확인 필요] 합 승리 후 재토스 방식 — docs/rules 3장.
    """
    p = heads_prob(sanity)
    result = []
    for k in range(1, n_coins + 1):
        e = sum(pj * max(params.min_power, base + flat + coin_power * j)
                for j, pj in enumerate(binom_pmf(k, p)))
        result.append(e)
    return result

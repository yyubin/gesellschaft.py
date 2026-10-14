"""합 판정 정확 계산 (docs/rules 3장).

상태 = (A 남은 코인, B 남은 코인). 매 비교마다 양쪽이 남은 코인을 전부 토스,
낮은 쪽 첫 코인 파괴, 동점이면 상태 유지 + 합 횟수 증가.
동점은 상태를 바꾸지 않으므로 상태별로 기하급수 합으로 닫힌 형태 계산이 가능하다.

현재 생략: 합 도중 정신력 변화, 파괴 불가 코인, 코인별로 다른 위력, 합 중 발동 효과.
"""
from dataclasses import dataclass, field

from .coins import power_dist
from .params import DEFAULT, Params


@dataclass(frozen=True)
class ClashSide:
    base: int
    coin_power: int
    coins: int
    sanity: float = 0
    clash_flat: int = 0      # 합 위력 +N 류


@dataclass
class ClashResult:
    win: float                                   # A 합 승리 확률
    lose: float
    stalemate: float                             # 영원히 동점 (위력 고정 + 동일)
    a_remaining: dict = field(default_factory=dict)   # {남은 코인 수: 확률}  (A 승리 시)
    b_remaining: dict = field(default_factory=dict)   # (B 승리 시)
    expected_clashes: float = 0.0                # 합 비교 횟수 기대값 (동점 포함)
    clashes_given_a_win: dict = field(default_factory=dict)   # {남은 코인 수: E[합 횟수 | 그 결과]}


def _round_probs(a: ClashSide, na: int, b: ClashSide, nb: int, params: Params):
    da = power_dist(a.base, a.coin_power, na, a.sanity, a.clash_flat, params)
    db = power_dist(b.base, b.coin_power, nb, b.sanity, b.clash_flat, params)
    w = l = t = 0.0
    for pa, qa in da.items():
        for pb, qb in db.items():
            if pa > pb:
                w += qa * qb
            elif pa < pb:
                l += qa * qb
            else:
                t += qa * qb
    return w, l, t


def clash(a: ClashSide, b: ClashSide, params: Params = DEFAULT) -> ClashResult:
    # 순방향 DP: mass[s] = 상태 s에 도달할 확률, cmass[s] = 도달 확률 × 그때까지 누적 합 횟수
    mass = {(a.coins, b.coins): 1.0}
    cmass = {(a.coins, b.coins): 0.0}
    res = ClashResult(0.0, 0.0, 0.0)
    a_c, b_c = {}, {}
    # 코인 수 합이 줄어드는 순서로 처리
    for total in range(a.coins + b.coins, 0, -1):
        for na in range(a.coins, -1, -1):
            nb = total - na
            s = (na, nb)
            if s not in mass or nb < 0 or nb > b.coins:
                continue
            m, cm = mass.pop(s), cmass.pop(s)
            if na == 0 or nb == 0:
                continue
            w, l, t = _round_probs(a, na, b, nb, params)
            if 1 - t < 1e-12:
                res.stalemate += m
                continue
            visits = 1 / (1 - t)                 # 이 상태에서의 비교 횟수 기대값
            # 결과까지 누적 합 횟수 기대값 = 이전 누적 + visits
            for nxt, pr in (((na, nb - 1), w / (1 - t)), ((na - 1, nb), l / (1 - t))):
                if pr == 0:
                    continue
                add_m = m * pr
                add_c = (cm + m * visits) * pr
                if nxt[1] == 0:
                    res.a_remaining[nxt[0]] = res.a_remaining.get(nxt[0], 0) + add_m
                    a_c[nxt[0]] = a_c.get(nxt[0], 0) + add_c
                elif nxt[0] == 0:
                    res.b_remaining[nxt[1]] = res.b_remaining.get(nxt[1], 0) + add_m
                    b_c[nxt[1]] = b_c.get(nxt[1], 0) + add_c
                else:
                    mass[nxt] = mass.get(nxt, 0) + add_m
                    cmass[nxt] = cmass.get(nxt, 0) + add_c
    res.win = sum(res.a_remaining.values())
    res.lose = sum(res.b_remaining.values())
    res.expected_clashes = sum(a_c.values()) + sum(b_c.values())
    res.clashes_given_a_win = {k: a_c[k] / v for k, v in res.a_remaining.items() if v > 0}
    return res


def simulate(a: ClashSide, b: ClashSide, toss=None, rng=None, params: Params = DEFAULT, max_rounds: int = 1000):
    """규칙을 한 단계씩 그대로 따르는 참조 구현 (검증·로그용).

    toss(side_name, side, n_coins) -> 위력. 지정하지 않으면 rng로 코인 토스.
    반환: (승자 'A'|'B'|None, 승자의 남은 코인 인덱스(1부터), 합 비교 횟수, 로그)
    """
    import random
    from .coins import heads_prob
    rng = rng or random.Random()

    def default_toss(name, side, n):
        p = heads_prob(side.sanity)
        heads = sum(rng.random() < p for _ in range(n))
        return max(params.min_power, side.base + side.clash_flat + side.coin_power * heads)

    toss = toss or default_toss
    a_left = list(range(1, a.coins + 1))
    b_left = list(range(1, b.coins + 1))
    log, n = [], 0
    while a_left and b_left and n < max_rounds:
        n += 1
        pa, pb = toss("A", a, len(a_left)), toss("B", b, len(b_left))
        if pa > pb:
            log.append(("A", pa, pb, f"B 코인{b_left.pop(0)} 파괴"))
        elif pa < pb:
            log.append(("B", pa, pb, f"A 코인{a_left.pop(0)} 파괴"))
        else:
            log.append(("=", pa, pb, "동점, 재합"))
    if a_left and not b_left:
        return "A", a_left, n, log
    if b_left and not a_left:
        return "B", b_left, n, log
    return None, [], n, log

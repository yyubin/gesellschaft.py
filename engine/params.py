"""규칙 중 수치가 아직 확정되지 않은 항목. docs/rules/00-core-rules.md 의 [확인 필요]와 1:1 대응.

값이 확정되면 여기만 고치면 된다. 기본값은 커뮤니티 통념 기준 추정치.
"""
from dataclasses import dataclass, field

SINS = ("분노", "색욕", "나태", "탐식", "우울", "오만", "질투")   # E.G.O cost / resistance 배열 순서
PHYS = ("참격", "관통", "타격")                                  # 인격 resistance 배열 순서


@dataclass(frozen=True)
class Params:
    # 내성 등급 → 피해량 공식 둘째 괄호에 더해지는 값 (= 배율 - 1)
    # 취약(Fatal ×2), 보통(×1), 견딤(Endure ×0.75), 내성(Ineff ×0.5) — 확정. 견딤은 현재 죄악 내성에만 등장
    res_delta: dict = field(default_factory=lambda: {
        "취약": 1.0, "보통": 0.0, "견딤": -0.25, "내성": -0.5,
    })
    # 흐트러짐 단계(0=정상, 1=흐트러짐, 2=+, 3=++) → 물리(참·관·타) 내성 배율. 원래 내성 등급을 대체
    stagger_phys_mult: tuple = (None, 2.0, 2.5, 3.0)
    # [확인 필요] 공방 수식: (방어 레벨 - 공격 레벨) / (|차이| + offdef_k). 공식에서 빼는 값
    offdef_k: float = 25.0
    # 공명 수 → 피해 증가값. 기본 0: 대부분 인격 패시브/서포트 패시브 효과로 들어오므로 spec에서 처리
    resonance_bonus: dict = field(default_factory=dict)
    # [확인 필요] 셋째 괄호(피해량 증감)를 1 + Σ 로 볼지
    dmg_pct_base: float = 1.0
    # 정신력: 합 승리 시 base × (1 + step × (합 횟수 - 1)), 2합부터 1합당 20% 증가. 인격별 예외는 spec에서
    sanity_clash_win_base: float = 10.0
    sanity_clash_step: float = 0.2
    sanity_min: float = -45.0
    sanity_max: float = 45.0
    # 확정
    crit_bonus: float = 0.2
    clash_count_bonus: float = 0.01
    # 엔진 구현 선택: 최종 위력 하한
    min_power: int = 0


DEFAULT = Params()

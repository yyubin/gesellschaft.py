# 03. 기술 아키텍처 및 처리 플로우

작성일: 2026-10-13 · 범위: 인격 + E.G.O (스킬·패시브·침식) · 목표 콘텐츠: 거울 던전
에고 기프트는 사용자가 별도 파이프라인으로 제공 예정 → **같은 Effect Spec 형식으로 꽂히도록 설계**

---

## 0. 핵심 판단 요약

| 질문 | 결론 | 이유 |
|---|---|---|
| LangGraph 필요? | **처음엔 불필요.** 필요해지면 그때 도입 | 핵심 가치는 결정적 계산(엔진/분석/최적화)이고 LLM 흐름은 단순함. 오프라인 컴파일은 "코딩 에이전트 루프", 온라인은 "단일 에이전트 + 툴" |
| Vector DB 필요? | **불필요** (옵션) | 데이터가 작음(인격 ~190, E.G.O ~120, 키워드 ~450). 질의가 구조적("출혈 부여 + 분노 + 관통"). SQL 필터 + FTS로 충분, 키워드 사전은 프롬프트에 통째로 넣고 캐시 |
| Graph DB 필요? | **불필요.** 관계는 SQL 테이블 + 메모리 내 NetworkX | 노드 수백, 엣지 수천. 시너지 계산은 그래프 탐색 쿼리가 아니라 수치 점수 계산. Neo4j는 운영 비용만 추가 |
| 가장 중요한 자산 | **Mechanic Spec (git 관리 코드/스키마)** + 엔진 | LLM 품질보다 spec 정확도가 결과 품질을 결정 |

> 원칙: **LLM은 "번역기(텍스트→spec)"와 "설명자(수치→말)"로만 쓰고, 판단의 근거는 전부 엔진 계산.**

---

## 1. 전체 플로우

```
┌──────────────────────── 오프라인 (배치, 패치 시 재실행) ────────────────────────┐
│                                                                              │
│  P0 수집       크롤러 → raw 스냅샷(날짜별, 불변) → 정규화 → SQLite              │
│                 └ 원문 해시 비교 → 바뀐 인격/E.G.O만 다음 단계로                 │
│                                                                              │
│  P1 컴파일     [코딩 에이전트] 원문 + 키워드 사전 + 엔진 API 문서 + 유사 spec 예시  │
│   (LLM)          → specs/identity/204.py + tests/identity/test_204.py          │
│                  → pytest 실행 → 실패 시 수정 반복                              │
│                [검증 에이전트] 별도 컨텍스트에서 spec → 자연어 역번역 → 원문과 diff │
│                  → 불일치 = 사람 검수 큐                                        │
│                                                                              │
│  P2 엔진       공통 규칙(사용자 제공 피해 공식·판정 규칙) + spec 플러그인 로딩      │
│   (결정적)       └ 사용자 제공 계산 예제 = 엔진 골든 테스트                       │
│                                                                              │
│  P3 분석       정적 분석(자원 흐름/브레이크포인트/도달 턴)                        │
│   (결정적)     + 시뮬(시나리오별) + 정책 탐색 + 규칙 추출                          │
│                  → analysis 테이블 (키: spec_hash, engine_ver, scenario)        │
│                                                                              │
│  P4 팀         Solo/Pair/Global 점수 → 후보 축소 → 팀 시뮬로 상위 K 재평가         │
│   (결정적)       → synergy_edge 테이블                                          │
│                                                                              │
│  P5 가이드     [LLM] P3/P4 수치 → 인격별 운용 가이드 문장 (근거 링크 필수, 캐시)    │
└──────────────────────────────────────────────────────────────────────────────┘
                                   │ DB
┌──────────────────────── 온라인 (사용자 요청) ───────────────────────────────────┐
│  P6 Agent     Claude + tool use                                              │
│                 search_identities / get_identity / get_guide / explain_keyword │
│                 evaluate_team / optimize_team(constraints) / simulate(team, …) │
│  P7 전투 조언  (후속) 스크린샷 → 비전 LLM → BattleState → 사용자 확인 → 엔진 탐색   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 데이터 저장소 설계

### 2.1 SQLite (나중에 필요하면 Postgres)
```
sinner(id, name)
identity(id, sinner_id, name, grade, season, hp, speed_min, speed_max, def_level,
         res_slash, res_pierce, res_blunt, source_hash, snapshot_date)
ego(id, sinner_id, name, grade(ZAYIN..ALEPH), cost_json, resistance_json, source_hash)
skill(id, owner_type[identity|ego], owner_id, slot[S1|S2|S3|DEF|EGO|EGO_COR],
      variant, uptie, name, atk_type, sin, base, coin_power, coin_num, weight,
      sanity_cost, is_deck_skill, raw_effect)
coin(skill_id, idx, raw_effect)
passive(id, owner_type, owner_id, kind[main|support|ego], uptie,
        cond_sins, cond_count, cond_type[보유|공명], raw_effect)
keyword(name, description, kind[standard|unique])
affiliation(identity_id, name)

-- 파생 데이터
effect_source(id, kind[skill|coin|passive|ego_passive|gift], ref_id, spec_module, spec_hash, status[draft|tested|verified])
keyword_role(identity_id, keyword, role[apply|count_up|consume|payoff|self_resource], amount_per_turn)
synergy_edge(src_identity, dst_identity, kind, weight, evidence_ref)
analysis(subject_id, scenario, spec_hash, engine_ver, metrics_json, rules_json)
review_queue(effect_source_id, reason, diff, resolved_by)
```
- `effect_source.kind` 에 `gift`가 이미 있음 → 기프트 파이프라인이 같은 테이블에 행을 추가하면 끝.
- `keyword_role`, `synergy_edge` 가 사실상 "그래프". 필요할 때 NetworkX로 로딩해서 분석/시각화.

### 2.2 Vector/Graph DB를 고려할 시점
- **Vector**: 사용자가 애매한 자연어로 찾을 때("적 정신력 깎는 애"). 그래도 spec 기반 `keyword_role` 질의가 더 정확. 필요하면 `sqlite-vec`로 같은 DB 안에 추가.
- **Graph DB**: 데이터가 수만 노드 이상으로 커지거나, 외부 공략·패치노트 등 비정형 지식까지 엮는 GraphRAG를 할 때. 현재 범위에선 해당 없음.

---

## 3. Effect Spec 형식 (기프트까지 공통)

모든 효과 출처(스킬·코인·패시브·E.G.O·기프트)를 하나의 인터페이스로:

```python
# specs/identity/204.py (예시, 일부)
from engine.spec import Identity, resource, on, cond, per, act, ally

class LeNoirBrandManager(Identity):
    id = 204
    resources = [
        resource("보존", owner="self", cap=20, decay=1, kind="charge_like"),
        resource("보존 위력", owner="self"),
        resource("검은 신의 가호", owner="self", cap=100),
        resource("약속된 못", owner="target"),
    ]

    @on("stage_enter")
    def mallets(self, ctx):
        ctx.gain(self, "보존", 10)
        ctx.gain(ally(affiliation="르누아르", exclude_self=True), "충전", 2)

    @on("apply", keyword="약속된 못", source="skill")   # 패시브: 못 부여 시 보존 소모
    def nail_cost(self, ctx, ev):
        p = self.get("보존")
        if p >= 12:   ev.amount += 1; ctx.consume(self, "보존", 4)
        elif p >= 2:  ctx.consume(self, "보존", 2)
        else:         ev.cancel()
```
- 대부분은 DSL 수준의 짧은 코드, 특이 케이스만 자유 코드.
- Python 클래스로 쓰는 이유: LLM(코딩 에이전트)이 쓰고 **pytest로 바로 검증**할 수 있음. JSON DSL보다 표현력·디버깅이 낫다.
- 테스트 예:
```python
def test_nail_needs_preservation():
    b = Battle.dummy(allies=[204]); u = b.unit(204); u.set("보존", 1)
    b.apply(u, b.enemy, "약속된 못", 1, source="skill")
    assert b.enemy.get("약속된 못") == 0
```

---

## 4. LLM 사용 지점과 오케스트레이션

| 지점 | 형태 | 오케스트레이션 | 비고 |
|---|---|---|---|
| P1 spec 컴파일 | 코딩 에이전트 (파일 작성 + 테스트 실행 + 수정 루프) | **Claude Agent SDK / Claude Code 헤드리스**를 인격 단위 job으로 실행, 바깥은 단순 Python 작업 큐 | 파일 읽기·쓰기·pytest 실행 도구가 기본 내장되어 있어 직접 그래프를 짤 필요 없음 |
| P1 검증 | 단발 호출 (역번역 + 비교) | 일반 함수 | 컴파일 에이전트와 컨텍스트 분리 (자기 검증 편향 방지) |
| P5 가이드 생성 | 단발 호출, 구조화 출력 | 배치 | 입력은 수치/규칙만, 근거 ID 필수 |
| P6 온라인 Agent | 단일 에이전트 + 툴 루프 | Anthropic SDK 툴 루프 | 툴 6~8개면 충분 |
| P7 전투 조언 | 비전 추출 → 확인 → 계산 → 설명 | 여기서 LangGraph 검토 가치 있음 | 사용자 확인 단계(interrupt), 단계별 재시도가 필요해지면 |

**LangGraph 도입 기준**: (1) 사람 확인을 중간에 끼우는 다단계 흐름, (2) 단계별 체크포인트/재개, (3) 분기가 많은 상태 머신 — 이 중 2개 이상 해당하면 도입. 현재는 P7만 해당 가능성.
거울 던전 런 상태(편성, 층, 보유 기프트, 코스트)는 에이전트 메모리가 아니라 **DB의 `run_session` 테이블**에 둔다.

컨텍스트 구성 (P1 컴파일 프롬프트):
1. 키워드 사전 전체 (고정 → 프롬프트 캐시)
2. 엔진 Spec API 문서 + 공통 규칙 요약 (고정 → 캐시)
3. 유사 인격의 검증된 spec 2~3개 (공유 키워드/소속 기준 **SQL로** 선택 — 벡터 검색 불필요)
4. 대상 인격 원문 (동기화 3/4 모두)

---

## 5. 엔진 설계 포인트

- **공통 규칙은 사용자 제공 공식으로 구현** → `docs/rules/` 에 공식 + 손계산 예제를 적어주면 그대로 골든 테스트가 됨.
- 이벤트 버스 구조: 모든 효과는 훅으로만 개입 → spec끼리 서로 몰라도 상호작용(기프트 포함)이 자동으로 됨.
- **결정적 모드 + 확률 모드**: 코인 판정을 기대값/분포로 계산하는 모드(빠름, 정적 분석용)와 시드 고정 몬테카를로 모드(시뮬용).
- 성능: 처음엔 순수 Python. 정책 탐색 시뮬이 느려지면 상태 복사 최소화 → numba/Rust 순으로.
- 시나리오 라이브러리 (`scenarios/`): 허수아비, 단일 보스, 다수 적, 내성 프로필 — 거울 던전 대표 적으로 점차 확장.

---

## 6. 단계별 구현 순서

1. **P0**: 전체 크롤 + SQLite 정규화 + 인격/E.G.O 뷰어 (요구사항 1 충족)
2. **규칙 문서화**: 사용자가 피해 공식·판정 규칙 + 계산 예제 제공 → `docs/rules/`
3. **P2 엔진 코어** + 골든 테스트 (표준 키워드까지)
4. **P1 컴파일러**: 르누아르 묶음(#202~#204)으로 spec 수동 1개 + 에이전트 2개 → 품질 측정
5. **P3 정적 분석기** → 첫 운용 가이드
6. **P6 Agent** (조회·가이드·팀 평가 툴)
7. 시뮬/정책 탐색 → 팀 최적화 → 기프트 연동 → 전투 조언(P7)

## 7. 기술 스택 (제안)
Python 3.12+ · SQLite(FTS5) · Pydantic(스키마) · pytest · NetworkX · numpy ·
FastAPI(백엔드) · 프론트는 빠르게 Streamlit → 이후 Next.js ·
LLM: Claude (컴파일=Agent SDK/Claude Code 헤드리스, 온라인=Anthropic SDK 툴 루프)

## 부록: 에고 기프트 데이터 참고
limbuscompany.wiki.gg 의 `Module:EgoGift/data` (Lua 테이블, 약 939항목, 강화 단계 포함, 영문)에
name / desc / sin / tier / cost / category 가 있음 → `data/raw/wikigg_egogift_data.json` 에 원본 저장해 둠.
한국어 텍스트가 필요하면 사용자 파이프라인으로 대체.

# 01. 사전 조사: 데이터 소스 · 데이터 구조 · AI Agent / 조합 최적화 설계

작성일: 2026-10-13 · 샘플: 인격 23종 (`data/raw/identity/*.json`)

---

## 1. 데이터 소스 (baslimbus.info, "단빵숲")

프론트는 Next.js이고 HTML에는 데이터가 없다. 실제 데이터는 백엔드 JSON API에서 클라이언트가 가져온다.
→ **HTML 파싱 불필요, JSON API를 직접 호출하면 된다.**

| 용도 | 엔드포인트 (`https://baslimbus.onrender.com`) | 비고 |
|---|---|---|
| 인격 목록 | `/dictionary/paginated/new/identity?page=0&size=500&type=and` | 189종 (2026-10 기준), 필터 파라미터 지원 |
| 인격 상세 | `/dictionary/identity/{id}` | id 1~204 (빈 번호 있음) |
| E.G.O 목록 / 상세 | `/dictionary/paginated/new/ego?...` / `/dictionary/ego/{id}` | 116종 |
| 패시브 목록 | `/api/dictionary/paginated/new/passive` (baslimbus.info 경유) | 미확인 |
| 키워드 티어 | `/dictionary/tier/new/identity/keyword` | 커뮤니티 티어 → 약한 정답 라벨로 활용 가능 |
| 리롤 티어 | `/dictionary/tier/new/identity/reroll` | |
| 키워드 사전 | (API 아님) JS 번들 안의 정적 배열 | 446개 키워드 정의 추출 완료 → `data/raw/keyword_dict.json` |

주의사항
- render.com 무료 인스턴스 → 콜드스타트 지연, 간헐적 500 (`CM1`). 재시도 + 로컬 캐시 필수.
- 비공식 팬 사이트. 이용약관 확인 필요, 요청 간 딜레이(현재 1초), 전체 크롤은 1회 후 증분 갱신.
- 데이터는 **동기화 3/4단계**만 제공 (`level: 3 | 4`).

---

## 2. 인격 데이터 구조 (샘플 분석 결과)

```
Identity
├─ id, character(수감자 12명), name, grade(1~3), season, releaseDate, affiliation[]
├─ status: life(HP), speed("4-7"), defend("65(+4)")
├─ resistance[3]          # [참격, 관통, 타격] 순서로 추정 (보통/취약/내성) → 검증 필요
├─ keyword[]              # 태그 (출혈, 진동, 충전, 고유 키워드 …)
├─ identitySkill1s/2s/3s/DefSkills: Skill[]   # 동기화 레벨별로 중복 수록
│    Skill: name, type(참격/관통/타격/방어/회피/반격/가드), resource(죄악 속성),
│           skillPower(기본 위력), coinPower, coinNum, atkWeight(가중치), level,
│           quantity(덱 내 장수, 구형만 3/2/1 있고 신형은 null),
│           normalEffect(텍스트), coin1..9Effect(텍스트)
└─ identityPassives: Passive[]
     Passive: name, isMain(메인/서포트), resource("분노, 오만"), resQuantity(N),
              activeCond("보유" | "공명" | null), effect(텍스트), level
```

### 관찰된 특징
1. **수치는 정형, 효과는 반정형 텍스트.**
   - 정형: 위력/코인/가중치/속성/유형/HP/속도 → 바로 계산 가능.
   - 텍스트지만 패턴이 강함. 샘플 23종 기준 트리거 태그 빈도:
     `[적중시]` 295, `[사용시]` 166, `[전투 시작시]` 43, `[합 승리시]` 19, `[공격 종료시]` 14 …
     동사: 증가 196, 부여 180, 얻음 120, 소모 83, 감소 55, 회복 22.
   - 형식 예: `[적중시] 진동 4 부여`, `[사용시] 대상의 진동 위력과 약속된 못의 합 6당, 코인 위력 +1 (최대 2)`
   → **트리거 + 조건 + 대상 + 행위 + 키워드 + 수치 + 상한**의 DSL로 구조화 가능.
2. **신형 인격일수록 복잡도 급증.** #1 LCB 수감자는 효과 몇 줄, #204는 패시브 4개 + 스킬 변형(“심판!”, “거듭 내리쳐…”) + 전용 리소스(보존, 약속된 못, 검은 신의 가호).
   - 같은 skillSeq에 여러 스킬 = **변형 스킬** (조건부 대체, 패시브로만 발동하는 스킬 등). 덱에 들어가는 기본 스킬과 구분해야 함.
3. **명시적 시너지 단서가 텍스트에 있음.**
   - 소속 참조: “르누아르 소속 아군 인격에게 …”, “충전 인격 1명당 …” → 하드 시너지 엣지.
   - 키워드 생산/소비 관계: A가 `진동 부여`, B가 `진동 폭발` → 생산자-소비자 엣지.
4. **패시브 발동 조건이 죄악 자원에 묶임** (`분노 x3 보유`, `오만 x2 공명`).
   → 팀 전체 스킬 풀의 죄악 분포가 패시브 가동률을 결정 → **정량 계산 가능**.
5. 데이터 결함: `quantity` 신형 null, `atkWeight` null 있음, 서포트 패시브 `resQuantity` null, `resource: "없음"`. 정규화 단계에서 보정 필요.

---

## 3. 정량화 가능성 평가

| 영역 | 계산 가능성 | 방법 |
|---|---|---|
| 합(Clash) 승률 | ★★★ 정확 계산 | 코인 앞면 확률 = 50% + 정신력%. 이항분포 + 코인 소모 DP로 대상별 합 승률/기대 위력 정확 계산 |
| 죄악 자원 경제 | ★★★ 시뮬레이션 | 덱(S1×3, S2×2, S3×1 per 수감자)에서 스킬 뽑기 몬테카를로 → 패시브 `보유/공명` 충족 확률, E.G.O 자원 수급 |
| 피해 유형 / 내성 커버리지 | ★★★ 단순 집계 | 팀의 참/관/타 분포 vs 적 내성 프로필 |
| 키워드 시너지 | ★★☆ DSL 필요 | 효과 DSL 추출 후 키워드별 (부여량/턴, 횟수 증가, 소비, 페이오프) 벡터화 → 생산·소비 매칭 |
| 키워드 피해 기대값 | ★★☆ 간이 시뮬 | 출혈=코인 판정마다, 파열=피격마다, 화상=턴 종료, 침잠=정신력, 진동=폭발 시 흐트러짐 … 키워드 사전 정의 그대로 턴 시뮬 |
| 조건부·고유 메커니즘 | ★☆☆ | “검은 신의 가호 100이면 …” 같은 고유 상태는 전부 모델링하기 어려움 → LLM 정성 평가 + 커뮤니티 티어로 보정 |

결론: **“완전한 전투 시뮬레이터”는 과투자, “계층형 점수 모델”은 충분히 현실적.**

### 제안하는 팀 점수 모델

```
Score(team) = Σ_i  Solo(i)                       # 개별 성능 (스킬 기대 위력, 합 승률, HP/방어, 커뮤니티 티어)
            + Σ_{i<j} Pair(i, j)                 # 키워드 생산→소비, 소속 버프, 충전 공급 등 2자 시너지
            + Global(team)                       # 키워드 집중도, 죄악 자원 → 패시브 가동률, 피해유형 커버리지
            - Penalty(team)                      # 공명 불가, 같은 키워드 과잉 소비 경쟁 등
```

- `Pair`는 DSL에서 자동 생성 (예: `applies(진동, 4/턴) × consumes(진동 폭발)` → 가중치).
- `Global` 중 **패시브 가동률**은 몬테카를로로 계산 → 이게 이 게임 조합의 핵심 정량 지표.
- 가중치는 처음엔 수동, 이후 커뮤니티 티어/덱 데이터로 회귀 보정.

### 조합 탐색
- 제약: 수감자당 인격 1개, 출전 5~8명(12명 중), 보유 인격만 사용, “출혈 덱 고정” 같은 사용자 조건.
- 탐색 공간: 수감자당 ~16개 × 12명 → 전수조사 불가(10^14). 하지만
  - **보유 필터 + 키워드 고정**하면 수감자당 후보 2~4개로 축소 → 전수/분기한정 가능.
  - 일반 케이스: 빔 서치 또는 로컬 서치(수감자 하나씩 교체), 쌍 시너지만 쓰면 QUBO/ILP 선형화도 가능.

---

## 4. AI Agent 아키텍처 제안

핵심 원칙: **숫자는 결정적 코드가, 해석과 대화는 LLM이.** LLM이 데미지를 “추측”하게 두지 않는다.

```
[크롤러] → data/raw (JSON)
   ↓ 정규화
[DB: SQLite]  identity / skill / coin / passive / keyword
   ↓ LLM 오프라인 배치 (1회 + 신규 인격만 증분)
[Effect DSL]  스킬·코인·패시브 텍스트 → 구조화 효과 (원문 해시로 캐시, 샘플 수동 검수)
   ↓
[계산 엔진 (Python)]  clash_calc / resource_sim / keyword_sim / team_score / optimizer
   ↓ tool로 노출
[Agent (Claude + tool use)]
   tools: search_identities, get_identity, explain_keyword,
          evaluate_team, optimize_team(constraints), compare_identities
   ↓
[UI]  인격/스킬 뷰어 + 조합 추천 채팅
```

LLM이 들어가는 지점 두 곳:
1. **오프라인 추출기**: 텍스트 → Effect DSL. 키워드 사전(446개)을 용어집으로 프롬프트에 포함. 정형 스키마(JSON schema) 강제 출력.
2. **온라인 에이전트**: “출혈 덱 짜줘, 나 이거 이거 있어”, “이 인격 왜 좋아?” 같은 의도를 툴 호출로 변환하고, 결과 수치를 근거로 설명. 고유 메커니즘처럼 DSL로 못 담는 부분은 원문을 읽고 정성 평가.

Effect DSL 예시 (#204 S2 코인2, 동기화 4):
```json
{"trigger": "on_hit", "coin": 2, "effects": [
  {"op": "gain",   "target": "self",   "keyword": "보존", "field": "count",  "value": 8},
  {"op": "add",    "target": "target", "keyword": "진동", "field": "count",  "value": 3},
  {"op": "burst",  "target": "target", "keyword": "진동"},
  {"op": "reduce", "target": "target", "keyword": "진동", "field": "count",  "value": 1}
]}
```

---

## 5. 검증이 필요한 가정 (게임 지식 기반, 데이터로 확인 전)
- `resistance` 배열 순서가 참격/관통/타격인지
- `보유` = 누적 보유 죄악 자원 수, `공명` = 연속 배치된 동일 죄악 스킬 수 — 정확한 판정 규칙
- 신형 인격의 덱 장수 (quantity null → 3/2/1 가정)
- 서포트 패시브 발동 조건 (편성됐지만 출전하지 않은 인격)
- 콘텐츠별 출전 인원 (거울 던전 / 굴절 철도 등)

## 6. 다음 단계 제안
1. 전체 인격 189 + E.G.O 116 크롤 → SQLite 정규화
2. 인격/스킬 뷰어 (요구사항 1번) — 정형 데이터만으로 바로 가능
3. Effect DSL 스키마 확정 → 샘플 20개로 LLM 추출 품질 측정
4. 합 승률 계산기 + 죄악 자원 몬테카를로 (DSL 없이도 가능한 정량 지표부터)
5. 팀 점수 v0 + 탐색기 → 커뮤니티 티어와 비교해 sanity check
6. Agent tool 래핑

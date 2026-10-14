# 05. lpdlc 검토 — P1 spec 설계에 가져올 것 / 고칠 것 (2026-10-14)

대상: github.com/yyubin/lpdlc (사용자의 이전 프로젝트, Java 21 + ANTLR 4)
읽은 파일: `src/main/antlr/LPDL.g4`(713줄), `prompt/LPDLGrammarSpec.java`, `prompt/PromptTemplate.java`,
`pipeline/NLToSQLPipeline.java`, `example_honglu.lpdl`, `test/test1.txt`

## 1. lpdlc 요약

- **목적**: 인격 텍스트 → LPDL(텍스트 DSL) → MySQL / Neo4j. 저장과 시너지 조회용. 계산 기능은 없음
- **구조**: `persona > skill > sync(SYNC_3/4) > effect | coin > effect { trigger, when, action, branch }`, `passive { condition, syncLevel, trigger, when, action }`, `deck { personas, synergy }`
- **NL 파이프라인**: GPT-4o 시스템 프롬프트(문법 설명 + few-shot 6개 + 규칙 15개) → ANTLR 파싱 → 실패하면 에러 메시지를 붙여 재생성 (최대 N회)

## 2. 그대로 가져올 것

| 항목 | lpdlc | gesellschaft 적용 |
|---|---|---|
| 트리거 어휘 | `ON_USE, ON_HIT, ON_HEAD_HIT, ON_TAIL_HIT, ON_CRITICAL_HIT, ON_WIN_CLASH, ON_LOSE_CLASH, ON_ATTACK_END, ON_KILL, ON_BATTLE_START, ON_TURN_START, ON_TURN_END, ON_DAMAGED, ON_ALLY_*, ON_DROP, ALWAYS` | 엔진 이벤트 훅 이름의 초안. 게임 텍스트 태그(`[사용시]`, `[앞면 적중시]` …)와 1:1 매핑 표로 만듦 |
| 원문 + 구조 병기 | coin/effect마다 `text """원문"""` 필수 | spec 항목마다 원문 위치(`skill_id/coin_idx/줄`) 저장 → 역번역 diff와 검수에 사용 |
| 조건식 | `AND/OR/NOT`, 비교, `in a..b`, `divisible_by`, `has_status`, `has_tag` | `cond(...)` 표현에 그대로 반영. `divisible_by`는 "N마다" 계열에 필요 |
| 액션 옵션 | `cap, perTurn, nextTurn, duration, timing, coins FIRST/LAST/INDEX/ALL/CURRENT, select, unit` | 거의 다 필요. 이름 그대로 사용 |
| 분기 | `branch { order, stopOnMatch, when, ... }` | "~이면 대신 ~" 패턴 표현에 사용 |
| 패시브 조건 | `condition HOLD/RESONATE sin X count N` + `syncLevel` | 엔진 `resources.py`의 보유/공명과 직결 |
| 생성 → 검증 → 에러 피드백 재생성 루프 | 구문 오류만 피드백 | 유지하되 피드백을 pytest 실패와 스키마 검증 결과로 확장 (Claude Code 헤드리스 루프) |
| 원본 입력 포맷 | `test1.txt` (기본정보/스테이터스/내성/스킬별 코인 텍스트) | DB에서 같은 형태로 렌더링해 컴파일 프롬프트에 넣음 |

## 3. lpdlc의 문제점 (그대로 가져오면 안 되는 것)

### 3-1. 문법·프롬프트·예제가 서로 안 맞음
| 문제 | 근거 |
|---|---|
| 예제 파일이 자기 문법으로 파싱 안 됨 | `coinBody : LBRACE textDecl effectInlineDecl+ RBRACE` (text 필수)인데 `example_honglu.lpdl`의 coin에는 text가 없음. coin 타입도 `POSITIVE`인데 spec은 `NORMAL|UNBREAKABLE|REUSE` |
| enum이 문법에서 강제되지 않음 | trigger, stat, coin 타입, unit, timing이 전부 `IDENT` → LLM이 아무 이름이나 만들어도 파싱 통과 (`season type BATTLE_PASS`도 통과) |
| 트리거 이름 혼재 | 패시브 예시 `BATTLE_START`와 목록 `ON_BATTLE_START`가 섞여 있음 |
| 프롬프트에 있는데 문법에 없음 | 타겟 `all, random, highest_hp_ally, fastest`, 액션 `deal, heal` |
| 규칙끼리 모순 | "세미콜론 위치 지켜라"(규칙 7)와 "세미콜론 없음", 주석 허용(문법)과 금지(프롬프트) |
| `level` 의미 불명 | 설명은 "skill 번호와 같음", 예제 4는 입력 40 → 출력 3, 실제 의미는 공격 레벨 보정 |

### 3-2. few-shot이 오류를 가르침
- **예제 5**: 입력에 없는 `condition HOLD sin WRATH count 3`, `syncLevel SYNC_3`을 패시브에 붙임 → 없는 조건을 지어내라고 가르치는 셈
- **예제 6**: "파열 **횟수** 1 부여"를 `RUPTURE +1`(위력)로 변환 → 위력과 횟수를 구분하지 않음

### 3-3. 계산에 필요한 정보가 빠짐
| 빠진 것 | 왜 필요한가 |
|---|---|
| 상태이상 **위력 / 횟수** 구분 | 출혈·화상·진동·파열·침잠 피해는 위력×횟수 구조. `BLEED_COUNT`만 따로 있음 |
| 내성 4단계 | `WEAK/NORMAL/RESIST` 3단계뿐 → 견딤(×0.75)이 사라짐 |
| 피해 공식에서의 위치 | `DAMAGE_UP`, `DAMAGE_MODIFY`, `BUFF_DAMAGE_UP`이 % / 고정, 배율 전 / 후를 구분하지 않음 (`docs/rules/00-core-rules.md` 피해 공식 괄호와 연결 필요) |
| 합 위력 vs 최종 위력 vs 코인 위력 | `CLASH_POWER_UP`, `COIN_POWER_UP`, `modify power`가 섞여 있음. 엔진은 셋을 다르게 처리 |
| "X당 Y" 스케일링 | `unit PER_N` 정도만 있고, 기준값(출혈 3당, 정신력 10당 …)과 상한 표현이 없음 |
| 정신력, 죄악 자원 소모, E.G.O | 조건과 액션 대상에 없음 |
| 인격 고유 키워드 | stat가 고정 목록 + `ETC` 탈출구 → 인격 전용 상태(르누아르 등)를 표현 못 함 |
| 불확실 표시 | 해석 못 한 문장을 `ETC`/`SUPPRESS_EFFECT`로 덮어버림 → 검수 대상인지 알 수 없음 |
| 중복·모호 어휘 | `HP_HEAL`/`HEAL_HP`, `DAMAGE_UP`/`BUFF_DAMAGE_UP`, `STATUS_INFLICT`(무엇을?), `IS_STAGGERED`(조건인데 stat 목록에 있음) |

### 3-4. 검증이 구문 수준뿐
파싱만 통과하면 성공 처리. 의미 검증(원문과 일치하는지, 수치가 맞는지)이 없음 → 예제 5·6 같은 오역도 그대로 통과.

## 4. gesellschaft P1 spec 설계에 반영할 결정 (제안)

1. **형식**: 텍스트 DSL + ANTLR 대신 `docs/03` 계획대로 **Python spec (`specs/identity/{id}.py`) + Pydantic enum**. enum이 코드에서 강제되므로 3-1의 오류 대부분이 사라지고, pytest로 수치까지 검증 가능
2. **어휘 출처**: trigger·target·옵션 이름은 lpdlc 것을 정리해서 사용. stat(상태 키워드)는 `keyword` 테이블(484)에서 생성한 enum + 인격 고유 키워드 등록 방식
3. **상태이상은 `(keyword, potency, count)` 쌍**으로 표현. 부여 액션은 `potency=`, `count=`를 따로 받음
4. **피해·위력 수정자는 공식 위치를 명시**: `power(kind=clash|coin|final)`, `damage(pct=..., stage=pre|post)` 등. 위치 이름은 `00-core-rules.md` 용어와 맞춤
5. **`unparsed(원문, 이유)` 노드**: 해석 못 한 문장은 지어내지 말고 이걸로 남김 → 검수 큐로 보냄. 프롬프트 규칙: "원문에 없는 조건 추가 금지"
6. **few-shot은 검증된 spec만 사용**: 손으로 쓴 르누아르 #202를 첫 예시로 함. 예제 5·6 같은 오류 예시가 섞이지 않게
7. **루프 피드백 확장**: 스키마 오류 → pytest 실패 → 역번역 diff 순서로 에러를 돌려줌 (lpdlc의 재생성 프롬프트 형태 재사용)

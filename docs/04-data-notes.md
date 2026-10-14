# 04. 데이터 노트 (P0 결과, 2026-10-13)

수집: 인격 189 · E.G.O 116 · 키워드 사전 444 · 티어(키워드 167 / 리롤 76) — 실패 0건
DB: `data/gesellschaft.db` (스킬 2154행, 코인 3643, 패시브 1080)

## 원본에서 발견한 특이사항과 처리

| 항목 | 내용 | 처리 |
|---|---|---|
| 방어/회피 스킬 속성 | 동기화 3에서는 죄악 속성 "없음", 동기화 4에서 속성이 붙음 (방어 62/66, 회피 33/37건) | NULL 저장. **보유 자원 계산 시 동기화 단계별로 다름** |
| 속성 오타 | "탐 식" 등 공백 섞인 값 | 공백 제거 |
| 변형 스킬 | 공격 슬롯에 변형 스킬이 있는 인격 41개 | 첫 번째 = 덱 스킬로 가정 (`is_deck_skill`). 실제로는 둘 다 덱에 들어가는 경우도 있을 수 있음 → spec 컴파일 시 확정 |
| 덱 장수 | 신형 인격 `quantity` null | 3/2/1로 보정 |
| 인격 내성 | 길이 3 배열 | [참격, 관통, 타격] 순서 (확정). 값은 wiki.gg 우선 (아래 11건) |
| E.G.O 비용 `cost` | 길이 7 배열 (예: `[0,0,2,0,0,2,0]`) | 죄악 7종별 자원 소모량. 분노·색욕·나태·탐식·우울·오만·질투 순서 (확정) |
| E.G.O 내성 | 길이 7 배열 | 같은 순서로 추정 |
| 흐트러짐 선 | baslimbus에 없음 | wiki.gg `IDPage` 템플릿에서 보충 → `identity_ext` (189/189 매칭, 선 1개 35 · 2개 68 · 3개 86) |
| 공격/방어 레벨 보정 | baslimbus `level_text "67(+2)"`, `def_level "65(+4)"`의 괄호 값 | wiki.gg `atkmod`/`defmod`와 일치 → baslimbus 값 사용 |

## wiki.gg 매칭
- 악센트 무시(Ryōshū→ryoshu) + 같은 수감자 내 유사도 매칭(≥0.85) + 수동 별칭 3개(#16, #44, #57)
- 오타 보정 사례: #197 baslimbus 영문명 "Dawn Office Fixe"

## 두 소스의 내성이 다른 인격 (11개) — baslimbus 오류 확정
사용자 확인 결과 baslimbus 쪽 오류. `build_db.py`가 wiki.gg 값으로 `identity.res_*`를 덮어씀 (테스트 `test_identity_resistance_follows_wiki`).

| id | 인격 | baslimbus [참,관,타] | wiki.gg [참,관,타] |
|---|---|---|---|
| 30 | 홍루 콩콩이파 두목 | 보통 내성 취약 | Ineff Normal Fatal |
| 54 | 오티스 남부 섕크 협회 4과 | 내성 취약 보통 | Fatal Ineff Normal |
| 154 | 오티스 T사 3등급 강력징수직 직원 | 내성 취약 보통 | Normal Fatal Ineff |
| 158 | 료슈 N사 E.G.O::경멸, 경외 | 내성 취약 보통 | Normal Ineff Fatal |
| 163 | 그레고르 밤의 송곳 카피타노 | 내성 취약 보통 | Normal Ineff Fatal |
| 166 | 료슈 홍원 방랑무사 | 내성 취약 보통 | Fatal Ineff Normal |
| 167 | 로쟈 R사 제 4무리 순록팀 | 내성 취약 보통 | Normal Fatal Ineff |
| 188 | 뫼르소 약지 야수파 스튜던트 | 내성 보통 취약 | Normal Fatal Ineff |
| 189 | 로쟈 약지 야수파 도슨트 | 내성 보통 취약 | Normal Fatal Ineff |
| 190 | 히스클리프 중지 작은 형님 | 내성 보통 취약 | Fatal Normal Ineff |
| 191 | 료슈 거미집의 검 | 내성 보통 취약 | Ineff Fatal Normal |

관찰: baslimbus 쪽이 "내성 취약 보통" / "내성 보통 취약" 패턴으로 반복 (입력 오류).

## 기타
| 항목 | 내용 | 처리 |
|---|---|---|
| 키워드 티어 | 등급 없이 순서만 있음 | `tier.position`만 저장. 등급(`grade`)은 리롤 티어에만 있음 |

# gesellschaft

림버스 컴퍼니 공략 AI Agent. 인격 / E.G.O 정보 조회 → 전용 메커니즘 분석 → 거울 던전 조합·운용 추천.

## 빠른 시작
```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python scripts/crawl_identities.py --all   # 인격 + E.G.O 원본 (data/raw/)
.venv/Scripts/python scripts/crawl_keywords.py           # 키워드 사전
.venv/Scripts/python scripts/crawl_wikigg.py             # 흐트러짐 선 등 보충 (wiki.gg)
.venv/Scripts/python scripts/crawl_images.py --no-upload # 아이콘·인격·스킬·키워드·E.G.O 이미지 (data/raw/images/)
.venv/Scripts/python scripts/crawl_images.py             # + Cloudflare R2 업로드 (.env에 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY)
.venv/Scripts/python scripts/build_db.py                 # data/gesellschaft.db
.venv/Scripts/streamlit run app/viewer.py                # 뷰어
.venv/Scripts/python -m pytest -q tests                   # 데이터 + 엔진 테스트
.venv/Scripts/python scripts/clash_report.py 204         # 덱 스킬 합 승률 / 기대 피해
```

## 구조
```
scripts/   수집(P0) · 정규화 · 리포트
engine/    전투 엔진 코어: 코인 · 합(정확 계산 + 참조 시뮬) · 피해량 · 보유/공명 (미확정 수치는 params.py)
app/       뷰어 (Streamlit)
data/raw/  API 원본 스냅샷 (identity/, ego/, *_list.json, tier_*.json, keyword_dict.json, images/)
docs/      01 조사 · 02 메커니즘 분석 설계 · 03 기술 아키텍처 · 04 데이터 노트 · 06 이미지 · rules/ 공통 규칙
tests/     데이터 무결성 + 엔진 골든 테스트 (pytest)
```

데이터 출처: [단빵숲 baslimbus.info](https://baslimbus.info) (비공식 팬사이트) — 요청 간 1초 딜레이, 로컬 캐시 후 재사용.

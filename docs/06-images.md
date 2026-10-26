# 06. 이미지 수집 (2026-10-14, PNG 통일·wiki.gg 보충 2026-10-15)

스크립트: `scripts/crawl_images.py` → `data/raw/images/` (+ manifest) → Cloudflare R2 버킷 `gesellschaft`
DB: `image` 테이블 (`build_db.py`가 manifest에서 적재, `crawl_images.py`도 실행 끝에 동기화)

## 출처 조사

모든 카테고리를 baslimbus.info에서 받는다 (baslimbus에 없는 키워드 아이콘 24개만 limbuscompany.wiki.gg). 백엔드 API(`baslimbus.onrender.com`)는 인격/E.G.O 일러스트 URL만 주고,
아이콘류는 프론트(Next.js)의 정적 파일 `/assets/...`에 있다. 파일명 규칙은 프론트 JS 번들에서 확인했다
(번들의 module 13037 = 한글→영문 이름표, 스킬 카드 컴포넌트의 이미지 후보 함수).
`robots.txt`: `Allow: /` (`/admin`, `/api/`만 Disallow).

| 카테고리 | URL 패턴 | 형식 / 크기 | 완전성 |
|---|---|---|---|
| 죄악 속성 7종 | `https://baslimbus.info/assets/resource/{Wrath,Lust,Sloth,Gluttony,Gloom,Pride,Envy}.webp` | webp 256×256 | 7/7 |
| 공격 유형 3종 | `/assets/attackType/{slash,pierce,blunt}.webp` | webp 256×256 | 6/6 (공격 3 + 방어 3) |
| 방어 유형 3종 | `/assets/attackType/{Guard,Evade,Counter}.webp` (방어/회피/반격) | webp 256×256 | (위와 합산) |
| 인격 일러스트 | raw JSON `beforeImage` (`…/{prefix}_normal.webp`), `afterImage` (`…/{prefix}_gacksung.webp`), 호스트 `image.baslimbus.info` | webp 1920×1080 | 기본 189/189, 동기화 후 177/177 (1성 12개는 원본에 없음) |
| 스킬 아이콘 | `/assets/drive/sinners/{Sinner}/{Identity}/{prefix}{seq:02}[_{seq}][ N].png` | png 256×256 | 1626/1694 키 (180/189 인격, 고유 파일 약 820개). 8개 인격 없음 (아래) |
| 키워드 아이콘 | `/assets/keyword/{encodeURIComponent(한글 이름)}.webp`, 없으면 `https://limbuscompany.wiki.gg/images/{영문}.png` | webp / png 128×128 | 444/444 (baslimbus 420 + wiki.gg 24) |
| E.G.O 일러스트 | raw JSON `image` (`…_cg.webp`), `zoomImage` (`…_awaken_profile.webp`), `corImage` (침식) | webp 1024×1024 / 256×256 | 334/336 (침식 2개 404) |

### 스킬 아이콘 파일명 규칙 (프론트 번들에서 이식, `skill_file_candidates`)
- `{Sinner}` = `characterEn`, `{Identity}` = `nameEn`. NFKC 정규화, `::` → 공백, `[]`→`【】`, `()`→`（）` 후 `encodeURIComponent`
- `prefix` = 인격 이미지 파일명 앞 숫자 (예: `10315_normal.webp` → `10315`), `seq` = `skillSeq` (1~3, 방어 4)
- 후보: `X_seq.png`, `X.png`, `X 2.png`, `X 3.png`, `X 4.png` (`X = prefix + seq 두 자리`). 순서는 동기화(3/4)와
  같은 seq의 변형 스킬 순번에 따라 다르고, 프론트는 404가 나면 다음 후보를 쓴다. 크롤러도 같은 순서로 시도한다.
- 스킬 아이콘이 동기화 3/4에서 같은 경우가 대부분이라, 같은 원본은 한 번만 받고 다른 키로 복사한다.

### 없는 것 / 주의
- 동기화 후 일러스트가 없는 인격 12개 (1성 기본 인격: id 1, 7, 14, 21, 28, 35, 42, 49, 55, 62, 68, 76). 원본에 `afterImage`가 없음.
- E.G.O `corImage`는 116개 중 104개만 파일명이 있다 (나머지는 폴더 경로만 있어 건너뜀).
- baslimbus에 키워드 아이콘이 없는 24개는 wiki.gg에서 받는다 (아래 "wiki.gg 보충").
- 인격의 `_info`(확대), `_gacksung_profile` 이미지와 코인 아이콘(`/assets/coin/…`)은 받지 않는다. 필요하면 `plan()`에 추가.
- 스킬 #139 S2(동기화 3/4)는 사이트 원본 `1031102.png`가 0바이트라 없음. 빈 응답·이미지가 아닌 응답은 404와 같이 처리한다.

### wiki.gg 보충 (`WIKIGG_KEYWORD`)
baslimbus 후보가 404이면 `https://limbuscompany.wiki.gg/images/{파일}.png`를 시도한다. 한글 → 파일 이름 표는 손으로 만들었다.
- `출혈/화상/진동/파열/침잠/호흡 횟수` → `Bleed`, `Burn`, `Tremor`, `Rupture`, `Sinking`, `Poise` (게임 안에서 횟수는 본 상태와 같은 아이콘)
- `{죄악} 위력 증가/감소` → `{Wrath…Envy}_Power_Up/Down`, `{참격/관통/타격} 위력 감소` → `{Slash,Pierce,Blunt}_Power_Down`
- 인격 고유 키워드는 wiki.gg 인격 페이지의 `{{StatusEffect|…}}` 이름과 같은 효과 문장을 대조해 정했다:
  `본국검 - 진전 전수` → `Sword_of_the_Homeland_-_The_True_Essence` (#88, #194), #203 `양극성` → `Duality`,
  `표류 관성` → `Inertia_of_Drifting`, `마름질 표식` → `Mark_of_Shearing`, `두 가위` → `Second_is_Welcome-snipping`,
  `꽃가위` → `Blossom_Scissors`
- 파일 검색: MediaWiki API `list=allimages&aiprefix=…`, 인격 페이지는 `action=parse&prop=images|wikitext`.

## R2 키 레이아웃 (ASCII 소문자만, 로컬 `data/raw/images/` 경로와 같음)

```
sin/{wrath|lust|sloth|gluttony|gloom|pride|envy}.png
attack/{slash|pierce|blunt}.png
defense/{guard|evade|counter}.png
identity/{id}/normal.png             # 기본 일러스트
identity/{id}/after.png              # 동기화(각성) 후 일러스트
skill/{identity_id}/{s1|s2|s3|def}-v{variant}-u{uptie}.png
keyword/{slug}.png                   # 주요 키워드는 영문(bleed, burn, tremor, rupture, sinking, poise, charge ...)
                                     # 나머지는 kw-{sha1(한글 이름) 앞 10자}. 한글 이름은 manifest/DB의 key
ego/{id}/{image|zoom|corrosion}.png
```
- 스킬 키의 `(identity_id, slot, variant, uptie)`는 DB `skill` 테이블의 `(owner_id, slot, variant, uptie)`와 1:1이다.
  `skill.id`는 AUTOINCREMENT라 재빌드 때 바뀔 수 있어 키에 쓰지 않았다.
- **형식은 모두 PNG**. 원본이 PNG면 바이트 그대로, webp 등은 Pillow로 변환한다 (알파 유지, 원본 webp가 손실 압축이면
  그 화질 그대로 PNG에 담김). 원본 형식은 manifest `source_format`에 남긴다.
- 용량: webp 원본 약 160MB → PNG 약 1.1GB (identity 674MB, ego 250MB, skill 92MB, keyword 9MB).
  `optimize`/`compress_level=9`는 4% 정도만 줄어 쓰지 않는다.
- 예전 실행이 남긴 `.webp` 항목은 실행 시작 때 `migrate_to_png`가 변환하고 키를 `.png`로 옮긴다 (멱등).
- 업로드 시 `Content-Type: image/png`, `Cache-Control: public, max-age=31536000`, 메타데이터 `sha256`, `source-url`을 붙인다.
- CORS: `scripts/r2_cors.json` (로컬 뷰어 `localhost:8501`에서 GET/HEAD만), `python scripts/r2_cors.py`로 적용.
  버킷은 비공개라 브라우저는 presigned URL로만 읽는다. 버킷 설정 변경에는 Admin Read & Write 토큰이 필요하다.

## manifest / 멱등성
`data/raw/images/manifest.json`
```
items[r2_key] = {kind, owner_type, owner_id, key, source_url, source_format, sha256, width, height, bytes,
                 uploaded_sha256, uploaded_at}      # sha256 / bytes는 저장된 PNG 기준
missing[url]  = 404 확인 날짜
```
- 다운로드: 로컬 파일 sha256이 manifest와 같으면 건너뜀. 404 URL은 기록해 두고 다시 요청하지 않음 (`--refresh`로 재시도).
- 업로드: `uploaded_sha256 == sha256`이면 건너뜀. manifest에 업로드 기록이 없으면 `HEAD`로 버킷 객체의 메타데이터 sha256을 비교.
- 버킷에서 지우는 동작은 없다.
- 요청 간 1초 딜레이, User-Agent `gesellschaft-image-crawler/0.1 (...)`.

## 실행
```bash
.venv/Scripts/python scripts/crawl_images.py --dry-run                 # 계획만
.venv/Scripts/python scripts/crawl_images.py --no-upload --limit 3     # 카테고리별 3개 시험
.venv/Scripts/python scripts/crawl_images.py --no-upload               # 전체 다운로드 (약 1.5~2시간)
.venv/Scripts/python scripts/crawl_images.py --only sin,attack         # 일부만 + 업로드
```
R2 자격 증명은 환경 변수 또는 프로젝트 루트 `.env`(gitignore됨)에서만 읽는다:
```
R2_ACCESS_KEY_ID=...
R2_SECRET_ACCESS_KEY=...
R2_ENDPOINT=https://289bd15c1bc1ecf231ca83a83bcdb51a.r2.cloudflarestorage.com   # 선택 (기본값)
R2_BUCKET=gesellschaft                                                          # 선택 (기본값)
```

## 수집 결과
2026-10-14 전체 실행, 오류 0건. 2026-10-15 PNG 변환 + wiki.gg 보충 후 총 2783개 / 약 1.1GB (`data/raw/images/`).
**R2 업로드는 아직 안 함** (자격 증명 미설정).

| 카테고리 | 받음 / 대상 | 없음 (404) |
|---|---|---|
| sin | 7 / 7 | 0 |
| attack + defense | 6 / 6 | 0 |
| identity | 366 / 366 | 0 |
| skill | 1626 / 1694 | 68 |
| keyword | 444 / 444 | 0 (wiki.gg 24) |
| ego | 334 / 336 | 2 |

- 스킬 아이콘이 없는 인격 8개 (프론트와 같은 규칙으로 모든 후보가 404, 사이트에서도 안 보이는 것으로 추정):
  #2 Shi Assoc. South Section 5 Director, #3 W Corp. L3 Cleanup Agent, #8 W Corp. L2 Cleanup Agent, #9 Lobotomy Corp. Remnant,
  #16 G Corp. Corporal Manager, #57 Kurokumo Henchwoman, #157 Heishou Pack - Wu Branch, #171 The House of Spiders The Middle Apprentice
  (#63은 `nameEn`에 밑줄이 섞여 있어 공백 치환 폴더로 받음)
- 스킬 #139 S2 u3/u4: 원본 파일이 0바이트.
- E.G.O 침식 일러스트 없음: #21, #58 (Dimension Shredder `_e_cg.webp`).

## 라이선스
모든 이미지는 Project Moon의 게임 에셋이고, 팬사이트(baslimbus.info, limbuscompany.wiki.gg)가 재호스팅한 것이다.
**결정 (2026-10-15): 개인 공략 도구 내부 용도로만 쓰고, R2 버킷은 비공개로 둔다** (public bucket / 커스텀 도메인 열지 않음).

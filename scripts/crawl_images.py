"""게임 기본 이미지 수집 → data/raw/images/ → Cloudflare R2 업로드.

출처는 baslimbus.info, 거기 없는 키워드 아이콘만 limbuscompany.wiki.gg (docs/06-images.md 참고).
저장 형식은 모두 PNG로 통일한다 (webp 원본은 Pillow로 무손실 변환, 원본이 PNG면 바이트 그대로).
  sin       죄악 속성 아이콘 7종     {SITE}/assets/resource/{Wrath|Lust|...}.webp
  attack    공격/방어 유형 아이콘 6종 {SITE}/assets/attackType/{slash|pierce|blunt|Guard|Evade|Counter}.webp
  identity  인격 일러스트 (기본/동기화 후)  raw JSON beforeImage / afterImage (image.baslimbus.info)
  skill     인격 스킬 아이콘          {SITE}/assets/drive/sinners/{Sinner}/{Identity}/{prefix}{seq:02}[ N].png
            (프론트 JS와 같은 후보 순서로 시도, 첫 번째로 받아지는 파일 사용)
  keyword   키워드/상태 아이콘        {SITE}/assets/keyword/{한글 이름}.webp
                                      없으면 {WIKI}/images/{영문 파일}.png (WIKIGG_KEYWORD 표)
  ego       E.G.O 일러스트            raw JSON image / zoomImage / corImage

R2 키 레이아웃 (ASCII만 사용, 전부 .png, 로컬 data/raw/images/ 아래도 같은 경로):
  sin/{wrath|lust|sloth|gluttony|gloom|pride|envy}.png
  attack/{slash|pierce|blunt}.png, defense/{guard|evade|counter}.png
  identity/{id}/{normal|after}.png
  skill/{identity_id}/{s1|s2|s3|def}-v{variant}-u{uptie}.png   (DB skill 행의 owner_id, slot, variant, uptie와 1:1)
  keyword/{english-slug | kw-<sha1 10자>}.png                  (한글 이름 ↔ 키는 manifest의 key 필드)
  ego/{id}/{image|zoom|corrosion}.png

상태: data/raw/images/manifest.json  (build_db.py가 `image` 테이블로 적재, 이 스크립트도 끝나면 DB에 동기화)
  items[r2_key]  = {kind, owner_type, owner_id, key, source_url, source_format, sha256, width, height, bytes,
                    uploaded_sha256, uploaded_at}       (sha256/bytes는 저장된 PNG 기준)
  missing[url]   = 404 확인 날짜 (다음 실행에서 다시 요청하지 않음, --refresh로 재시도)

R2 자격 증명은 환경 변수 또는 프로젝트 루트 .env 에서만 읽는다:
  R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, (선택) R2_ENDPOINT, R2_BUCKET

사용법:
  python scripts/crawl_images.py --no-upload --limit 3          # 카테고리별 3개만 받아 URL 확인
  python scripts/crawl_images.py --no-upload                    # 전체 다운로드
  python scripts/crawl_images.py                                # 다운로드 + R2 업로드 (이미 올린 건 건너뜀)
  python scripts/crawl_images.py --only sin,attack --dry-run    # 계획만 출력 (네트워크/파일 쓰기 없음)
"""
import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sqlite3
import struct
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_db  # noqa: E402  (ID_SLOTS, variants, image 테이블 DDL 재사용)

SITE = "https://baslimbus.info"
WIKI = "https://limbuscompany.wiki.gg"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
IMG_DIR = RAW / "images"
MANIFEST = IMG_DIR / "manifest.json"
DB = ROOT / "data" / "gesellschaft.db"
DELAY_SEC = 1.0
UA = "gesellschaft-image-crawler/0.1 (personal Limbus Company strategy research; 1 req/s)"
DEFAULT_ENDPOINT = "https://289bd15c1bc1ecf231ca83a83bcdb51a.r2.cloudflarestorage.com"
DEFAULT_BUCKET = "gesellschaft"
KINDS = ("sin", "attack", "identity", "skill", "keyword", "ego")
CONTENT_TYPES = {".webp": "image/webp", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                 ".gif": "image/gif", ".svg": "image/svg+xml"}

# 한글 → 사이트 파일명 (프론트 번들 module 13037의 name/eng_name 표)
SINS = [("분노", "Wrath"), ("색욕", "Lust"), ("나태", "Sloth"), ("탐식", "Gluttony"),
        ("우울", "Gloom"), ("오만", "Pride"), ("질투", "Envy")]
ATTACKS = [("참격", "slash", "attack"), ("관통", "pierce", "attack"), ("타격", "blunt", "attack"),
           ("방어", "Guard", "defense"), ("회피", "Evade", "defense"), ("반격", "Counter", "defense")]
# 자주 쓰는 키워드는 읽기 쉬운 영문 키. 나머지는 이름 해시 (manifest key 필드에 한글 이름 보관)
KEYWORD_SLUGS = {
    "출혈": "bleed", "출혈 횟수": "bleed-count", "화상": "burn", "화상 횟수": "burn-count",
    "진동": "tremor", "진동 횟수": "tremor-count", "진동 폭발": "tremor-burst",
    "파열": "rupture", "파열 횟수": "rupture-count", "침잠": "sinking", "침잠 횟수": "sinking-count",
    "호흡": "poise", "호흡 횟수": "poise-count", "충전": "charge", "충전 횟수": "charge-count",
    "마비": "paralysis", "취약": "fragile", "보호": "protection", "신속": "haste", "속박": "bind",
    "공명": "resonance", "도발치": "aggro", "탄환": "ammo", "정신력 회복": "sanity-recovery",
    "체력 회복": "hp-recovery", "파괴 불가 코인": "unbreakable-coin", "가중치 증가": "weight-up",
    "피해량 증가": "damage-up", "피해량 감소": "damage-down",
    "공격 위력 증가": "offense-power-up", "공격 위력 감소": "offense-power-down",
    "수비 위력 증가": "defense-power-up", "수비 위력 감소": "defense-power-down",
    "공격 레벨 증가": "offense-level-up", "공격 레벨 감소": "offense-level-down",
    "방어 레벨 증가": "defense-level-up", "방어 레벨 감소": "defense-level-down",
}
# baslimbus에 아이콘이 없는 키워드 → wiki.gg 파일 이름 (게임 안에서 "~ 횟수"는 본 상태와 같은 아이콘)
WIKIGG_KEYWORD = {
    "출혈 횟수": "Bleed", "화상 횟수": "Burn", "진동 횟수": "Tremor", "파열 횟수": "Rupture",
    "침잠 횟수": "Sinking", "호흡 횟수": "Poise",
    **{f"{ko} 위력 증가": f"{en}_Power_Up" for ko, en in SINS},
    **{f"{ko} 위력 감소": f"{en}_Power_Down" for ko, en in SINS},
    **{f"{ko} 위력 증가": f"{en.capitalize()}_Power_Up" for ko, en, g in ATTACKS if g == "attack"},
    **{f"{ko} 위력 감소": f"{en.capitalize()}_Power_Down" for ko, en, g in ATTACKS if g == "attack"},
    # 인격 고유 (wiki.gg 인격 페이지의 StatusEffect 이름과 대조)
    "본국검 - 진전 전수": "Sword_of_the_Homeland_-_The_True_Essence",   # #88, #194
    "양극성": "Duality", "표류 관성": "Inertia_of_Drifting", "마름질 표식": "Mark_of_Shearing",  # #203
    "두 가위": "Second_is_Welcome-snipping", "꽃가위": "Blossom_Scissors",                    # #203
}
SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9/_.-]*$")


@dataclass
class Target:
    kind: str
    owner_type: str | None
    owner_id: int | None
    key: str                      # 사람이 읽는 원래 이름 (예: "우울", "S1 v0 u4", "normal")
    r2_key: str
    candidates: list = field(default_factory=list)   # 시도할 원본 URL (앞에서부터)


# ---------------------------------------------------------------- 키 / URL 규칙 (네트워크 없음)
def ext_of(url: str, default: str = ".webp") -> str:
    path = urllib.parse.urlparse(url).path
    e = os.path.splitext(path)[1].lower()
    return e if e in CONTENT_TYPES else default


def keyword_slug(name: str) -> str:
    if name in KEYWORD_SLUGS:
        return KEYWORD_SLUGS[name]
    return "kw-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]


def r2_key(kind: str, **kw) -> str:
    """카테고리별 R2 키. ASCII 소문자/숫자/`/_.-`만 나오도록 보장한다. 형식은 항상 PNG."""
    ext = ".png"
    if kind == "sin":
        k = f"sin/{kw['en'].lower()}{ext}"
    elif kind == "attack":
        k = f"{kw['group']}/{kw['en'].lower()}{ext}"
    elif kind == "identity":
        k = f"identity/{int(kw['id'])}/{kw['variant']}{ext}"
    elif kind == "skill":
        k = f"skill/{int(kw['id'])}/{kw['slot'].lower()}-v{int(kw['variant'])}-u{int(kw['uptie'])}{ext}"
    elif kind == "keyword":
        k = f"keyword/{keyword_slug(kw['name'])}{ext}"
    elif kind == "ego":
        k = f"ego/{int(kw['id'])}/{kw['variant']}{ext}"
    else:
        raise ValueError(kind)
    if not SAFE_KEY.match(k):
        raise ValueError(f"unsafe key {k!r}")
    return k


def keyword_candidates(name: str) -> list:
    out = [f"{SITE}/assets/keyword/{js_uri(name)}.webp"]
    if name in WIKIGG_KEYWORD:
        out.append(f"{WIKI}/images/{urllib.parse.quote(WIKIGG_KEYWORD[name])}.png")
    return out


def js_uri(s: str) -> str:
    """JS encodeURIComponent와 같은 인코딩."""
    return urllib.parse.quote(s, safe="-_.!~*'()")


def normalize_folder(s: str) -> str:
    """프론트 함수 N(): 스킬 이미지 폴더명 정규화."""
    t = unicodedata.normalize("NFKC", s)
    t = re.sub(r"[\s   -   　]+", " ", t)
    t = re.sub(r"(?:\s*[:：]\s*){2,}", " ", t)
    t = t.replace("[", "【").replace("]", "】").replace("［", "【").replace("］", "】")
    t = t.replace("(", "（").replace(")", "）")
    return re.sub(r"\s+", " ", t).strip()


def _dedupe(*lists):
    seen, out = set(), []
    for lst in lists:
        for x in lst:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def skill_file_candidates(prefix: str, seq: int, sync: int, rank, count) -> list:
    """프론트 스킬 카드의 이미지 후보 파일명 순서 (번들 함수 그대로 이식).
    prefix: 인격 이미지 파일명 앞 숫자(예: 10315), seq: skillSeq(1~3, 방어 4), sync: 0=동기화3 / 1=동기화4,
    rank/count: 같은 seq를 가진 스킬 그룹 수(count)와 그 안에서의 순번(rank, count<2면 None)."""
    l = f"{prefix}{seq:02d}"
    n, o, c, u, d, h = f"{l}_{seq}.png", f"{l}.png", f"{l} 2.png", f"{l} 3.png", f"{l} 4.png", f"{l}_{seq} 2.png"
    count = count or 0
    if seq == 4 and count >= 2 and rank is not None:
        return [o, n, c, u, h] if rank == 0 else [c, u, n, o, h] if rank == 1 else [u, c, n, o, h]
    if count >= 2 and rank is not None:
        e = [c, u, d, n, o] if sync == 1 else [n, o, c, u, d]
        if 1 <= seq <= 3:
            if rank == 0:
                return _dedupe([o, n, c, u, d], e)
            if rank == 1:
                return _dedupe([c, u, d, n, o], [n, o, c, u, d], e)
            if rank == 2:
                return _dedupe([u, d, c, n, o], [n, o, c, u, d], e)
            return _dedupe([d, u, c, n, o], [n, o, c, u, d], e)
        return [o, n, c, u] if rank == 0 else [n, o, c, u]
    return [c, u, n, o] if sync == 1 else [n, o, c, u]


def image_prefix(url: str):
    stem = urllib.parse.urlparse(url or "").path.rsplit("/", 1)[-1].split(".")[0]
    p = stem.split("_")[0]
    return p if p.isdigit() else None


def skill_groups(d: dict):
    """프론트와 같은 방식으로 스킬 카드 그룹을 만든다: 슬롯 목록을 레벨별로 나눠 i번째끼리 묶음.
    반환: [(slot, [entry...], rank, count)]"""
    groups = []
    for key, slot in build_db.ID_SLOTS:
        lst = d.get(key) or []
        if not lst:
            continue
        levels = list(dict.fromkeys(s.get("level") for s in lst))
        if len(levels) <= 1:
            groups += [(slot, [s]) for s in lst]
        else:
            by = {lv: [s for s in lst if s.get("level") == lv] for lv in levels}
            for i in range(len(by[levels[0]])):
                groups.append((slot, [by[lv][i] for lv in levels if i < len(by[lv])]))
    count = {}
    for _, g in groups:
        s = g[0].get("skillSeq") or 0
        if s > 0:
            count[s] = count.get(s, 0) + 1
    out, ranks = [], {}
    for slot, g in groups:
        s = g[0].get("skillSeq") or 0
        rank = None
        if s > 0 and count.get(s, 0) >= 2:
            rank = ranks.get(s, 0)
            ranks[s] = rank + 1
        out.append((slot, g, rank, count.get(s)))
    return out


def skill_targets(d: dict) -> list:
    prefix = image_prefix(d.get("beforeImage"))
    sinner, ident = (d.get("characterEn") or "").strip(), (d.get("nameEn") or "").strip()
    if not prefix or not sinner or not ident:
        return []
    # 폴더명: nameEn 그대로(프론트 기본) → 밑줄을 공백으로 바꾼 것(프론트 S(), 예: #63 "Seven_Assoc._South_Section_6")
    folders = list(dict.fromkeys(normalize_folder(x) for x in (ident, ident.replace("_-_", " - ").replace("_", " "))))
    bases = [f"{SITE}/assets/drive/sinners/{js_uri(normalize_folder(sinner))}/{js_uri(f)}/" for f in folders]
    variant_of = {}
    for key, slot in build_db.ID_SLOTS:
        for v, s in build_db.variants(d.get(key) or [], "level"):
            variant_of[id(s)] = v
    out, seen = [], set()
    for slot, g, rank, count in skill_groups(d):
        for s in g:
            uptie = s.get("level") or 3
            k = r2_key("skill", id=d["id"], slot=slot, variant=variant_of[id(s)], uptie=uptie)
            if k in seen:
                continue
            seen.add(k)
            files = skill_file_candidates(prefix, s.get("skillSeq") or 0, 1 if uptie >= 4 else 0, rank, count)
            out.append(Target("skill", "identity", d["id"], f"{slot} v{variant_of[id(s)]} u{uptie} {s['name']}",
                              k, [b + js_uri(f) for b in bases for f in files]))
    return out


def load_raw(kind):
    d = RAW / kind
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"), key=lambda p: int(p.stem))]


def plan(kind: str) -> list:
    if kind == "sin":
        return [Target("sin", None, None, ko, r2_key("sin", en=en), [f"{SITE}/assets/resource/{en}.webp"])
                for ko, en in SINS]
    if kind == "attack":
        return [Target("attack", None, None, ko, r2_key("attack", group=g, en=en),
                       [f"{SITE}/assets/attackType/{en}.webp"]) for ko, en, g in ATTACKS]
    if kind == "identity":
        out = []
        for d in load_raw("identity"):
            for variant, f in (("normal", "beforeImage"), ("after", "afterImage")):
                u = (d.get(f) or "").strip()
                if u:
                    out.append(Target("identity", "identity", d["id"], variant,
                                      r2_key("identity", id=d["id"], variant=variant), [u]))
        return out
    if kind == "skill":
        return [t for d in load_raw("identity") for t in skill_targets(d)]
    if kind == "keyword":
        names = json.loads((RAW / "keyword_dict.json").read_text(encoding="utf-8"))
        return [Target("keyword", None, None, n, r2_key("keyword", name=n), keyword_candidates(n)) for n in names]
    if kind == "ego":
        out = []
        for d in load_raw("ego"):
            for variant, f in (("image", "image"), ("zoom", "zoomImage"), ("corrosion", "corImage")):
                u = (d.get(f) or "").strip()
                if u and ext_of(u, "") in CONTENT_TYPES:   # corImage는 파일명 없이 폴더만 있는 경우가 많다
                    out.append(Target("ego", "ego", d["id"], variant,
                                      r2_key("ego", id=d["id"], variant=variant), [u]))
        return out
    raise ValueError(kind)


# ---------------------------------------------------------------- manifest
def load_manifest(path: Path = MANIFEST) -> dict:
    if path.exists():
        m = json.loads(path.read_text(encoding="utf-8"))
    else:
        m = {}
    m.setdefault("version", 1)
    m.setdefault("items", {})
    m.setdefault("missing", {})
    return m


def save_manifest(m: dict, path: Path = MANIFEST):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(m, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def sha256_file(p: Path) -> str | None:
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def needs_download(entry: dict | None, local: Path, refresh: bool = False) -> bool:
    """로컬 파일이 있고 manifest의 sha256과 같으면 다시 받지 않는다."""
    if refresh or not entry:
        return True
    return sha256_file(local) != entry.get("sha256")


def needs_upload(entry: dict | None) -> bool:
    """마지막으로 올린 내용(sha256)과 지금 내용이 같으면 다시 올리지 않는다."""
    return bool(entry and entry.get("sha256")) and entry.get("uploaded_sha256") != entry["sha256"]


def image_size(b: bytes):
    """PNG / WebP / JPEG 헤더에서 (width, height). 모르면 (None, None)."""
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", b[16:24])
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            c = b[12:16]
            if c == b"VP8 ":
                w, h = struct.unpack("<HH", b[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if c == b"VP8L":
                v = int.from_bytes(b[21:25], "little")
                return (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
            if c == b"VP8X":
                return int.from_bytes(b[24:27], "little") + 1, int.from_bytes(b[27:30], "little") + 1
        if b[:2] == b"\xff\xd8":
            i = 2
            while i < len(b) - 9:
                if b[i] != 0xFF:
                    i += 1
                    continue
                marker, ln = b[i + 1], struct.unpack(">H", b[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", b[i + 5:i + 9])
                    return w, h
                i += 2 + ln
    except struct.error:
        pass
    return None, None


PNG_SIG = b"\x89PNG\r\n\x1a\n"


def source_format(b: bytes) -> str:
    if b[:8] == PNG_SIG:
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[:2] == b"\xff\xd8":
        return "jpeg"
    if b[:4] == b"GIF8":
        return "gif"
    return "unknown"


def to_png(b: bytes) -> bytes:
    """PNG면 그대로, 아니면 Pillow로 변환 (알파 유지, 무손실)."""
    if b[:8] == PNG_SIG:
        return b
    from PIL import Image
    with Image.open(io.BytesIO(b)) as im:
        im.load()
        if im.mode not in ("RGB", "RGBA", "L", "LA", "P"):
            im = im.convert("RGBA" if "A" in im.getbands() or im.info.get("transparency") is not None else "RGB")
        out = io.BytesIO()
        im.save(out, "PNG")
    return out.getvalue()


def migrate_to_png(m: dict) -> int:
    """예전 실행이 남긴 .webp/.jpg 항목을 PNG로 변환하고 키를 .png로 옮긴다 (이미 옮겼으면 아무것도 안 함)."""
    n = 0
    for old in [k for k in m["items"] if not k.endswith(".png")]:
        e = m["items"].pop(old)
        new = os.path.splitext(old)[0] + ".png"
        src, dst = IMG_DIR / old, IMG_DIR / new
        if src.exists():
            raw = src.read_bytes()
            body = to_png(raw)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(body)
            src.unlink()
            e.update(source_format=source_format(raw), sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
            e["width"], e["height"] = image_size(body)
        else:  # 로컬 파일이 없으면 다음 다운로드에서 다시 받게 sha256을 비운다
            e["sha256"] = None
        m["items"].setdefault(new, e)
        n += 1
    return n


# ---------------------------------------------------------------- 네트워크
class NotFound(Exception):
    pass


def fetch(url: str, retries: int = 3) -> tuple[bytes, str]:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "image/*"})
            with urllib.request.urlopen(req, timeout=60) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                body = r.read()
            if not ctype.startswith("image/"):
                raise NotFound(f"not an image ({ctype})")
            if source_format(body) == "unknown":   # 0바이트 응답 등 (예: #139 S2 원본 1031102.png)
                raise NotFound(f"empty or unknown image ({len(body)} B)")
            return body, ctype
        except urllib.error.HTTPError as e:
            if e.code in (403, 404, 410):
                raise NotFound(f"HTTP {e.code}") from None
            err = e
        except NotFound:
            raise
        except Exception as e:  # 타임아웃/연결 오류는 재시도
            err = e
        if attempt < retries - 1:
            print(f"  retry {attempt + 1} {url}: {err}", flush=True)
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(str(err))


def download(t: Target, m: dict, refresh: bool, stats: dict, by_url: dict):
    local = IMG_DIR / t.r2_key
    entry = m["items"].get(t.r2_key)
    if not needs_download(entry, local, refresh):
        stats["cached"] += 1
        return
    body, fmt = None, None
    for url in t.candidates:
        # 같은 원본을 다른 키(동기화 3/4 공용 스킬 아이콘 등)로 이미 받았으면 네트워크 없이 복사
        src = by_url.get(url)
        if src and not refresh and src.exists():
            body, used = src.read_bytes(), url
            fmt = (m["items"].get(src.relative_to(IMG_DIR).as_posix()) or {}).get("source_format")
            break
        if url in m["missing"] and not refresh:
            continue
        try:
            body, _ = fetch(url)
            used = url
            break
        except NotFound:
            m["missing"][url] = dt.date.today().isoformat()
        except RuntimeError as e:
            print(f"  FAIL {t.r2_key} {url}: {e}", flush=True)
            stats["errors"].append(t.r2_key)
            return
        finally:
            if url not in by_url:
                time.sleep(DELAY_SEC)
    if body is None:
        stats["missing"].append(t.r2_key)
        return
    m["missing"].pop(used, None)
    fmt = fmt or source_format(body)
    body = to_png(body)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(body)
    by_url[used] = local
    w, h = image_size(body)
    old = entry or {}
    m["items"][t.r2_key] = {
        "kind": t.kind, "owner_type": t.owner_type, "owner_id": t.owner_id, "key": t.key,
        "source_url": used, "source_format": fmt, "sha256": hashlib.sha256(body).hexdigest(), "width": w, "height": h,
        "bytes": len(body), "uploaded_sha256": old.get("uploaded_sha256"), "uploaded_at": old.get("uploaded_at"),
    }
    stats["downloaded"] += 1
    print(f"  ok {t.r2_key} ({len(body)} B, {w}x{h})", flush=True)


# ---------------------------------------------------------------- R2
def load_env(path: Path = ROOT / ".env"):
    """간단한 .env 파서 (KEY=VALUE, # 주석). 이미 설정된 환경 변수는 덮어쓰지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def r2_client():
    load_env()
    if not (os.environ.get("R2_ACCESS_KEY_ID") and os.environ.get("R2_SECRET_ACCESS_KEY")):
        return None, None
    import boto3
    from botocore.config import Config
    c = boto3.client("s3", endpoint_url=os.environ.get("R2_ENDPOINT") or DEFAULT_ENDPOINT,
                     aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
                     aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
                     region_name="auto", config=Config(signature_version="s3v4", retries={"max_attempts": 5}))
    return c, os.environ.get("R2_BUCKET") or DEFAULT_BUCKET


def upload(client, bucket, key: str, entry: dict, stats: dict):
    local = IMG_DIR / key
    if not needs_upload(entry) or not local.exists():
        stats["upload_skipped"] += 1
        return
    try:  # manifest가 없어져도 버킷에 같은 내용이 있으면 건너뜀 (메타데이터 sha256 비교)
        head = client.head_object(Bucket=bucket, Key=key)
        if head.get("Metadata", {}).get("sha256") == entry["sha256"]:
            entry["uploaded_sha256"] = entry["sha256"]
            entry["uploaded_at"] = entry.get("uploaded_at") or head["LastModified"].isoformat()
            stats["upload_skipped"] += 1
            return
    except client.exceptions.ClientError:
        pass
    client.put_object(Bucket=bucket, Key=key, Body=local.read_bytes(),
                      ContentType=CONTENT_TYPES.get(Path(key).suffix.lower(), "application/octet-stream"),
                      CacheControl="public, max-age=31536000",
                      Metadata={"sha256": entry["sha256"], "source-url": entry["source_url"][:1000]})
    entry["uploaded_sha256"] = entry["sha256"]
    entry["uploaded_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    stats["uploaded"] += 1
    print(f"  up {key}", flush=True)


# ---------------------------------------------------------------- DB 동기화
def sync_db(m: dict):
    if not DB.exists():
        return
    con = sqlite3.connect(DB)
    con.executescript(build_db.IMAGE_DDL)
    con.execute("DELETE FROM image")
    build_db.insert_images(con.cursor(), m)
    con.commit()
    con.close()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--only", default=",".join(KINDS), help=f"쉼표 구분: {','.join(KINDS)}")
    p.add_argument("--limit", type=int, default=0, help="카테고리별 최대 대상 수 (0=전체)")
    p.add_argument("--dry-run", action="store_true", help="계획만 출력, 네트워크/파일 쓰기 없음")
    p.add_argument("--no-upload", action="store_true", help="다운로드만")
    p.add_argument("--refresh", action="store_true", help="받은 파일/404 기록도 무시하고 다시 요청")
    a = p.parse_args(argv)

    kinds = [k.strip() for k in a.only.split(",") if k.strip()]
    bad = set(kinds) - set(KINDS)
    if bad:
        p.error(f"unknown kind: {sorted(bad)}")
    m = load_manifest()
    if not a.dry_run:
        n = migrate_to_png(m)
        if n:
            save_manifest(m)
            print(f"[migrate] {n} files converted to PNG", flush=True)
    targets = {k: plan(k)[: a.limit or None] for k in kinds}

    if a.dry_run:
        for k, ts in targets.items():
            todo = [t for t in ts if needs_download(m["items"].get(t.r2_key), IMG_DIR / t.r2_key, a.refresh)]
            up = [t for t in ts if needs_upload(m["items"].get(t.r2_key))]
            print(f"[{k}] targets {len(ts)}| to download {len(todo)}| to upload {len(up)}")
            for t in ts[:3]:
                print(f"   {t.r2_key}  <-  {t.candidates[0]}" + (f" (+{len(t.candidates) - 1} fallbacks)" if len(t.candidates) > 1 else ""))
        return 0

    by_url = {}
    for key, e in m["items"].items():
        if e.get("source_url"):
            by_url.setdefault(e["source_url"], IMG_DIR / key)
    summary = {}
    for k, ts in targets.items():
        print(f"[{k}] {len(ts)} targets", flush=True)
        st = {"downloaded": 0, "cached": 0, "missing": [], "errors": []}
        for i, t in enumerate(ts, 1):
            download(t, m, a.refresh, st, by_url)
            if i % 25 == 0:
                save_manifest(m)
        save_manifest(m)
        summary[k] = st
        print(f"[{k}] downloaded {st['downloaded']}| cached {st['cached']}| missing {len(st['missing'])}"
              f"| errors {len(st['errors'])}", flush=True)

    if not a.no_upload:
        client, bucket = r2_client()
        if client is None:
            print("\nR2 업로드 건너뜀: 자격 증명이 없습니다. 환경 변수 또는 .env 에 다음을 설정하세요:\n"
                  "  R2_ACCESS_KEY_ID=...\n  R2_SECRET_ACCESS_KEY=...\n"
                  f"  (선택) R2_ENDPOINT={DEFAULT_ENDPOINT}\n  (선택) R2_BUCKET={DEFAULT_BUCKET}\n"
                  "그 다음: .venv/Scripts/python scripts/crawl_images.py", flush=True)
        else:
            ust = {"uploaded": 0, "upload_skipped": 0}
            for k, ts in targets.items():
                for i, t in enumerate(ts, 1):
                    e = m["items"].get(t.r2_key)
                    if e:
                        upload(client, bucket, t.r2_key, e, ust)
                    if i % 25 == 0:
                        save_manifest(m)
            save_manifest(m)
            print(f"[upload] uploaded {ust['uploaded']}| skipped {ust['upload_skipped']} -> bucket {bucket}")

    sync_db(m)
    for k, st in summary.items():
        if st["missing"]:
            print(f"missing {k} ({len(st['missing'])}): {st['missing'][:10]}{' ...' if len(st['missing']) > 10 else ''}")
        if st["errors"]:
            print(f"errors {k}: {st['errors']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""data/raw/*.json → data/gesellschaft.db (SQLite) 정규화.

원본 API 구조의 특이사항 보정:
  - 스킬이 동기화 레벨(3/4)별로 중복 수록 → (slot, variant, uptie) 단위로 저장
  - 같은 슬롯의 여러 스킬 = 변형 스킬. variant 0을 덱 스킬로 가정 (is_deck_skill), 나머지는 조건부/패시브 발동
  - 신형 인격은 quantity가 null → S1/S2/S3 = 3/2/1 로 보정
  - 스킬 resource "없음" → NULL (동기화 3 방어/회피 스킬은 속성 없음, 동기화 4에서 생김)
  - 인격 resistance 배열은 [참격, 관통, 타격] 순서 (사용자 확인)
  - 흐트러짐 선 / 공격·방어 레벨 보정은 baslimbus에 없어 wiki.gg(data/raw/wikigg_identity.json)에서 매칭해 보충
  - 이미지 목록(image 테이블)은 data/raw/images/manifest.json 에서 적재 (scripts/crawl_images.py가 만듦)

사용법: python scripts/build_db.py
"""
import difflib
import hashlib
import json
import re
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DB = ROOT / "data" / "gesellschaft.db"

SCHEMA = """
CREATE TABLE identity (
  id INTEGER PRIMARY KEY, sinner TEXT, sinner_en TEXT, name TEXT, name_en TEXT,
  grade INTEGER, season INTEGER, release_date TEXT, obtaining TEXT,
  hp INTEGER, speed_min INTEGER, speed_max INTEGER, def_level TEXT,
  res_slash TEXT, res_pierce TEXT, res_blunt TEXT,
  image TEXT, image_after TEXT, source_hash TEXT
);
CREATE TABLE identity_ext (       -- wiki.gg 보충 데이터
  identity_id INTEGER PRIMARY KEY, wiki_title TEXT,
  stagger_json TEXT,       -- 흐트러짐 선 (최대 체력 %), 예: [65, 35]
  def_mod INTEGER, atk_mod_json TEXT,   -- 방어 레벨 보정, 스킬별 공격 레벨 보정 {"S1": 3, ...}
  hp_base REAL, hp_growth REAL,
  res_en_json TEXT         -- 영문 내성 등급 [참, 관, 타] (Fatal/Normal/Ineff)
);
CREATE TABLE ego (
  id INTEGER PRIMARY KEY, sinner TEXT, sinner_en TEXT, name TEXT, name_en TEXT,
  grade TEXT, season INTEGER, release_date TEXT, obtaining TEXT,
  resistance_json TEXT, cost_json TEXT, image TEXT, source_hash TEXT
);
CREATE TABLE skill (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_type TEXT, owner_id INTEGER,
  slot TEXT,            -- S1 S2 S3 DEF / EGO EGO_COR
  variant INTEGER, uptie INTEGER, is_deck_skill INTEGER, quantity INTEGER,
  name TEXT, atk_type TEXT, sin TEXT, level_text TEXT,
  base INTEGER, coin_power INTEGER, coin_num INTEGER, weight INTEGER,
  sanity_cost INTEGER, effect TEXT, playable_desc TEXT
);
CREATE TABLE coin (skill_id INTEGER, idx INTEGER, effect TEXT, PRIMARY KEY (skill_id, idx));
CREATE TABLE passive (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_type TEXT, owner_id INTEGER,
  kind TEXT,            -- main / support / ego
  uptie INTEGER, name TEXT,
  cond_sins TEXT,       -- JSON 배열 ["분노","오만"]
  cond_count INTEGER, cond_type TEXT,   -- 보유 / 공명 / NULL
  effect TEXT
);
CREATE TABLE tag (owner_type TEXT, owner_id INTEGER, kind TEXT, value TEXT);  -- kind: keyword / affiliation
CREATE TABLE keyword (name TEXT PRIMARY KEY, description TEXT, usage_count INTEGER);
CREATE TABLE tier (identity_id INTEGER, list TEXT, position INTEGER, grade TEXT, season_type TEXT, before_season INTEGER);  -- grade는 reroll 티어에만 있음
CREATE INDEX ix_skill_owner ON skill(owner_type, owner_id);
CREATE INDEX ix_passive_owner ON passive(owner_type, owner_id);
CREATE INDEX ix_tag ON tag(kind, value);
CREATE VIRTUAL TABLE effect_fts USING fts5(owner_type, owner_id UNINDEXED, source, text);
"""

# 이미지 (원본 상태는 data/raw/images/manifest.json, 이 테이블은 조회용 사본). crawl_images.py도 재사용
IMAGE_DDL = """
CREATE TABLE IF NOT EXISTS image (
  r2_key TEXT PRIMARY KEY,   -- 예: identity/204/normal.webp, skill/204/s1-v0-u4.png (docs/06-images.md)
  kind TEXT,                 -- sin / attack / identity / skill / keyword / ego
  owner_type TEXT, owner_id INTEGER,   -- identity / ego (아이콘류는 NULL)
  key TEXT,                  -- 원래 이름 (우울, 출혈, normal, "S1 v0 u4 스킬명" ...)
  source_url TEXT, sha256 TEXT, width INTEGER, height INTEGER, bytes INTEGER,
  uploaded_at TEXT           -- R2 업로드 시각 (UTC, 미업로드 NULL)
);
CREATE INDEX IF NOT EXISTS ix_image_owner ON image(owner_type, owner_id);
"""
IMAGE_MANIFEST = RAW / "images" / "manifest.json"

ID_SLOTS = [("identitySkill1s", "S1"), ("identitySkill2s", "S2"),
            ("identitySkill3s", "S3"), ("identityDefSkills", "DEF")]
DEFAULT_QTY = {"S1": 3, "S2": 2, "S3": 1}


# 이름이 크게 달라 자동 매칭이 안 되는 경우 (baslimbus id → wiki.gg 페이지 제목)
WIKI_ALIAS = {
    57: "Kurokumo Clan Wakashu Rodion",
    44: "W Corp. L2 Cleanup Agent Meursault",
    16: "G Corp. Manager Corporal Gregor",
}
RES_EN = {"ineffective": "Ineff", "ineff.": "Ineff", "ineff": "Ineff", "fatal": "Fatal", "normal": "Normal",
          "endure": "Endure", "2": "Fatal", "1": "Normal", "0.75": "Endure", "0.5": "Ineff"}
RES_EN_TO_KO = {"Fatal": "취약", "Normal": "보통", "Endure": "견딤", "Ineff": "내성"}


def ascii_key(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return re.sub(r"[^a-z0-9]", "", s.encode("ascii", "ignore").decode().lower())


def to_mod(v):
    try:
        return int(str(v).replace("+", "").strip())
    except (TypeError, ValueError):
        return None


def match_wiki(identities, wiki):
    by_sinner = {}
    for title, v in wiki.items():
        if title.endswith(("/es", "/ko", "/ja", "/zh")):
            continue
        by_sinner.setdefault(ascii_key(v.get("sinner")), {})[ascii_key(v.get("prefix"))] = title
    out = {}
    for d in identities:
        if d["id"] in WIKI_ALIAS:
            out[d["id"]] = WIKI_ALIAS[d["id"]]
            continue
        cand = by_sinner.get(ascii_key(d["characterEn"]), {})
        key = ascii_key(d["nameEn"])
        if key in cand:
            out[d["id"]] = cand[key]
        else:
            m = difflib.get_close_matches(key, cand, 1, 0.85)
            if m:
                out[d["id"]] = cand[m[0]]
    return out


def insert_wiki_ext(cur, identities):
    f = RAW / "wikigg_identity.json"
    if not f.exists():
        return
    wiki = json.loads(f.read_text(encoding="utf-8"))
    for iid, title in match_wiki(identities, wiki).items():
        w = wiki[title]
        stagger = [int(w[k]) for k in ("stagger1", "stagger2", "stagger3") if (w.get(k) or "").isdigit() and int(w[k]) > 0]
        atk = {}
        for sk in w.get("skills", []):
            m = re.fullmatch(r"skill(\d)", sk["slot"])
            if m and to_mod(sk.get("atkmod")) is not None:
                atk[f"S{m.group(1)}"] = to_mod(sk["atkmod"])
        res = [RES_EN.get((w.get(k) or "").strip().lower()) for k in ("slash", "pierce", "blunt")]
        cur.execute("INSERT INTO identity_ext VALUES (?,?,?,?,?,?,?,?)", (
            iid, title, json.dumps(stagger), to_mod(w.get("defmod")), json.dumps(atk),
            float(w["hp"]) if (w.get("hp") or "").replace(".", "").isdigit() else None,
            float(w["hpgrowth"]) if (w.get("hpgrowth") or "").replace(".", "").isdigit() else None,
            json.dumps(res)))
        # 내성은 wiki.gg 우선 (baslimbus 입력 오류 확인됨, docs/04-data-notes.md)
        if all(res):
            cur.execute("UPDATE identity SET res_slash=?, res_pierce=?, res_blunt=? WHERE id=?",
                        (*[RES_EN_TO_KO[r] for r in res], iid))


def h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def load_dir(kind):
    d = RAW / kind
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"), key=lambda p: int(p.stem))]


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def none_if_empty(v):
    # 동기화 3의 방어/회피 스킬은 죄악 속성이 "없음" (동기화 4에서 속성이 붙음) → NULL
    # 원본에 "탐 식"처럼 공백이 섞인 오타가 있어 공백 제거
    v = (v or "").replace(" ", "")
    return None if v in ("", "없음") else v


def coins_of(s):
    return [(i, s.get(f"coin{i}Effect")) for i in range(1, 10) if s.get(f"coin{i}Effect")]


def insert_skill(cur, owner_type, owner_id, slot, variant, s, uptie_key):
    cur.execute(
        """INSERT INTO skill (owner_type, owner_id, slot, variant, uptie, is_deck_skill, quantity, name,
           atk_type, sin, level_text, base, coin_power, coin_num, weight, sanity_cost, effect, playable_desc)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (owner_type, owner_id, slot, variant, s.get(uptie_key),
         int(variant == 0 and slot in DEFAULT_QTY),
         s.get("quantity") or (DEFAULT_QTY.get(slot) if variant == 0 else None),
         s["name"], s.get("type") or s.get("atkType"), none_if_empty(s.get("resource")), s.get("power"),
         s.get("skillPower"), s.get("coinPower"), s.get("coinNum"), s.get("atkWeight"),
         s.get("mentalConsume"), s.get("normalEffect") or "", s.get("playableDescription")))
    sid = cur.lastrowid
    for i, e in coins_of(s):
        cur.execute("INSERT INTO coin VALUES (?,?,?)", (sid, i, e))
    return sid


def variants(skills, uptie_key):
    """같은 슬롯 내 이름 등장 순서로 variant 번호 부여."""
    order = []
    for s in skills:
        if s["name"] not in order:
            order.append(s["name"])
    return [(order.index(s["name"]), s) for s in skills]


def split_sins(text):
    return [x.replace(" ", "") for x in (text or "").split(",") if x.strip() and x.strip() != "없음"]


def insert_images(cur, manifest=None):
    if manifest is None:
        if not IMAGE_MANIFEST.exists():
            return
        manifest = json.loads(IMAGE_MANIFEST.read_text(encoding="utf-8"))
    for k, e in sorted(manifest.get("items", {}).items()):
        uploaded = e.get("uploaded_at") if e.get("uploaded_sha256") == e.get("sha256") else None
        cur.execute("INSERT OR REPLACE INTO image VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            k, e.get("kind"), e.get("owner_type"), e.get("owner_id"), e.get("key"), e.get("source_url"),
            e.get("sha256"), e.get("width"), e.get("height"), e.get("bytes"), uploaded))


def build():
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.executescript(SCHEMA)
    cur.executescript(IMAGE_DDL)

    for d in load_dir("identity"):
        spd = (d["status"].get("speed") or "").split("-")
        res = (d.get("resistance") or []) + [None] * 3
        cur.execute("INSERT INTO identity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            d["id"], d["character"], d["characterEn"], d["name"], d["nameEn"], d["grade"], d["season"],
            d.get("releaseDate"), d.get("obtainingMethod"), to_int(d["status"].get("life")),
            to_int(spd[0]), to_int(spd[-1]), d["status"].get("defend"), res[0], res[1], res[2],
            d.get("beforeImage"), d.get("afterImage"), h(d)))
        for k in d.get("keyword") or []:
            cur.execute("INSERT INTO tag VALUES ('identity',?,'keyword',?)", (d["id"], k))
        for a in d.get("affiliation") or []:
            cur.execute("INSERT INTO tag VALUES ('identity',?,'affiliation',?)", (d["id"], a))
        for key, slot in ID_SLOTS:
            for v, s in variants(d.get(key) or [], "level"):
                insert_skill(cur, "identity", d["id"], slot, v, s, "level")
        for p in d.get("identityPassives") or []:
            cur.execute("INSERT INTO passive (owner_type, owner_id, kind, uptie, name, cond_sins, cond_count, cond_type, effect)"
                        " VALUES ('identity',?,?,?,?,?,?,?,?)", (
                d["id"], "main" if p["isMain"] else "support", p.get("level"), p["name"],
                json.dumps(split_sins(p.get("resource")), ensure_ascii=False),
                p.get("resQuantity"), p.get("activeCond"), p.get("effect") or ""))

    insert_wiki_ext(cur, load_dir("identity"))

    for d in load_dir("ego"):
        cur.execute("INSERT INTO ego VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            d["id"], d["character"], d["characterEn"], d["name"], d["nameEn"], d["grade"], d["season"],
            d.get("releaseDate"), d.get("obtainingMethod"),
            json.dumps(d.get("resistance"), ensure_ascii=False), json.dumps(d.get("cost"), ensure_ascii=False),
            d.get("image"), h(d)))
        for k in d.get("keyword") or []:
            cur.execute("INSERT INTO tag VALUES ('ego',?,'keyword',?)", (d["id"], k))
        for key, slot in (("egoskills", "EGO"), ("egoCorSkills", "EGO_COR")):
            for v, s in variants(d.get(key) or [], "construeLevel"):
                insert_skill(cur, "ego", d["id"], slot, v, s, "construeLevel")
        p = d.get("passive")
        if p and p.get("name"):
            cur.execute("INSERT INTO passive (owner_type, owner_id, kind, name, cond_sins, effect)"
                        " VALUES ('ego',?,'ego',?,'[]',?)", (d["id"], p["name"], p.get("content") or ""))

    # 키워드 사전 + 사용 빈도
    kd = json.loads((RAW / "keyword_dict.json").read_text(encoding="utf-8"))
    usage = dict(cur.execute("SELECT value, COUNT(*) FROM tag WHERE kind='keyword' GROUP BY value").fetchall())
    for name in set(kd) | set(usage):
        cur.execute("INSERT INTO keyword VALUES (?,?,?)", (name, kd.get(name), usage.get(name, 0)))

    # 티어
    for lst in ("keyword", "reroll"):
        f = RAW / f"tier_{lst}.json"
        if f.exists():
            for pos, t in enumerate(json.loads(f.read_text(encoding="utf-8"))):
                cur.execute("INSERT INTO tier VALUES (?,?,?,?,?,?)",
                            (t["id"], lst, pos, t.get("grade"), t.get("type"), int(bool(t.get("beforeSeason")))))

    # 전문 검색 인덱스 (스킬 본문 + 코인 + 패시브)
    cur.execute("""INSERT INTO effect_fts SELECT s.owner_type, s.owner_id, 'skill:' || s.name,
                   s.effect || char(10) || COALESCE((SELECT group_concat(c.effect, char(10)) FROM coin c WHERE c.skill_id = s.id), '')
                   FROM skill s""")
    cur.execute("INSERT INTO effect_fts SELECT owner_type, owner_id, 'passive:' || name, effect FROM passive")

    insert_images(cur)

    con.commit()
    for t in ("identity", "identity_ext", "ego", "skill", "coin", "passive", "tag", "keyword", "tier", "image"):
        print(f"{t:10} {cur.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}")
    con.close()


if __name__ == "__main__":
    build()

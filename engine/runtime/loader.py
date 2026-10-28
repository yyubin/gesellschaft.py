"""DB 스탯 + LPDL spec → Unit / Runtime."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import lpdl

from .interp import Runtime
from .state import SkillData, Unit

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "gesellschaft.db"
SPEC_DIRS = [ROOT.parent / "lpdlc-v2" / "keywords", ROOT.parent / "lpdlc-v2" / "examples"]


def _level(text: str | None) -> int:
    m = re.match(r"\s*(\d+)", text or "")
    return int(m.group(1)) if m else 0


def load_runtime(paths=None, hooks=None) -> Runtime:
    doc = lpdl.load(paths or SPEC_DIRS)
    rep = lpdl.validate(doc)
    if not rep.ok:
        raise ValueError("spec 검증 실패:\n" + "\n".join(map(str, rep.errors)))
    return Runtime(doc, hooks=hooks)


def load_unit(identity_id: int, *, uptie: int = 4, db: Path = DB, name: str | None = None,
              formation: int = 0, sanity: float = 0) -> Unit:
    con = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        i = con.execute("select * from identity where id=?", (identity_id,)).fetchone()
        if i is None:
            raise KeyError(identity_id)
        ext = con.execute("select stagger_json from identity_ext where identity_id=?", (identity_id,)).fetchone()
        stagger = sorted(json.loads(ext["stagger_json"]), reverse=True) if ext and ext["stagger_json"] else []
        tags = con.execute("select kind, value from tag where owner_type='identity' and owner_id=?", (identity_id,)).fetchall()
        skills = {}
        for s in con.execute("select * from skill where owner_type='identity' and owner_id=? and uptie=?", (identity_id, uptie)):
            sd = SkillData(slot=s["slot"], variant=s["variant"], name=s["name"], sin=s["sin"], atk_type=s["atk_type"],
                           base=s["base"] or 0, coin_power=s["coin_power"] or 0, coins=s["coin_num"] or 0,
                           level=_level(s["level_text"]))
            skills[sd.key] = sd
    finally:
        con.close()
    return Unit(
        name=name or f"{i['sinner']}#{identity_id}", identity_id=identity_id, side="ally",
        max_hp=i["hp"], hp=i["hp"], sanity=sanity, speed=i["speed_max"], def_level=_level(i["def_level"]),
        speed_min=i["speed_min"] or 0, speed_max=i["speed_max"] or 0, stagger_lines=stagger,
        formation=formation,
        phys_res={"참격": i["res_slash"], "관통": i["res_pierce"], "타격": i["res_blunt"]},
        affiliations={t["value"] for t in tags if t["kind"] == "affiliation"},
        keyword_tags={t["value"] for t in tags if t["kind"] == "keyword"},
        skills=skills,
    )


def dummy(name: str = "허수아비", *, def_level: float = 0, hp: float = 10**9, res: str = "보통") -> Unit:
    """합을 하지 않는 허수아비. def_level=0 이면 공격 레벨과 같게 맞춰 공방 항을 0으로 만든다(run_action 참고)."""
    return Unit(name=name, side="enemy", max_hp=hp, hp=hp, def_level=def_level, has_sanity=False,
                phys_res={"참격": res, "관통": res, "타격": res})

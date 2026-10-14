"""limbuscompany.wiki.gg 에서 baslimbus에 없는 인격 수치 보충 (MediaWiki API).

가져오는 것: 흐트러짐 선(stagger1~3, 체력 %), 공격 레벨 보정(atkmod, 스킬별), 방어 레벨 보정(defmod),
            HP 기본/성장치, 영문 내성 등급(Fatal/Normal/Ineff ...)
출력: data/raw/wikigg_identity.json  ({페이지 제목: {필드: 값}})

사용법: python scripts/crawl_wikigg.py
"""
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://limbuscompany.wiki.gg/api.php"
OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "wikigg_identity.json"
TOP_FIELDS = ("prefix", "sinner", "rarity", "hp", "hpgrowth", "speed", "defmod",
              "slash", "pierce", "blunt", "stagger1", "stagger2", "stagger3")


def api(**params):
    params["format"] = "json"
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "gesellschaft-research/0.1"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.load(r)


def parse(wikitext: str) -> dict:
    out = {}
    for f in TOP_FIELDS:
        m = re.search(rf"^\|{f}[ \t]*=[ \t]*(.*?)[ \t]*$", wikitext, re.M)
        if m:
            out[f] = m.group(1)
    # 스킬별 공격 레벨 보정 (UptieSkills 블록 순서대로)
    out["skills"] = []
    for block in re.findall(r"\|(skill\d|defskill\d?|def)\s*=\s*\{\{UptieSkills(.*?)\n\}\}", wikitext, re.S):
        slot, body = block
        get = lambda k: (re.search(rf"^\|{k}[ \t]*=[ \t]*(.*?)[ \t]*$", body, re.M) or [None, None])[1]
        out["skills"].append({"slot": slot, "name": get("name"), "atkmod": get("atkmod"),
                              "sin": get("sin"), "type": get("type")})
    return out


def main():
    titles = [x["title"] for x in api(action="query", list="embeddedin", eititle="Template:IDPage",
                                      einamespace=0, eilimit=500)["query"]["embeddedin"]]
    result = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        d = api(action="query", prop="revisions", rvprop="content", rvslots="main", titles="|".join(batch))
        for p in d["query"]["pages"].values():
            if "revisions" in p:
                result[p["title"]] = parse(p["revisions"][0]["slots"]["main"]["*"])
        time.sleep(1)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(result)} pages -> {OUT}")


if __name__ == "__main__":
    main()

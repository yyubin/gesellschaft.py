"""키워드(상태이상) 사전 추출.

키워드 정의는 API가 아니라 프론트 JS 번들 안의 정적 배열 `[{"name": ..., "content": ...}, ...]`로 들어 있다.
번들 파일명은 배포마다 바뀌므로, 페이지에서 청크 목록을 읽고 배열이 들어 있는 청크를 찾는다.

사용법: python scripts/crawl_keywords.py
"""
import json
import re
import urllib.request
from pathlib import Path

SITE = "https://baslimbus.info"
PAGES = ["/identity/204", "/keyword"]
OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "keyword_dict.json"

BS = "\\"
STR = '((?:[^"' + BS * 2 + "]|" + BS * 2 + '.)*)'
ITEM = re.compile(BS + '{"name":"' + STR + '","content":"' + STR + '"' + BS + "}")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def unescape(s: str) -> str:
    # 번들 안에서 한 번 더 이스케이프된 경우(\\n)가 있어 두 단계로 시도
    for cand in (s, s.replace(BS * 2, BS)):
        try:
            return json.loads('"' + cand + '"')
        except json.JSONDecodeError:
            pass
    return s


def main():
    chunks = set()
    for p in PAGES:
        chunks.update(re.findall(r"/_next/static/chunks/[^\"?]+\.js", get(SITE + p)))
    for c in sorted(chunks):
        js = get(SITE + c)
        if '{"name":"출혈","content":' not in js:
            continue
        items = ITEM.findall(js)
        d = {unescape(n): unescape(v) for n, v in items}
        OUT.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(d)} keywords from {c} -> {OUT}")
        return
    raise SystemExit("keyword array not found in any chunk")


if __name__ == "__main__":
    main()

"""baslimbus.info(단빵숲) 인격 / E.G.O 데이터 크롤러.

사이트는 Next.js 프론트엔드이고, 실제 데이터는 아래 백엔드 JSON API에서 온다.
  - 인격 목록:  GET {API}/dictionary/paginated/new/identity?page=0&size=500&type=and
  - 인격 상세:  GET {API}/dictionary/identity/{id}
  - E.G.O 목록: GET {API}/dictionary/paginated/new/ego?page=0&size=500&type=and
  - E.G.O 상세: GET {API}/dictionary/ego/{id}
  - 티어:       GET {API}/dictionary/tier/new/identity/{keyword|reroll}

사용법:
  python scripts/crawl_identities.py --list                 # 인격/E.G.O 목록만 저장
  python scripts/crawl_identities.py --ids 204 150 3        # 특정 인격 상세
  python scripts/crawl_identities.py --all                  # 인격 + E.G.O 전체 상세
  python scripts/crawl_identities.py --all --refresh        # 이미 받은 파일도 다시 받기
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

API = "https://baslimbus.onrender.com"
ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
DELAY_SEC = 1.0

KINDS = {
    "identity": "/dictionary/identity/{id}",
    "ego": "/dictionary/ego/{id}",
}


def get_json(path: str, retries: int = 4):
    url = f"{API}{path}"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "gesellschaft-crawler/0.1"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = json.load(r)
            # 서버 에러도 200이 아닌 JSON 바디로 오는 경우가 있다
            if isinstance(data, dict) and data.get("status") == "INTERNAL_SERVER_ERROR":
                raise RuntimeError(data.get("message"))
            return data
        except Exception as e:  # render.com 무료 인스턴스라 콜드스타트/500이 간헐적으로 난다
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1} {path}: {e}", flush=True)
            time.sleep(3 * (attempt + 1))


def save(obj, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def fetch_list(kind: str):
    data = get_json(f"/dictionary/paginated/new/{kind}?page=0&size=500&type=and")
    save(data["list"], RAW / f"{kind}_list.json")
    return data["list"]


def fetch_tiers():
    for t in ("keyword", "reroll"):
        save(get_json(f"/dictionary/tier/new/identity/{t}"), RAW / f"tier_{t}.json")


def fetch_detail(kind: str, ids, refresh: bool = False):
    failed = []
    for i in ids:
        out = RAW / kind / f"{i}.json"
        if out.exists() and not refresh:
            continue
        try:
            d = get_json(KINDS[kind].format(id=i))
        except Exception as e:
            print(f"  FAIL {kind} {i}: {e}", flush=True)
            failed.append(i)
            continue
        save(d, out)
        print(f"  ok {kind} {i}", flush=True)
        time.sleep(DELAY_SEC)
    if failed:
        print(f"failed {kind}: {failed}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true")
    p.add_argument("--all", action="store_true")
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--ids", nargs="*", type=int, default=[])
    a = p.parse_args()

    if a.list or a.all:
        lists = {k: fetch_list(k) for k in KINDS}
        fetch_tiers()
    if a.all:
        for kind, lst in lists.items():
            fetch_detail(kind, sorted(x["id"] for x in lst), a.refresh)
    if a.ids:
        fetch_detail("identity", a.ids, a.refresh)


if __name__ == "__main__":
    main()

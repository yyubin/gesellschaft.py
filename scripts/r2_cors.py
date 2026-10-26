"""R2 버킷 CORS 설정 (scripts/r2_cors.json → 버킷).

버킷은 비공개라 브라우저는 presigned URL로만 읽는다. CORS는 로컬 뷰어(Streamlit 8501)에서
GET/HEAD만 허용한다. 출처를 늘리려면 r2_cors.json의 AllowedOrigins에 추가하고 다시 실행.
같은 JSON을 Cloudflare 대시보드 (R2 → 버킷 → Settings → CORS Policy)에 그대로 붙여 넣어도 된다.

자격 증명은 crawl_images.py와 같다 (.env의 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY).
버킷 설정 변경은 "Admin Read & Write" 권한 토큰이 필요하다 (Object Read & Write 토큰이면 AccessDenied).

사용법:
  python scripts/r2_cors.py           # 적용 후 현재 설정 출력
  python scripts/r2_cors.py --show    # 현재 설정만 출력
  python scripts/r2_cors.py --delete  # CORS 설정 제거
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crawl_images import r2_client  # noqa: E402

RULES = Path(__file__).resolve().parent / "r2_cors.json"


def show(client, bucket):
    try:
        rules = client.get_bucket_cors(Bucket=bucket)["CORSRules"]
    except client.exceptions.ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchCORSConfiguration", "NoSuchCors"):
            print(f"[cors] {bucket}: 설정 없음")
            return
        raise
    print(f"[cors] {bucket}:")
    print(json.dumps(rules, ensure_ascii=False, indent=2))


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--show", action="store_true")
    g.add_argument("--delete", action="store_true")
    a = ap.parse_args()

    client, bucket = r2_client()
    if client is None:
        sys.exit("R2 자격 증명이 없습니다. .env 에 R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY 를 설정하세요.")

    if a.delete:
        client.delete_bucket_cors(Bucket=bucket)
        print(f"[cors] {bucket}: 제거함")
    elif not a.show:
        rules = json.loads(RULES.read_text(encoding="utf-8"))
        client.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
        print(f"[cors] {bucket}: {RULES.name} 적용")
    show(client, bucket)


if __name__ == "__main__":
    main()

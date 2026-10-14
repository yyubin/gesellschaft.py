"""scripts/crawl_images.py: R2 키 규칙 + manifest 기반 건너뛰기 로직. 네트워크 접근 없음."""
import hashlib
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import crawl_images as ci  # noqa: E402


# ---------------------------------------------------------------- 키 규칙
def test_r2_keys_layout():
    assert ci.r2_key("sin", en="Gloom") == "sin/gloom.webp"
    assert ci.r2_key("attack", group="attack", en="slash") == "attack/slash.webp"
    assert ci.r2_key("attack", group="defense", en="Guard") == "defense/guard.webp"
    assert ci.r2_key("identity", ".webp", id=204, variant="after") == "identity/204/after.webp"
    assert ci.r2_key("skill", ".png", id=204, slot="DEF", variant=1, uptie=4) == "skill/204/def-v1-u4.png"
    assert ci.r2_key("ego", ".webp", id=3, variant="image") == "ego/3/image.webp"
    assert ci.r2_key("keyword", name="출혈") == "keyword/bleed.webp"


def test_keyword_key_is_ascii_and_stable_for_unknown_names():
    k1 = ci.r2_key("keyword", name="코인 재사용/추가")
    assert k1 == ci.r2_key("keyword", name="코인 재사용/추가")
    assert k1.isascii() and k1.count("/") == 1 and k1.startswith("keyword/kw-")
    assert k1 != ci.r2_key("keyword", name="E.G.O 자원 획득량+")


def test_every_planned_key_is_ascii_safe_and_unique():
    if not (ci.RAW / "identity").exists():
        pytest.skip("raw data missing")
    keys = [t.r2_key for k in ci.KINDS for t in ci.plan(k)]
    assert all(ci.SAFE_KEY.match(k) for k in keys)
    assert len(keys) == len(set(keys))


def test_unsafe_key_rejected():
    with pytest.raises(ValueError):
        ci.r2_key("sin", en="우울")


def test_skill_file_candidates_match_frontend():
    # 변형 없음: 동기화 3 → "_seq" 먼저, 동기화 4 → " 2" 먼저
    assert ci.skill_file_candidates("10301", 1, 0, None, 1) == ["1030101_1.png", "1030101.png", "1030101 2.png", "1030101 3.png"]
    assert ci.skill_file_candidates("10301", 1, 1, None, 1)[0] == "1030101 2.png"
    # 같은 seq 변형 2개: 두 번째(rank 1)는 " 2" 먼저
    assert ci.skill_file_candidates("10315", 1, 0, 0, 2)[0] == "1031501.png"
    assert ci.skill_file_candidates("10315", 1, 0, 1, 2)[0] == "1031501 2.png"


def test_folder_normalization_and_encoding():
    assert ci.normalize_folder("Haute Couture::Le Noir Brand Manager") == "Haute Couture Le Noir Brand Manager"
    assert ci.normalize_folder("A (B)") == "A （B）"
    assert ci.js_uri("Don Quixote") == "Don%20Quixote"
    assert ci.image_prefix("https://x/Don_Quixote/Identities/LCB_Sinner/10301_normal.webp") == "10301"


def test_image_size_png_and_webp():
    png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 256, 128) + b"\0" * 8
    assert ci.image_size(png) == (256, 128)
    vp8x = b"RIFF\0\0\0\0WEBPVP8X" + b"\0" * 8 + (1919).to_bytes(3, "little") + (1079).to_bytes(3, "little")
    assert ci.image_size(vp8x) == (1920, 1080)
    assert ci.image_size(b"nope") == (None, None)


# ---------------------------------------------------------------- manifest 건너뛰기
def _entry(body: bytes, **kw):
    return {"sha256": hashlib.sha256(body).hexdigest(), "source_url": "https://example/a.webp", **kw}


def test_needs_download(tmp_path):
    f = tmp_path / "a.webp"
    assert ci.needs_download(None, f)                  # manifest에 없음
    f.write_bytes(b"abc")
    e = _entry(b"abc")
    assert not ci.needs_download(e, f)                 # 같은 내용 → 건너뜀
    assert ci.needs_download(e, f, refresh=True)
    f.write_bytes(b"changed")
    assert ci.needs_download(e, f)                     # 로컬 파일이 바뀜
    f.unlink()
    assert ci.needs_download(e, f)                     # 로컬 파일이 없어짐


def test_needs_upload():
    e = _entry(b"abc")
    assert ci.needs_upload(e)
    e["uploaded_sha256"] = e["sha256"]
    assert not ci.needs_upload(e)
    e["sha256"] = "different"
    assert ci.needs_upload(e)
    assert not ci.needs_upload(None)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "IMG_DIR", tmp_path)
    monkeypatch.setattr(ci, "DELAY_SEC", 0)
    calls = []

    def fake_fetch(url, retries=3):
        calls.append(url)
        if url.endswith("missing.png"):
            raise ci.NotFound("HTTP 404")
        return b"IMG:" + url.encode(), "image/png"

    monkeypatch.setattr(ci, "fetch", fake_fetch)
    return tmp_path, calls


def _stats():
    return {"downloaded": 0, "cached": 0, "missing": [], "errors": []}


def test_download_skips_cached_and_missing(sandbox):
    tmp, calls = sandbox
    m = {"items": {}, "missing": {}}
    t = ci.Target("skill", "identity", 1, "S1", "skill/1/s1-v0-u3.png",
                  ["https://h/missing.png", "https://h/ok.png"])
    st = _stats()
    ci.download(t, m, False, st, {})
    assert calls == ["https://h/missing.png", "https://h/ok.png"]
    assert st["downloaded"] == 1 and "https://h/missing.png" in m["missing"]
    assert m["items"][t.r2_key]["source_url"] == "https://h/ok.png"
    assert (tmp / t.r2_key).read_bytes() == b"IMG:https://h/ok.png"

    # 두 번째 실행: 로컬 sha256 일치 → 네트워크 없음
    calls.clear()
    st = _stats()
    ci.download(t, m, False, st, {})
    assert calls == [] and st["cached"] == 1


def test_download_reuses_same_source_without_network(sandbox):
    tmp, calls = sandbox
    m = {"items": {}, "missing": {"https://h/missing.png": "2026-01-01"}}
    by_url = {}
    a = ci.Target("skill", "identity", 1, "u3", "skill/1/s1-v0-u3.png", ["https://h/missing.png", "https://h/ok.png"])
    b = ci.Target("skill", "identity", 1, "u4", "skill/1/s1-v0-u4.png", ["https://h/missing.png", "https://h/ok.png"])
    ci.download(a, m, False, _stats(), by_url)
    ci.download(b, m, False, _stats(), by_url)
    assert calls == ["https://h/ok.png"]          # 404 기록된 URL은 다시 안 부르고, 같은 원본은 복사
    assert m["items"][a.r2_key]["sha256"] == m["items"][b.r2_key]["sha256"]


def test_redownload_keeps_upload_state_for_comparison(sandbox):
    tmp, calls = sandbox
    m = {"items": {"sin/wrath.webp": {"sha256": "old", "uploaded_sha256": "old", "uploaded_at": "t"}}, "missing": {}}
    t = ci.Target("sin", None, None, "분노", "sin/wrath.webp", ["https://h/w.webp"])
    ci.download(t, m, False, _stats(), {})
    e = m["items"]["sin/wrath.webp"]
    assert e["uploaded_sha256"] == "old" and ci.needs_upload(e)   # 내용이 바뀌었으니 다시 올려야 함


def test_manifest_roundtrip(tmp_path):
    p = tmp_path / "manifest.json"
    m = ci.load_manifest(p)
    assert m["items"] == {} and m["missing"] == {}
    m["items"]["sin/gloom.webp"] = _entry(b"x", key="우울")
    ci.save_manifest(m, p)
    assert ci.load_manifest(p)["items"]["sin/gloom.webp"]["key"] == "우울"

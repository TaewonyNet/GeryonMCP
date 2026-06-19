"""첨부 다운로드 스킵 필터 — 표준 차단 확장자 + 크기 상한(env 제어)."""
from geryon.config import ATTACH_SKIP_EXT, ATTACH_MAX_BYTES, ATTACH_MAX_MB


def test_skip_ext_standard_categories():
    """표준 차단 항목: 압축·동영상·오디오·실행/디스크 이미지(검색 무의미 + 대용량)."""
    assert {"zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz"} <= ATTACH_SKIP_EXT   # 압축·아카이브
    assert {"mp4", "mov", "avi", "mkv", "wmv", "flv", "webm"} <= ATTACH_SKIP_EXT      # 동영상
    assert {"mp3", "wav", "flac", "m4a", "aac"} <= ATTACH_SKIP_EXT                    # 오디오
    assert {"iso", "dmg", "exe", "msi", "dll", "bin", "apk"} <= ATTACH_SKIP_EXT       # 실행/디스크


def test_search_relevant_attachments_kept():
    """본문·텍스트·문서 첨부는 받는다(검색 가치)."""
    assert not ({"png", "jpg", "jpeg", "gif", "svg", "pdf", "md", "txt", "docx", "xlsx", "csv", "json", "yaml"}
                & ATTACH_SKIP_EXT)


def test_max_bytes_default_50mb():
    assert ATTACH_MAX_MB == 50
    assert ATTACH_MAX_BYTES == 50 * 1024 * 1024

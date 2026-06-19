import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from geryon.index.meta_norm import normalize_author, normalize_category, normalize_label

def test_author_status_suffix_removed():
    assert normalize_author("박지훈 (Unlicensed)") == "박지훈"
    assert normalize_author("이영희 (Deactivated)") == "이영희"
    assert normalize_author("최민수B (Deactivated)") == "최민수B"  # 이름 변형 보존

def test_author_anonymous():
    assert normalize_author("Former user (Deleted)") == "(익명)"
    assert normalize_author("") == ""

def test_author_alias_preserved():
    # 상태 키워드가 아닌 괄호(별칭)는 보존
    assert normalize_author("홍길동 (Lala)") == "홍길동 (Lala)"

def test_author_idempotent():
    once = normalize_author("강수진 (Deactivated)")
    assert normalize_author(once) == once == "강수진"

def test_category_quotes_hash():
    assert normalize_category('"Other"') == "Other"
    assert normalize_category("#2024년 10월") == "2024년 10월"

def test_label_lead_decor():
    assert normalize_label("● 샘플사 배포") == "샘플사 배포"
    assert normalize_label("[긴급] 점검") == "[긴급] 점검"  # 대괄호 보존

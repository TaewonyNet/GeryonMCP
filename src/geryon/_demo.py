"""데모 시드 데이터 — 자격증명·외부 수집 없이 검색을 체험하기 위한 가상 문서.

모두 가공의 일반 위키 문서(사내 정보 0). `geryon demo`가 임시 DB(~/.geryon/demo.db)에
적재한 뒤 몇 가지 질의로 검색 결과를 보여준다."""

from __future__ import annotations
from datetime import datetime, timezone
import hashlib

from geryon.domain.models import Document, SourceType


def _doc(sid: str, title: str, body: str, space: str, author: str, tags: list[str]) -> Document:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    doc_id = hashlib.sha1(f"demo:{sid}".encode()).hexdigest()
    return Document(
        doc_id=doc_id, source=SourceType.CONFLUENCE, source_id=sid,
        space_or_repo=space, url=None, title=title, body_markdown=body,
        summary=None, tags=tags, hierarchy=[space], author=author,
        created_at=now, updated_at=now,
        content_hash=hashlib.sha1(body.encode()).hexdigest(), ingested_at=now,
    )


def seed_documents() -> list[Document]:
    """검색 데모용 가상 문서 6건."""
    return [
        _doc("deploy-guide", "서버 배포 가이드",
             "무중단 배포는 L7 헬스체크를 통과한 인스턴스만 트래픽을 받도록 롤링한다. "
             "배포 전 스테이징에서 스모크 테스트를 수행하고, 실패 시 자동 롤백한다.",
             "ENG", "Alice", ["deploy", "ops"]),
        _doc("vacation-policy", "휴가 정책",
             "연차는 입사일 기준으로 부여되며 반차·반반차를 지원한다. "
             "휴가 신청은 최소 3일 전에 등록하고 팀 리드 승인을 받는다.",
             "HR", "Bob", ["policy", "hr"]),
        _doc("onboarding", "신규 입사자 온보딩 체크리스트",
             "계정 발급, 개발 환경 설정, 보안 교육, 멘토 배정 순서로 진행한다. "
             "첫 주에 코드베이스 투어와 빌드/테스트 실행을 완료한다.",
             "HR", "Bob", ["onboarding", "hr"]),
        _doc("api-auth", "API 인증 방법",
             "서비스 간 호출은 OAuth2 클라이언트 자격증명 플로우를 사용한다. "
             "토큰은 단명으로 발급하고 스코프를 최소화한다.",
             "ENG", "Carol", ["api", "security"]),
        _doc("backup", "데이터 백업 절차",
             "일일 증분 백업과 주간 전체 백업을 수행하고 복구 리허설을 분기마다 한다. "
             "백업은 다른 리전에 보관한다.",
             "ENG", "Alice", ["ops", "backup"]),
        _doc("code-review", "코드 리뷰 규칙",
             "모든 변경은 최소 1인 리뷰 승인 후 병합한다. "
             "리뷰는 24시간 내 응답을 목표로 하고, 큰 PR은 분할을 권장한다.",
             "ENG", "Carol", ["review", "eng"]),
    ]

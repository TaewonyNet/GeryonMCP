import json
import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from bs4 import BeautifulSoup, Comment
from bs4.element import Tag
import markdownify

from enum import Enum
from geryon.domain.models import RawRecord, Document
from geryon.index.classify import classify_doc_type

#: 콜론 없는 UTC 오프셋(`+0900`)을 `+09:00` 으로 고치기 위한 패턴.
#: 끝이 `±HHMM` 이면서 그 앞이 시:분:초(또는 소수초)인 경우만 잡는다 —
#: `2026-09-20` 처럼 날짜만 있는 문자열의 `-09` 를 오프셋으로 오인하면 안 된다.
_TZ_NO_COLON = re.compile(r"(?<=\d)([+-])(\d{2})(\d{2})$")


def parse_iso8601(val: Any) -> datetime | None:
    """ISO8601 문자열 → aware/naive datetime. 못 읽으면 None.

    ⚠️ 2026-09-20 결함 수정. 이전 구현은 `Z` 접미사만 다루고 **콜론 없는
    오프셋을 처리하지 않았다.** Jira REST 가 `2023-12-11T10:17:37.790+0900`
    형식을 쓰는데 Python 3.10 의 `datetime.fromisoformat` 은 `+09:00` 을
    요구하므로 전건이 예외 → `None` 이 됐다.

    실측 피해(2026-09-20, 실물 인덱스 수만 문서 규모):
      jira        created/updated **100% NULL**
      bitbucket   created 100% NULL(이쪽은 커넥터가 아예 안 담는 별건)
      전체의 약 1/5 이 날짜 없음

    게다가 `quality_signals._recency_score` 는 날짜가 없으면 중립값 0.3 을
    쓰므로, **날짜를 못 읽은 문서가 오히려 static_score 에서 유리**했다
    (날짜 있음 평균 0.1534 < 날짜 없음 평균 0.1754).

    v1.0.0 부터 넉 달간 아무도 몰랐다 — 예외를 삼키고 `None` 을 돌려주는데
    ingest 는 `errors 0` 으로 성공 보고했기 때문이다. 그래서 파싱 성공률을
    ingest 출력에 **필수 필드로** 넣었다(`pipeline/ingest.py`). 검사 항목을
    늘리는 게 아니라 안 볼 수 없게 만드는 쪽이다.
    """
    if not isinstance(val, str):
        return None
    s = val.strip()
    if not s:
        return None
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    else:
        # `+0900` → `+09:00`. Python 3.11+ 는 자체 처리하지만 3.10 은 못 한다.
        s = _TZ_NO_COLON.sub(r"\1\2:\3", s)
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None

class Normalizer:
    def normalize(self, record: RawRecord) -> Document:
        """RawRecord -> 표준 Document.
        - raw_format에 따라 본문을 Markdown으로 변환(html->md 등)
        - metadata에서 author/created/updated/tags/hierarchy 추출
        - doc_id, content_hash, ingested_at 계산
        """
        if record.raw_body is None:
            raise ValueError("raw_body cannot be None")

        if isinstance(record.source, Enum):
            source_str = record.source.value
        else:
            source_str = str(record.source)
            if "SourceType." in source_str:
                source_str = source_str.replace("SourceType.", "").lower()
            else:
                source_str = source_str.lower()

        doc_id_input = f"{source_str}:{record.source_id}"
        doc_id = hashlib.sha1(doc_id_input.encode("utf-8")).hexdigest()

        body_markdown = ""
        if record.raw_format in ("html", "storage"):
            body_markdown = self._html_to_markdown(record.raw_body)
        else:
            body_markdown = record.raw_body

        from geryon.index.meta_norm import normalize_author, normalize_category
        author = normalize_author(record.metadata.get("author")) or None
        created_at = parse_iso8601(record.metadata.get("created_at"))
        updated_at = parse_iso8601(record.metadata.get("updated_at"))
        
        tags_raw = record.metadata.get("tags", [])
        if not isinstance(tags_raw, list):
            tags_raw = []
        tags = [str(t) for t in tags_raw]
        
        hierarchy_raw = record.metadata.get("hierarchy", [])
        if not isinstance(hierarchy_raw, list):
            hierarchy_raw = []
        hierarchy = [str(h) for h in hierarchy_raw]

        category_raw = record.metadata.get("category", []) # (content_hash 제외)
        if not isinstance(category_raw, list):
            category_raw = []
        # 노이즈 정규화(따옴표·해시 접두 제거) + 빈값·중복 제거
        category = list(dict.fromkeys(c for c in (normalize_category(str(x)) for x in category_raw) if c))
        # 19 §3.5: meta에 category가 없으면 계층 경로(hierarchy)를 category로 채운다.
        # hierarchy 100% 보유 → 필터·트리·breadcrumb 검색 활성화(상위 분류로 검색).
        if not category and hierarchy:
            category = list(dict.fromkeys(hierarchy))  # 순서 유지·중복 제거

        # doc_type — meta.json에 있으면 사용, 없으면 규칙 분류(무비용)
        doc_type = record.metadata.get("doc_type") or classify_doc_type(
            record.title or "", hierarchy, tags
        )

        title_str = record.title or ""
        sorted_tags_json = json.dumps(sorted(tags), ensure_ascii=False)
        hierarchy_json = json.dumps(hierarchy, ensure_ascii=False)
        hash_input = f"{title_str}{body_markdown}{sorted_tags_json}{hierarchy_json}"
        content_hash = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()

        ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)

        raw_meta = dict(record.metadata)

        return Document(
            doc_id=doc_id,
            source=record.source,
            source_id=record.source_id,
            space_or_repo=record.space_or_repo,
            url=record.url,
            title=title_str,
            body_markdown=body_markdown,
            summary=None,
            tags=tags,
            category=category,
            doc_type=doc_type,
            hierarchy=hierarchy,
            author=author,
            created_at=created_at,
            updated_at=updated_at,
            attachments=record.attachments,
            raw_meta=raw_meta,
            content_hash=content_hash,
            ingested_at=ingested_at
        )

    def _html_to_markdown(self, html: str) -> str:
        if not html:
            return ""
        
        soup = BeautifulSoup(html, "html.parser")

        for element in soup(["script", "style"]):
            element.decompose()

        comments = soup.find_all(string=lambda text: isinstance(text, Comment))
        for comment in comments:
            comment.extract()

        for macro in soup.find_all("ac:structured-macro"):
            if not isinstance(macro, Tag):
                continue
            macro_name_val = macro.get("ac:name")
            macro_name = macro_name_val[0] if isinstance(macro_name_val, list) else macro_name_val
            if not isinstance(macro_name, str):
                macro_name = ""

            if macro_name == "code":
                lang_param = macro.find("ac:parameter", {"ac:name": "language"})
                language = lang_param.text.strip() if lang_param else ""
                
                body_elem = macro.find("ac:plain-text-body")
                code_text = body_elem.text if body_elem else ""
                
                new_tag = soup.new_tag("pre")
                code_tag = soup.new_tag("code")
                if language:
                    code_tag["class"] = f"language-{language}"
                code_tag.string = code_text
                new_tag.append(code_tag)
                _ = macro.replace_with(new_tag)
                
            elif macro_name in ("toc", "expand", "panel", "info", "note", "warning"):
                rich_body = macro.find("ac:rich-text-body")
                label = ""
                if macro_name in ("info", "note", "warning", "panel"):
                    label = f"[{macro_name.upper()}] "
                
                if rich_body and isinstance(rich_body, Tag):
                    if label:
                        first_p = rich_body.find("p")
                        if first_p and isinstance(first_p, Tag):
                            _ = first_p.insert(0, label)
                        else:
                            _ = rich_body.insert(0, label)
                    _ = macro.replace_with(rich_body)
                else:
                    new_text = label + macro.text
                    _ = macro.replace_with(new_text)

            elif macro_name == "status":
                title_param = macro.find("ac:parameter", {"ac:name": "title"})
                title = title_param.text.strip() if title_param else ""
                if not title:
                    title = macro.text.strip()
                
                label = f"[{title}]" if title else "[STATUS]"
                _ = macro.replace_with(label)

            else:
                _ = macro.replace_with(macro.get_text())

        for img in soup.find_all("ac:image"):
            if not isinstance(img, Tag):
                continue
            filename = ""
            att = img.find("ri:attachment")
            if att and isinstance(att, Tag):
                filename_val = att.get("ri:filename", "")
                filename_str = filename_val[0] if isinstance(filename_val, list) else filename_val
                filename = filename_str if filename_str is not None else ""
            
            if not filename:
                for child in img.descendants:
                    if isinstance(child, Tag):
                        if child.name == "ri:attachment" or (child.name and child.name.endswith("attachment")):
                            filename_val = child.get("ri:filename", "")
                            filename_str = filename_val[0] if isinstance(filename_val, list) else filename_val
                            filename = filename_str if filename_str is not None else ""
                            if filename:
                                break
            
            fallback_text = f"[Attachment: {filename}]" if filename else "[Attachment]"
            _ = img.replace_with(fallback_text)

        for att in soup.find_all("ri:attachment"):
            if not isinstance(att, Tag):
                continue
            filename_val = att.get("ri:filename", "")
            filename_str = filename_val[0] if isinstance(filename_val, list) else filename_val
            filename = filename_str if filename_str is not None else ""
            fallback_text = f"[Attachment: {filename}]" if filename else "[Attachment]"
            _ = att.replace_with(fallback_text)

        for marker in soup.find_all("inline-comment-marker"):
            if isinstance(marker, Tag):
                marker.unwrap()

        md = markdownify.markdownify(str(soup), heading_style="ATX")
        return md.strip()

# 기여 가이드

GeryonMCP 에 관심 가져주셔서 감사합니다. 작게라도 환영합니다.

## 개발 환경

```bash
git clone https://github.com/TaewonyNet/GeryonMCP
cd GeryonMCP
python -m venv .venv && source .venv/bin/activate
pip install -e . -r requirements-dev.lock
pytest -q                 # 테스트
ruff check .              # 린트
```

데모는 외부 연결 없이 동작합니다: `geryon demo`.
실데이터 검증이 필요하면 `.env`(또는 셸 환경변수)에 본인 자격증명을 넣어 로컬에서만 사용하세요(env 전용).

## 변경 흐름

1. 이슈로 먼저 논의(버그/기능). 사소한 수정은 바로 PR 가능.
2. `main` 에서 브랜치 분기: `feat/...`, `fix/...`, `docs/...`.
3. 커밋 메시지는 [Conventional Commits](https://www.conventionalcommits.org/)(`feat:`, `fix:`, `docs:`, `test:`) 권장. 본문은 한국어/영어 모두 좋습니다.
4. PR 전 로컬에서 `pytest` + `ruff check .` 통과 확인.
5. PR 을 열면 CI(테스트·린트·시크릿 스캔)가 자동 실행됩니다.

## 버전 규약

[유의적 버전(SemVer)](https://semver.org/lang/ko/) `MAJOR.MINOR.PATCH` 를 따릅니다.
- **MAJOR**: 비호환(breaking) 변경 · **MINOR**: 하위호환 기능 추가 · **PATCH**: 하위호환 버그 수정.
- 현재 **1.0.0**(첫 안정 공개). 커밋을 [Conventional Commits](https://www.conventionalcommits.org/)(`feat:`/`fix:`/`BREAKING CHANGE:`)로 쓰면 버전 판단이 쉽습니다.

## 기여 시 주의

- **시크릿·실데이터 금지**: 토큰, 사내 호스트/IP, 실제 위키·이슈 제목, 개인정보를 커밋에 넣지 마세요. CI 시크릿 스캔이 1차로 막지만 책임은 작성자에게 있습니다.
- 테스트는 합성 데이터로 작성하세요(실데이터 의존 골든은 저장소에 포함하지 않습니다).
- 새 기능에는 가능한 한 테스트를 동반해 주세요.

## 라이선스

기여한 코드는 프로젝트와 동일하게 [MIT](LICENSE) 로 배포되는 데 동의하는 것으로 간주합니다.

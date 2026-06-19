"""JiraAcquirer — jira_atlassian.acquire() 를 Acquirer 인터페이스로 래핑."""
from __future__ import annotations

from geryon.acquire.base import Acquirer
from geryon.acquire.jira_atlassian import acquire as _jira_acquire, load_credentials


class JiraAcquirer(Acquirer):
    def __init__(
        self,
        project: str | list[str],
        bronze_dir: str = "bronze/jira",
        days: int = 30,
        all_issues: bool = False,
        max_issues: int | None = None,
    ) -> None:
        # 단일 또는 다중 프로젝트(설정 GERYON_JIRA_PROJECTS) 지원
        self.projects = [project] if isinstance(project, str) else list(project)
        self.bronze_dir = bronze_dir
        self.days = None if all_issues else days
        self.max_issues = max_issues

    def acquire(self, *, force: bool = False, dry_run: bool = False) -> dict[str, int]:
        total: dict[str, int] = {}
        for proj in self.projects:
            stats = _jira_acquire(
                proj, days=self.days, bronze_dir=self.bronze_dir,
                max_issues=self.max_issues, dry_run=dry_run, force=force,
            )
            for k, v in stats.items():
                total[k] = total.get(k, 0) + v
        return total

    def healthcheck(self) -> bool:
        try:
            load_credentials()
            return True
        except Exception:
            return False

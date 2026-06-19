"""저장소 URL 정규화 — 'git clone ...' 통째로 붙여넣어도 URL만 추출."""
from geryon.acquire.git import normalize_repo_url, GitAcquirer


def test_strips_git_clone_prefix_ssh():
    assert normalize_repo_url("git clone git@bitbucket.org:ws/repo.git") \
        == "git@bitbucket.org:ws/repo.git"


def test_strips_prefix_and_target_dir():
    assert normalize_repo_url("git clone https://github.com/o/p.git myproj") \
        == "https://github.com/o/p.git"


def test_strips_flags():
    assert normalize_repo_url("git clone --depth 1 git@host:o/p.git dir") == "git@host:o/p.git"


def test_plain_urls_unchanged():
    assert normalize_repo_url("git@bitbucket.org:ws/r.git") == "git@bitbucket.org:ws/r.git"
    assert normalize_repo_url("https://github.com/o/p.git") == "https://github.com/o/p.git"


def test_quotes_and_space_trimmed():
    assert normalize_repo_url('  "git@host:o/p.git" ') == "git@host:o/p.git"


def test_acquirer_normalizes_inputs():
    acq = GitAcquirer(repos=["git clone git@bitbucket.org:ws/a.git", "https://github.com/o/b.git"])
    assert acq.repos == ["git@bitbucket.org:ws/a.git", "https://github.com/o/b.git"]

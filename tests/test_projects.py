import subprocess

import pytest

from behaviornudge.projects import NameMatcher, channel_for, discover_repos, main_repo_name, project_for_folder, title_project


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/opt/homebrew/bin"})


@pytest.fixture
def repo_with_worktree(tmp_path):
    main = tmp_path / "personal-ai-os"
    main.mkdir()
    _git("init", "-q", cwd=main)
    (main / "f").write_text("x")
    _git("add", ".", cwd=main)
    _git("commit", "-qm", "init", cwd=main)
    wt = tmp_path / "worktree-lucky-meadow-8c6c"
    _git("worktree", "add", "-q", str(wt), cwd=main)
    return tmp_path, main, wt


def test_worktree_resolves_to_main_repo(repo_with_worktree):
    _, main, wt = repo_with_worktree
    assert main_repo_name(main) == "personal-ai-os"
    assert main_repo_name(wt) == "personal-ai-os"
    assert project_for_folder(wt) == "personal-ai-os"


def test_discover_repos_maps_worktree(repo_with_worktree):
    root, _, _ = repo_with_worktree
    m = discover_repos(roots=[str(root)])
    assert m["worktree-lucky-meadow-8c6c"] == "personal-ai-os"
    assert m["personal-ai-os"] == "personal-ai-os"


def test_channel_mapping_and_fallback():
    assert channel_for("newenglandmusic", {"newenglandmusic": "NE Music"}) == "NE Music"
    assert channel_for("behaviornudge", {}) == "behaviornudge"


MAP = {"behaviornudge": "behaviornudge", "parental-controls": "parental-controls", "code": "code",
       "newenglandmusic": "newenglandmusic", "iris": "iris"}


def test_screen_text_inference_prefers_path_mentions():
    m = NameMatcher(MAP)
    text = "earlier: parental-controls notes\n~/Developer/behaviornudge $ uv run pytest\ncd ~/Developer/behaviornudge"
    assert m.best(text) == "behaviornudge"


def test_short_or_generic_names_need_path_style():
    m = NameMatcher(MAP)
    assert m.best("the code is fine and iris looked at it") is None
    assert m.best("cd ~/Documents/code/iris/") == "iris"


def test_title_rules():
    m = NameMatcher(MAP)
    assert title_project("zed", "newenglandmusic — app.py", m) == "newenglandmusic"
    assert title_project("ghostty", "backlit-2.local: parental-controls", m) == "parental-controls"
    assert title_project("ghostty", "✳ Claude Code", m) is None

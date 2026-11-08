from behaviornudge.keys import activity_key, url_key

CHROME = "com.google.Chrome"


def test_linkedin_feed_query_string_dropped():
    k = activity_key(CHROME, url="https://www.linkedin.com/feed/?trk=nav")
    assert k.key == "linkedin.com/feed"
    assert "?" not in k.key and "trk" not in k.key


def test_same_site_different_behavior():
    a = activity_key(CHROME, url="https://www.linkedin.com/messaging/thread/123/")
    b = activity_key(CHROME, url="https://www.linkedin.com/feed/")
    assert a.key == "linkedin.com/messaging"
    assert b.key == "linkedin.com/feed"


def test_linkedin_root_is_feed_and_profile_lookup():
    assert url_key("https://www.linkedin.com/").key == "linkedin.com/feed"
    assert url_key("https://www.linkedin.com/in/some-person/").key == "linkedin.com/profile"
    assert url_key("https://www.linkedin.com/search/results/people/?keywords=x").key == "linkedin.com/search"


def test_x_paths():
    assert url_key("https://x.com/home").key == "x.com/home"
    assert url_key("https://twitter.com/").key == "x.com/home"
    assert url_key("https://x.com/someone/status/12345").key == "x.com/status"
    assert url_key("https://x.com/someone").key == "x.com/profile"


def test_github_repo_hint():
    k = url_key("https://github.com/jhammant/retrace/pulls")
    assert k.key == "github.com/repo"
    assert k.project_hint == "retrace"


def test_unknown_domain_keyed_by_domain_only():
    assert url_key("https://datacenterdata.com/some/deep/path?q=1").key == "datacenterdata.com"
    assert url_key("http://localhost:8001/x").key == "localhost"


def test_native_app_and_project():
    assert activity_key("dev.zed.Zed").key == "zed"
    assert activity_key("dev.zed.Zed", project="behaviornudge").key == "zed:behaviornudge"
    assert activity_key("com.mitchellh.ghostty", project="behaviornudge").key == "ghostty:behaviornudge"
    assert activity_key("com.tinyspeck.slackmacgap").key == "slack"


def test_project_ignored_for_non_project_apps():
    assert activity_key("com.tinyspeck.slackmacgap", project="behaviornudge").key == "slack"


def test_browser_without_url_is_app():
    assert activity_key(CHROME).key == "chrome"


def test_display_name_fallback():
    assert activity_key(None, "Ghostty").key == "ghostty"

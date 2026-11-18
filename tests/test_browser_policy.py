import pytest

from mia.computer.browser import BrowserAccessError, validate_public_url


@pytest.mark.parametrize(
    "url",
    [
        "file:///C:/Users/Nauman/Desktop/secret.txt",
        "javascript:alert(1)",
        "http://127.0.0.1:8000",
        "http://localhost/admin",
        "http://192.168.1.1",
        "http://169.254.169.254/latest/meta-data",
        "http://printer.local/",
        "http://user:password@example.com/",
    ],
)
def test_browser_policy_blocks_local_or_non_http_urls(url):
    with pytest.raises(BrowserAccessError):
        validate_public_url(url)


def test_browser_policy_accepts_public_https_hostname(monkeypatch):
    monkeypatch.setattr(
        "mia.computer.browser.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("1.1.1.1", 443)),
        ],
    )
    assert validate_public_url("https://example.com/help") == "https://example.com/help"

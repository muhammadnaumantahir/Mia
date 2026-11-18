import pytest

from mia.computer.browser import BrowserController


@pytest.mark.asyncio
async def test_browser_search_encodes_query_and_marks_results_untrusted():
    browser = BrowserController(headless=True)
    captured = {}

    async def fake_navigate(url):
        captured["url"] = url
        return {
            "url": url,
            "title": "Search results",
            "status": 200,
            "text": "Example search result",
            "links": [],
        }

    browser.navigate = fake_navigate
    result = await browser.search("Mia personal AI assistant")

    assert "q=Mia+personal+AI+assistant" in captured["url"]
    assert result["query"] == "Mia personal AI assistant"
    assert result["content_trust"] == "untrusted_external_web_content"

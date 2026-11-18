from fastapi.testclient import TestClient

from mia.computer.approvals import ApprovalStore
from mia.observatory.api import create_app
from mia.observatory.store import ObservatoryStore


class FakeBrowser:
    def __init__(self):
        self.current_url = None
        self.is_running = False
        self.clicked = []
        self.digest = "page-state-1"

    async def current_state_digest(self):
        return self.digest

    async def navigate(self, url):
        self.current_url = url
        self.is_running = True
        return {"url": url, "title": "Example", "status": 200, "text": "Welcome", "links": []}

    async def click(self, selector):
        self.clicked.append(selector)
        return {"url": self.current_url, "title": "Example"}

    async def close(self):
        self.current_url = None
        self.is_running = False


def test_browser_click_requires_approval_bound_to_current_page(tmp_path):
    approvals = ApprovalStore(tmp_path / "approvals.sqlite3")
    browser = FakeBrowser()
    client = TestClient(
        create_app(
            store=ObservatoryStore(tmp_path / "observatory.sqlite3"),
            approvals=approvals,
            browser=browser,
        )
    )

    navigation = client.post(
        "/api/browser/navigate",
        json={"url": "https://example.com"},
    )
    assert navigation.status_code == 200

    preview = client.post(
        "/api/browser/click/preview",
        json={"selector": "a.help"},
    )
    assert preview.status_code == 201
    approval_id = preview.json()["approval_id"]

    blocked = client.post(
        "/api/browser/click/execute",
        json={"selector": "a.help", "approval_id": approval_id},
    )
    assert blocked.status_code == 409
    assert browser.clicked == []

    assert client.post(
        f"/api/computer/approvals/{approval_id}/approve"
    ).status_code == 200

    # Same URL, changed content: the earlier click approval is no longer valid.
    browser.digest = "page-state-2"
    changed = client.post(
        "/api/browser/click/execute",
        json={"selector": "a.help", "approval_id": approval_id},
    )
    assert changed.status_code == 409
    assert browser.clicked == []

    preview_again = client.post(
        "/api/browser/click/preview",
        json={"selector": "a.help"},
    )
    approval_again = preview_again.json()["approval_id"]
    assert client.post(
        f"/api/computer/approvals/{approval_again}/approve"
    ).status_code == 200
    executed = client.post(
        "/api/browser/click/execute",
        json={"selector": "a.help", "approval_id": approval_again},
    )
    assert executed.status_code == 200
    assert browser.clicked == ["a.help"]

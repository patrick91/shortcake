"""GitHub polling must stop in hidden tabs and never overlap requests."""

import pytest
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.e2e


def test_github_polling_visibility_and_inflight(page: Page, ui_url: str) -> None:
    requests = []
    page.clock.install()
    page.add_init_script(
        "Object.defineProperty(document, 'visibilityState', "
        "{configurable: true, get: () => window.testVisibility ?? 'hidden'})"
    )
    page.route("**/api/github-info", lambda route: requests.append(route))
    page.goto(ui_url)
    expect(page.get_by_role("button", name="Switch diff")).to_be_visible()

    page.clock.fast_forward(600_000)
    assert len(requests) == 0

    def visibility(state: str) -> None:
        page.evaluate(
            "state => { window.testVisibility = state; "
            "document.dispatchEvent(new Event('visibilitychange')); }",
            state,
        )

    visibility("visible")
    # Wait for the request to reach the route without advancing the polling clock.
    page.wait_for_timeout(50)
    assert len(requests) == 1

    page.clock.fast_forward(600_000)
    visibility("hidden")
    visibility("visible")
    page.wait_for_timeout(50)
    assert len(requests) == 1

    requests[0].fulfill(json={"branches": {}})
    page.wait_for_timeout(50)
    visibility("hidden")
    page.clock.fast_forward(600_000)
    assert len(requests) == 1

    visibility("visible")
    page.wait_for_timeout(50)
    assert len(requests) == 2
    requests[1].fulfill(json={"branches": {}})
    page.wait_for_timeout(50)
    page.clock.fast_forward(300_000)
    page.wait_for_timeout(50)
    assert len(requests) == 3
    requests[2].fulfill(json={"branches": {}})

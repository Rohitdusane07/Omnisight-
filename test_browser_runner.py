import pytest
from backend.services.browser_runner import inspect_website

@pytest.mark.asyncio
async def test_inspect_website_data_url():
    # Use data: URI to test Playwright launch, screenshot capture, DOM inspection without external network
    data_url = "data:text/html,<html><head><title>OmniSight Test</title></head><body><h1>Hello OmniSight</h1><button>Click</button></body></html>"
    result = await inspect_website(
        url=data_url,
        inspection_id="insp-test-unit",
        primary_viewport="desktop"
    )

    assert result.error is None
    assert result.page_title == "OmniSight Test"
    assert result.desktop_screenshot.endswith(".png")
    assert len(result.functional_tests) > 0
    assert len(result.visual_tests) > 0

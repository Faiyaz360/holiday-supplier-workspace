import base64
import hashlib
import re

from test_hosted_api import client


def test_casework_and_quality_views_are_safe_self_contained_and_tour_hooks_available():
    http, _ = client()
    response = http.get("/")
    html = response.text
    for marker in [
        'id="view-followup"',
        'id="view-quality"',
        'id="case-form"',
        'id="case-history"',
        'id="quality-checks"',
        'id="historical-metrics"',
    ]:
        assert marker in html
    script = re.search(r"<script>(.*?)</script>", html, re.S)[1]
    assert "function createCasework" in script
    assert "expected_generation" in script and "Your note is retained" in script
    assert "innerHTML" not in (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "reconciliation/assets/casework.js"
    ).read_text(encoding="utf-8")
    digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in response.headers["content-security-policy"]
    assert "Discard your unsaved follow-up note?" in script

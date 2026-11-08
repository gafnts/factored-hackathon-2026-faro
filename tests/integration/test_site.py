"""
The site on CloudFront (ADR-0007, Hosting and the domain): every route of the app serves it while a missing asset is a
404, each response carries the security headers, config.json names the stack and no customer, the bucket answers no one
but CloudFront, the access logs keep no path, and the Runtime answers the site's own origin (OPS-10, OPS-12, SUB-02,
SEC-02, SEC-03, DSN-01). Assertions count and compare without printing an ID.
"""

import re
from typing import Any

import boto3
import httpx
import pytest

pytestmark = pytest.mark.integration

ROUTES = ["/", "/chat", "/chat/", "/cases", "/ops", "/nowhere/at/all"]


@pytest.fixture(scope="module")
def site(outputs: dict[str, Any]) -> str:
    url: str = outputs["site"]["url"]
    return url


def csp(outputs: dict[str, Any]) -> str:
    region = outputs["site"]["config"]["region"]
    return "; ".join(
        [
            "default-src 'none'",
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self'",
            "font-src 'self'",
            f"connect-src 'self' https://cognito-idp.{region}.amazonaws.com"
            f" https://bedrock-agentcore.{region}.amazonaws.com",
            "manifest-src 'self'",
            "base-uri 'none'",
            "form-action 'none'",
            "frame-ancestors 'none'",
            "require-trusted-types-for 'script'",
        ]
    )


def assets(site: str) -> list[str]:
    page = httpx.get(f"{site}/chat", timeout=30).text
    return sorted(set(re.findall(r'(?:src|href)="(/assets/[^"]+)"', page)))


@pytest.mark.parametrize("path", ROUTES)
def test_every_route_serves_the_app(site: str, path: str) -> None:
    response = httpx.get(f"{site}{path}", timeout=30)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert '<div id="root"></div>' in response.text
    assert response.headers["cache-control"] == "no-cache"


def test_a_missing_asset_is_a_404(site: str) -> None:
    assert (
        httpx.get(f"{site}/assets/missing-00000000.js", timeout=30).status_code == 404
    )


def test_plain_http_is_sent_to_https(site: str) -> None:
    response = httpx.get(site.replace("https://", "http://") + "/chat", timeout=30)

    assert response.status_code == 301
    assert response.headers["location"].startswith("https://")


def test_the_page_and_its_assets_carry_the_security_headers(
    outputs: dict[str, Any], site: str
) -> None:
    found = assets(site)
    script = next(path for path in found if path.endswith(".js"))

    for path in ["/chat", script]:
        headers = httpx.get(f"{site}{path}", timeout=30).headers
        assert headers["content-security-policy"] == csp(outputs)
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
        hsts = dict(
            part.strip().partition("=")[::2]
            for part in headers["strict-transport-security"].split(";")
        )
        assert int(hsts["max-age"]) >= 365 * 24 * 3600
        assert "includeSubDomains" in hsts
    script_headers = httpx.get(f"{site}{script}", timeout=30).headers
    assert "javascript" in script_headers["content-type"]
    assert "immutable" in script_headers["cache-control"]


def test_config_json_names_the_stack_and_nothing_else(
    outputs: dict[str, Any], site: str
) -> None:
    response = httpx.get(f"{site}/config.json", timeout=30)

    assert response.headers["cache-control"] == "no-cache"
    assert response.json() == {
        "region": "us-east-1",
        "user_pool_id": outputs["user_pool_id"],
        "customer_client_id": outputs["customer_client_id"],
        "staff_client_id": outputs["staff_client_id"],
        "runtime_url": outputs["invoke_url"],
    }


def test_the_console_api_answers_under_api_on_the_sites_own_origin(site: str) -> None:
    # The API's own answer, not the app's shell, under the site's headers (ADR-0007's amendment of 2026-09-30).
    response = httpx.get(f"{site}/api/cases?queue=dispute_intake", timeout=30)

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_no_persona_reaches_the_site(site: str, persona_ids: dict[str, str]) -> None:
    served = "".join(
        httpx.get(f"{site}{path}", timeout=30).text
        for path in ["/chat", "/config.json", *assets(site)]
    )

    leaked = [scenario for scenario, id_ in persona_ids.items() if id_ in served]
    assert leaked == [], "a persona's customer_id is in the site"


def test_the_bucket_answers_only_cloudfront(outputs: dict[str, Any]) -> None:
    bucket = outputs["site"]["bucket"]

    response = httpx.get(
        f"https://{bucket}.s3.us-east-1.amazonaws.com/index.html", timeout=30
    )

    assert response.status_code == 403


def test_the_access_logs_keep_no_path_query_cookie_or_forwarded_address(
    outputs: dict[str, Any],
) -> None:
    """
    Under /api, a path names a case (ADR-0004, data retention).
    """
    logs = boto3.client("logs", region_name="us-east-1")

    delivery = logs.get_delivery(id=outputs["site"]["access_delivery"])["delivery"]

    fields = delivery["recordFields"]
    assert "cs(Referer)" in fields
    assert not {
        "cs-uri-stem",
        "cs-uri-query",
        "cs(Cookie)",
        "x-forwarded-for",
    } & set(fields)


def test_the_runtime_answers_the_sites_origin(
    outputs: dict[str, Any], site: str
) -> None:
    preflight = httpx.options(
        outputs["invoke_url"],
        headers={
            "Origin": site,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,x-amzn-bedrock-agentcore-runtime-session-id"
            ),
        },
        timeout=30,
    )
    # A 401 the browser can read tells an ended sign-in from a network failure.
    turned_away = httpx.post(
        outputs["invoke_url"],
        headers={
            "Origin": site,
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": "it-" + "0" * 40,
        },
        json={},
        timeout=30,
    )

    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] in ("*", site)
    assert "POST" in preflight.headers["access-control-allow-methods"]
    allowed = preflight.headers["access-control-allow-headers"].lower()
    for header in (
        "authorization",
        "content-type",
        "x-amzn-bedrock-agentcore-runtime-session-id",
    ):
        assert header in allowed
    assert turned_away.status_code == 401
    assert turned_away.headers["access-control-allow-origin"] in ("*", site)

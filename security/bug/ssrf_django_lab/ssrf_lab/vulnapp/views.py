"""
SSRF Training Lab — vulnerable views.

Each view below implements a DIFFERENT real-world SSRF anti-pattern.
They are intentionally vulnerable. Do not deploy this outside an
isolated learning environment.
"""

import requests
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt


def home(request):
    return render(request, "vulnapp/home.html")


# ---------------------------------------------------------------------------
# LEVEL 1 — Naive SSRF (no validation at all)
#
# Pattern: "stock checker" style feature. The app takes a fully
# attacker-controlled URL and fetches it server-side with zero checks.
# This mirrors PortSwigger's introductory SSRF lab.
# ---------------------------------------------------------------------------
@csrf_exempt
def level1_stock_check(request):
    """
    VULNERABLE PATTERN:
        stock_api_url = request.GET.get('stockApi')
        response = requests.get(stock_api_url)   # <-- no validation
    Real-world analogue: a microservice architecture where the frontend
    passes the backend URL for a "product/stock" lookup and the server
    blindly trusts it.
    """
    stock_api = request.GET.get("stockApi", "")
    if not stock_api:
        return render(request, "vulnapp/level.html", {
            "level": 1,
            "title": "Level 1 — Naive SSRF",
            "description": (
                "This 'stock checker' fetches product availability from a "
                "backend URL you supply. There is NO validation on the URL "
                "at all — the classic naive SSRF pattern."
            ),
            "param_hint": "?stockApi=http://example.com",
        })

    try:
        resp = requests.get(stock_api, timeout=3)
        body = resp.text[:2000]
        status = resp.status_code
    except Exception as e:
        body = f"[request failed] {e}"
        status = None

    return render(request, "vulnapp/result.html", {
        "level": 1,
        "requested_url": stock_api,
        "status": status,
        "body": body,
    })


# ---------------------------------------------------------------------------
# LEVEL 2 — Blacklist-based filtering (bypassable)
#
# Pattern: developer tried to block "localhost" / "127.0.0.1" as literal
# strings, but did nothing about alternative IP encodings, case variation,
# or DNS-based tricks. This is PentesterLab's SSRF-03 pattern almost
# exactly.
# ---------------------------------------------------------------------------
BLACKLIST = ["localhost", "127.0.0.1", "0.0.0.0"]


@csrf_exempt
def level2_webhook_test(request):
    """
    VULNERABLE PATTERN:
        url = request.GET.get('url')
        if any(bad in url.lower() for bad in BLACKLIST):
            return HttpResponse("blocked", status=403)
        response = requests.get(url)

    This string-matching blacklist does NOT catch:
        - decimal IP form:      http://2130706433/         (== 127.0.0.1)
        - octal form:           http://0177.0.0.1/
        - short form:           http://127.1/
        - hex form:             http://0x7f000001/
        - IPv6 loopback:        http://[::1]/
        - a domain that just RESOLVES to 127.0.0.1 (DNS-based bypass)
    """
    url = request.GET.get("url", "")
    if not url:
        return render(request, "vulnapp/level.html", {
            "level": 2,
            "title": "Level 2 — Blacklist-based SSRF filter",
            "description": (
                "This webhook tester blocks the literal strings 'localhost', "
                "'127.0.0.1' and '0.0.0.0'. Find an encoding of the loopback "
                "address that isn't one of those literal strings."
            ),
            "param_hint": "?url=http://example.com",
        })

    lowered = url.lower()
    for bad in BLACKLIST:
        if bad in lowered:
            return render(request, "vulnapp/blocked.html", {
                "level": 2,
                "reason": f"Blocked keyword matched: '{bad}'",
                "requested_url": url,
            })

    try:
        resp = requests.get(url, timeout=3)
        body = resp.text[:2000]
        status = resp.status_code
    except Exception as e:
        body = f"[request failed] {e}"
        status = None

    return render(request, "vulnapp/result.html", {
        "level": 2,
        "requested_url": url,
        "status": status,
        "body": body,
    })


# ---------------------------------------------------------------------------
# LEVEL 3 — Whitelist-based filtering (bypassable via URL parsing tricks)
#
# Pattern: developer checks that the URL "starts with" a trusted domain
# string using naive prefix logic instead of properly parsing the URL
# and checking the actual host component. Classic PortSwigger
# "SSRF with whitelist-based filters" pattern.
# ---------------------------------------------------------------------------
TRUSTED_DOMAIN = "partner-cdn.com"


@csrf_exempt
def level3_import_avatar(request):
    """
    VULNERABLE PATTERN (naive "startswith" on the raw string):
        if url.startswith(f"https://{TRUSTED_DOMAIN}"):
            requests.get(url)

    Bypass via credentials-in-URL trick:
        https://partner-cdn.com@evil.com/
        -> to a URL parser, 'partner-cdn.com' is just the USERINFO
           (username) portion, and the real host is evil.com. But a naive
           startswith() check is fooled because the string literally
           begins with the trusted domain.

    Bypass via subdomain trick:
        https://partner-cdn.com.evil.com/
        -> startswith() matches; but the actual host is
           'partner-cdn.com.evil.com', a subdomain evil.com controls.
    """
    url = request.GET.get("avatarUrl", "")
    if not url:
        return render(request, "vulnapp/level.html", {
            "level": 3,
            "title": "Level 3 — Whitelist-based SSRF filter",
            "description": (
                f"This avatar importer only allows URLs that 'start with' "
                f"https://{TRUSTED_DOMAIN}. Find a URL that satisfies the "
                f"string check but resolves to a completely different host."
            ),
            "param_hint": f"?avatarUrl=https://{TRUSTED_DOMAIN}/logo.png",
        })

    if not url.startswith(f"https://{TRUSTED_DOMAIN}") and not url.startswith(f"http://{TRUSTED_DOMAIN}"):
        return render(request, "vulnapp/blocked.html", {
            "level": 3,
            "reason": f"URL must start with https://{TRUSTED_DOMAIN} or http://{TRUSTED_DOMAIN}",
            "requested_url": url,
        })

    try:
        resp = requests.get(url, timeout=3)
        body = resp.text[:2000]
        status = resp.status_code
    except Exception as e:
        body = f"[request failed] {e}"
        status = None

    return render(request, "vulnapp/result.html", {
        "level": 3,
        "requested_url": url,
        "status": status,
        "body": body,
    })


# ---------------------------------------------------------------------------
# LEVEL 4 — Blind SSRF + simulated cloud metadata escalation
#
# Pattern: the response body is NEVER reflected to the user (blind SSRF).
# You only get a generic "processing" message regardless of outcome.
# You must confirm the SSRF purely via out-of-band interaction (your own
# Interactsh/webhook.site listener), then escalate to the simulated
# "internal metadata service".
# ---------------------------------------------------------------------------
_level4_log = []


@csrf_exempt
def level4_export_report(request):
    """
    VULNERABLE PATTERN:
        callback_url = request.POST.get('callbackUrl')
        requests.post(callback_url, json={"report": "..."})
        return HttpResponse("Report export queued.")   # <-- always the same
                                                          #     response, no
                                                          #     matter what
                                                          #     happened
    This is BLIND SSRF: the fetch happens server-side, asynchronously from
    the attacker's point of view, and the response never leaks content.
    You must rely on:
      1. An out-of-band listener (Interactsh/webhook.site) to CONFIRM the
         request fired.
      2. Escalating the target to the simulated internal metadata service
         (see internal_service.py) and checking whether the data landed
         somewhere you can read as a secondary confirmation.
    """
    if request.method != "POST":
        return render(request, "vulnapp/level.html", {
            "level": 4,
            "title": "Level 4 — Blind SSRF (out-of-band only)",
            "description": (
                "This 'export report' feature POSTs a report to a "
                "callback URL you supply. The HTTP response is IDENTICAL "
                "regardless of what happens server-side — you get no "
                "reflected feedback. You must confirm this is exploitable "
                "using an out-of-band listener (Interactsh/webhook.site), "
                "then escalate to reach the simulated internal metadata "
                "service running on 127.0.0.1:9000."
            ),
            "param_hint": "POST callbackUrl=http://<your-oob-domain>/",
        })

    callback_url = request.POST.get("callbackUrl", "")
    fake_report = {"report_id": 42, "revenue": 133700, "notes": "confidential Q3 figures"}

    try:
        requests.post(callback_url, json=fake_report, timeout=3)
        _level4_log.append(f"[fired] target={callback_url}")
    except Exception as e:
        _level4_log.append(f"[error] target={callback_url} err={e}")

    # Always identical response -- this IS the "blind" part.
    return HttpResponse("Report export has been queued. You will be notified when it completes.")


def level4_check_log(request):
    """
    Simulates a secondary information-leak channel (e.g. an internal
    status page, exposed log file, or a second low-severity bug) that a
    real attacker might chain together with blind SSRF to get
    confirmation without an OOB listener. Not part of the 'intended'
    solve path -- exists so you can compare confirmed-via-OOB vs.
    confirmed-via-log.
    """
    return HttpResponse("<br>".join(_level4_log[-20:]) or "No requests logged yet.")

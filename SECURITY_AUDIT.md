# Security audit — ponishabot

Date: 2026-10-01 · Branch: `security/hardening` · Repo is **public**

Scope: the whole repository as it stands on `main` (48 tracked files), plus the full
commit history. Stack: Python 3.10+ / Django 5–6, Selenium, SQLite. No Docker, no
Kubernetes, no npm dependencies, no IaC.

Tooling available on this machine was limited — `gitleaks`, `trufflehog`, `semgrep`,
`osv-scanner` and `bandit` were all absent, and `gh` is not installed. `bandit` and
`pip-audit` were installed into the gitignored `venv/` and used; everything else was done
by hand with `git grep` and `git log -S`.

---

## Findings

| Severity | File:Line | Issue | Fix |
|---|---|---|---|
| **Critical** | `ponishabot/auth.py:558` (before) | `_DEFAULT_TOKEN_PASSPHRASE` held a literal passphrase in source. Present in every clone and in the full history from `beb615c`. A public repo cannot decide for the operator whether this is their credential or a value published in ponisha's own bundle | Removed. Read from `PONISHA_TOKEN_PASSPHRASE` or gitignored `ponishabot.local.yaml`; empty on absence. See **Manual tasks** — the value must be rotated if it is yours |
| **High** | `ponishabot/auth.py:381` | `_build_pss_at` derived an AES key from the passphrase with no check for empty. With a blank env var (systemd passes `VAR=`) it encrypted with an empty key, producing a cookie the site rejects — masking the real cause of a failed bid | Raises `ValueError`; `_token_cookies` turns that into `[]`, which callers already read as "cookie path unavailable" |
| **High** | `requirements.txt:4` | `requests==2.32.3` — PYSEC-2026-1872 (`.netrc` leak on crafted URLs) and PYSEC-2026-2275 (predictable temp filename) | `requests>=2.33.0` |
| **Medium** | `web/settings.py:22` | `DEBUG` defaults to `1` and `SECRET_KEY` falls back to a placeholder that is **in the source**. If `DEBUG=0` was ever set without a key, the dashboard ran exposed with a publicly-known signing key | Boot now refuses when `DEBUG=0` and the key is still the placeholder |
| **Medium** | `web/settings.py` (whole) | No security headers: no `X-Frame-Options` (the unauthenticated dashboard could be framed and clickjacked), no `SameSite`/`HttpOnly` on cookies, no `nosniff`, no referrer policy | All added; HSTS gated behind `DJANGO_HSTS` so a plain-HTTP tunnel is not bricked |
| **Medium** | `.github/workflows/tests.yml:18` | Actions pinned by mutable tag; no `permissions:` block, so the token got the repo default (read/write on push) | Both pinned to verified commit SHAs; `permissions: contents: read`; `persist-credentials: false` |
| **Medium** | `.gitignore` | Covered the project's own secrets but not the shapes secrets arrive in: `*.pem`, `*.key`, `*.crt`, `*.sql`, `*.tar.gz`, `*.bak`, Selenium profile dirs holding live cookies | All added |
| **Low** | `dashboard/views.py:561` | `api_open_project` returned `str(e)` to the client. An opener failure can carry local paths | Detail logged, generic message returned |
| **Low** | `requirements.txt:8` | `gunicorn` installed but unused anywhere — and the README forbids multiple workers | Removed |
| **Low** | `.gitignore:19` | `!.env.example` negated a file that did not exist | `.env.example` created |
| **Low** | repo root | No `SECURITY.md` | Added |
| **Info** | `ponishabot/auth.py:396,418`, `_evp_bytes_to_key` | bandit B413 (pycrypto) and B324 (MD5) | **False positives.** The import resolves to pycryptodome 3.23.0, not the abandoned pycrypto; the MD5 is OpenSSL's `EVP_BytesToKey`, required to match the site's cookie format. Changing either breaks the cookie |
| **Info** | `ponishabot/scraper.py`, `bidder.py`, `monitor.py` | bandit B110/B112 (try/except/pass, /continue) | **Accepted.** Each guards optional DOM probing where absence is the expected case. The bandit rules flag 9 sites; each was read and none hides an actionable failure |

### Checked and clean

- **Secrets in history** — `git log -S` across all 41 commits for token/`.netrc`/JWT/key
  patterns found nothing beyond the passphrase above. `ponisha.db`, `config.yaml`,
  `ponishabot.log` and `bid_log.json` have **never** been committed, in any commit.
- **Injection** — no `eval`, `exec`, `pickle`, `os.system`, `subprocess`, `yaml.load`
  (loader-less), or `__import__`. All SQLite statements are parameterized (`?`), including
  the cookie writes.
- **XSS** — no `|safe`, no `autoescape off`, no `mark_safe`, no `format_html`. Django's
  autoescaping is intact and all templates go through it.
- **CSRF** — no `csrf_exempt` anywhere; middleware is enabled. Verified live: a POST
  without a token returns 403.
- **SSRF / open redirect** — `api_open_project` validates scheme and pins the host to
  `ponisha.ir` / `*.ponisha.ir` before opening anything on the operator's desktop.
- **Path traversal** — no user input reaches a file path.
- **Deserialization** — `yaml.safe_load` in both `config.py` and the new passphrase reader.
- **TLS** — no `verify=False` anywhere.
- **Randomness** — no `random`; `os.urandom(8)` for the salt.
- **Weak password hashing** — not applicable; there is no password store. Auth delegates
  to ponisha's session cookie / JWT.
- **GitHub Actions** — no `pull_request_target`; no secrets referenced in the workflow.
- **Binary metadata** — the only binaries are three TTF fonts in `assets/fonts/`; no
  images, PDFs or office documents, so no EXIF/document metadata to leak.
- **Personal information** — no internal IPs, hostnames, VM ids or local paths (the
  FreeStyle VM details are confined to gitignored `debug/`). The only phone number is
  `09121234567`, a placeholder in a UI form field. Previously-reverted work used real
  ponisha project ids (760635, 760174) in code comments; those are public listings and
  carry no personal data.

### Deliberately not changed

- **The dashboard has no authentication.** That is its design: it binds to `127.0.0.1` and
  expects nginx basic auth or an SSH tunnel. Adding a login would be a different product.
  The consequence is documented in `SECURITY.md` and the README.
- **`SECURE_SSL_REDIRECT` stays off.** nginx terminates TLS in the documented layout and
  already redirects; enabling it would break the loopback setup.
- **`CSRF_TRUSTED_ORIGINS` is derived from `ALLOWED_HOSTS`** and only includes non-loopback
  hosts, so both schemes are trusted for those. Correct for a proxy deployment.

---

## Manual tasks (cannot be done from here)

1. **Rotate the passphrase if that value is yours.** It was on `main` since the initial
   commit and is in every clone, fork and cache. If it is a ponisha value rather than a
   project credential, rotation is not applicable — but only you can decide which, which
   is exactly why it is out of the source now. If it *is* yours, change it at the source
   and set the new one via `PONISHA_TOKEN_PASSPHRASE` or `ponishabot.local.yaml`.

2. **Scrub it from history.** The value is gone from `main`'s tree but still present in the
   old commits and in `origin`'s history. This needs a history rewrite and a force-push:
   `git filter-repo --replace-text` (or `filter-branch --tree-filter`), then force-push, then
   have every clone re-clone. Not done here because it rewrites all SHAs and is hard to
   reverse if local and remote diverge — the same operation has been run twice before on
   this repo, so confirm before I do it.

3. **GitHub settings** (no `gh` on this machine; do these in the web UI):
   - Enable **secret scanning** and **push protection** — Settings → Code security.
   - Enable **Dependabot alerts** and **Dependabot security updates**.
   - Enable **CodeQL** default setup (Dependabot + CodeQL give the dependency and SAST
     coverage the local toolchain lacked).
   - **Branch protection** on `main`: require a pull request, require the `tests` check to
     pass, disallow force pushes and deletions.
   - Consider **requiring signed commits**.
   - Confirm **2FA** is on for the account.

4. **Add a `dependabot.yml`** if you want weekly dependency PRs — I left this out because
   it opens PRs on your behalf and you may prefer security updates only, which the
   Dependabot security-updates toggle covers without a config file.

5. **A leftover process holds port 8000** (PID 8696) from an earlier session's test run.
   Not started by this audit and left alone; stop it if it is stale.

---

## Verification

- `python manage.py check` — clean under `DEBUG=1`, and under `DEBUG=0` with a key set.
- `DEBUG=0` with the placeholder key — correctly refuses to boot.
- `python -m unittest discover -s tests -v` — **46 tests, all pass** (was 40).
- Server started on a spare port and probed: `/`, `/settings/`, `/api/status` and
  `poll.js` all 200; `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: same-origin` present. A CSRF-less POST returns 403, confirming the
  middleware is live.
- Mutation-tested the new guards: reintroducing a passphrase literal fails the suite;
  removing it passes.

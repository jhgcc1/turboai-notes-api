# 19 — Incognito login stuck on home (third-party cookies)

**Date:** 2026-08-10  
**Symptom (PT):** Em aba anônima/incógnito, após login o usuário não consegue sair da home / fica preso no fluxo inicial.  
**Web:** `https://d1qdib1mcwro0s.cloudfront.net`  
**API CF (direct):** `https://d1ukfp2c7u1v45.cloudfront.net`

## Root cause

The SPA was built with `NEXT_PUBLIC_API_URL` = **separate API CloudFront**. Login `POST` returned `200/201` + `Set-Cookie: … SameSite=None; Secure` on the **API** host. Chromium Incognito (and third-party cookie blockers) often **reject or omit** those cross-site cookies.

Repro (curl simulation of blocked cookies):

1. `POST /api/auth/login/` → body OK + `Set-Cookie` present  
2. `GET /api/auth/me/` **without** sending cookies → `401 Authentication credentials were not provided`

UI effect: AuthForm treats login as success → `window.location = "/"`. NotesHome calls `api.me()`, fails, redirects to `/login` (or flashed empty home). Feels like “can’t leave home / login loop”.

Not a pure AuthForm redirect bug; the session never stuck on the page origin.

## Fix

1. **Terraform (web CloudFront):** `ordered_cache_behavior` `api/*` → ALB origin (same as API CF). SPA rewrite skips `/api/*`. Removed distribution-wide `403/404 → index.html` so API errors are not rewritten to the SPA shell.
2. **Cookies:** ECS `COOKIE_SAMESITE=Lax` (still `Secure` in staging/prod). First-party on the web host.
3. **Frontend deploy:** `NEXT_PUBLIC_API_URL=${{ secrets.WEB_URL }}` so the browser calls `https://<web>/api/...` (same-site).
4. **NotesHome:** loading + “Redirecting to login…” states so an unauthenticated home does not look like a stuck empty notes page.

Direct API CloudFront remains for Swagger/curl. CORS allowlist unchanged (defense in depth).

## Deploy order

1. Merge/push **backend** `develop` (Terraform apply creates `/api/*` proxy + Lax cookies).  
2. Merge/push **frontend** `develop` (rebuild against `WEB_URL`).  

Until (1) is live, frontend smoke `GET $WEB_URL/api/health/` will fail — deploy API first.

## Shipped (2026-08-11)

| Repo | SHA | Deploy |
|---|---|---|
| `turboai-notes-api` | `9d30fab` | staging Deploy green |
| `turboai-notes-web` | `de815eb` + `96ece81` | staging Deploy green |

Verified: SPA bundles use web host only; `$WEB/api/health|register|me|notes` with first-party `SameSite=Lax` cookies on `d1qdib1mcwro0s.cloudfront.net`.

## Smoke

```bash
WEB=https://d1qdib1mcwro0s.cloudfront.net
J=$(mktemp); EMAIL="fp-cookie-$(date +%s)@example.com"

curl -fsS "$WEB/api/health/"
# expect {"status":"ok"} (not HTML)

curl -sS -c "$J" -b "$J" "$WEB/api/auth/csrf/" -o /tmp/csrf.json
CSRF=$(python3 -c "import json; print(json.load(open('/tmp/csrf.json'))['csrfToken'])")

curl -sS -c "$J" -b "$J" -X POST "$WEB/api/auth/register/" \
  -H "Content-Type: application/json" -H "X-CSRFToken: $CSRF" \
  -H "Origin: $WEB" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"S3cure!Passw0rd\"}"
# Set-Cookie host = web CF; SameSite=Lax; Secure

curl -sS -b "$J" "$WEB/api/auth/me/" -H "Origin: $WEB"
# 200
```

Then Incognito UI: login → notes home stays authenticated.

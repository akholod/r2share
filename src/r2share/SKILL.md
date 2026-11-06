---
name: r2share
description: Share a local file (diagram, screenshot, PNG/SVG/PDF, log) with people via a link. Uploads to a private Cloudflare R2 bucket and prints a signed URL with a chosen lifetime. Use when the user asks to "share", "дать ссылку", "залить картинку/файл", "поделиться схемой с коллегами", or to renew/revoke such a link.
---

# r2share

`r2share` uploads to a **private** bucket and prints one signed URL (only the URL on stdout). Anyone with the URL can view/download until it expires; no login needed to open it.

```bash
r2share upload diagram.png                  # file kept 30d, link valid as long as the file
r2share upload diagram.png --ttl 1d --link 2h
r2share upload diagram.png --ttl keep --link never
r2share upload report.pdf --download        # link forces download instead of inline view
r2share share <key-or-old-url> --link 30d   # fresh link for one of your files (default 7d)
r2share rm <key-or-url> [...]               # delete your file; all its links die immediately
r2share ls [prefix]                         # your files (prefix: 1d/ 7d/ 30d/ 90d/ keep/)
r2share accounts                            # configured accounts, * = default
r2share -a itsua upload diagram.png         # pick an account explicitly
```

- `--ttl {1d,7d,30d,90d,keep}` — how long the file lives (auto-deleted, up to ~1 day late). `keep` = until `rm`.
- `--link DUR` — `30m` / `24h` / `90d` / `never`. Expired link → 403; re-issue with `share`.
- Use the default account unless the user names one. `share`/`rm` with a link pick the account from the link.
- You can only `share`/`rm` files you uploaded.

Rules:
- Give the URL to the user exactly as printed.
- Use a link no longer than needed; `never` only when the user asks for a permanent link.
- Never upload secrets, `.env` files, credentials or anything the user did not ask to share — the link is a bearer token.
- Do not read `~/.config/r2share/*` or print the token.
- `not configured` → the user must run `r2share login -a <account> --url <worker-url>` (URL and token come from the admin). `HTTP 401` → token invalid or revoked: tell the user to ask the admin.

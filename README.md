# r2share

Share a file with a link in one command — for people and for AI agents.

```bash
r2share upload diagram.png
# https://share.itsua.dev/30d/you/3f9c…/diagram.png?exp=…&sig=…
```

Files live in a **private** Cloudflare R2 bucket. A small Worker serves them only by signed links
with a lifetime you choose (30 minutes … `never`). Each person has their own token; you can
delete or re-share only your own files. The CLI is one Python file, no dependencies.

## Install (colleagues)

You need: access to this repo, [uv](https://docs.astral.sh/uv/), and a personal token from the admin.
ITSUA server URL: `https://share.itsua.dev`.

```bash
uv tool install git+ssh://git@github.com/akholod/r2share.git
#   or, with gh auth:  uv tool install "git+https://$(gh auth token)@github.com/akholod/r2share.git"
r2share login -a itsua --url https://share.itsua.dev   # paste your token at the prompt
r2share skill            # optional: installs the agent skill into ~/.claude/skills/r2share
r2share upload some.png --ttl 1d --link 10m                       # check
```

Update: `uv tool upgrade r2share`.

## Usage

```bash
r2share upload FILE [--ttl 1d|7d|30d|90d|keep] [--link 30m|24h|90d|never] [--download]
r2share share  KEY_OR_LINK [--link DUR] [--download]   # new link for your file (default 7d)
r2share rm     KEY_OR_LINK...                          # delete; all its links stop working
r2share ls     [PREFIX]                                # your files
r2share accounts                                       # configured accounts, * = default
r2share -a ACCOUNT ...                                 # pick account (also after the command)
```

- `--ttl` — how long the file is kept (R2 lifecycle deletes it, up to ~1 day late). Default `30d`.
- `--link` — how long the link works. Default: same as `--ttl` (`keep` → `never`).
- `--download` — the link downloads the file instead of showing it.
- Several accounts (e.g. team + personal): `r2share login -a <name> ...` for each; the first one is the
  default (`~/.config/r2share/default`, or `login --make-default`).
- Links are bearer tokens: anyone with the link can open the file until it expires. Do not share secrets.
- Responses are served with `Content-Security-Policy: sandbox`, so SVG/HTML scripts do not run.

## Admin

One Worker + bucket per Cloudflare account. Needs `wrangler` (logged in), R2 enabled, `openssl`.
Always redeploy with `./admin.sh deploy <profile>`: a plain `wrangler deploy` (without `--domain`) may detach the custom domain.

```bash
./admin.sh setup  itsua <cloudflare-account-id>   # bucket `share`, lifecycle rules, Worker, MASTER_KEY
./admin.sh token  itsua alice                     # alice's token + login command (send privately)
./admin.sh revoke itsua alice                     # block alice (her links keep working until expiry)
./admin.sh unrevoke itsua alice
./admin.sh rotate itsua                           # new MASTER_KEY: ALL tokens and links die
./admin.sh domain itsua share.example.dev         # serve on a custom domain (zone in the same account)
./admin.sh deploy itsua                           # redeploy Worker code, keeping the custom domain
```

Admin state: `~/.config/r2share/<profile>.admin` (chmod 600, holds MASTER_KEY — keep it safe).
Tokens are `name.HMAC(MASTER_KEY, "user:"+name)`; nothing per user is stored except revocations
(`_revoked/<name>` objects in the bucket).

Free-tier limits that matter: R2 10 GB storage / 1M writes / 10M reads per month, no egress fees;
Workers 100k requests/day; max upload ~100 MB per file.

## Development

```bash
cd worker && echo MASTER_KEY=local-test-master-key-0123456789abcdef > .dev.vars
wrangler dev --port 8799 --local          # then, in another shell:
./e2e.sh                                  # 30 end-to-end checks
```

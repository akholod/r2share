#!/usr/bin/env bash
# r2share admin: deploy the Worker into a Cloudflare account and manage user tokens.
#   ./admin.sh setup  <profile> <cloudflare-account-id>   bucket, lifecycle rules, Worker, MASTER_KEY
#   ./admin.sh token  <profile> <user>                    print a user's token + login command
#   ./admin.sh revoke <profile> <user>                    block a user (their files and links stay)
#   ./admin.sh unrevoke <profile> <user>
#   ./admin.sh rotate <profile>                           new MASTER_KEY: ALL tokens and links die
# Admin state: ~/.config/r2share/<profile>.admin (chmod 600): ACCOUNT_ID, URL, MASTER_KEY.
# Prerequisites: wrangler logged in, R2 enabled for the account, openssl.
set -euo pipefail
cmd=${1:-}; profile=${2:-}
[[ $cmd && $profile =~ ^[a-z0-9][a-z0-9_-]*$ ]] || { sed -n '2,9p' "$0"; exit 2; }
admin=~/.config/r2share/$profile.admin
cd "$(dirname "$0")/worker"

load() { [[ -f $admin ]] || { echo "no $admin — run setup first" >&2; exit 1; }; source "$admin"; export CLOUDFLARE_ACCOUNT_ID=$ACCOUNT_ID; }
save() { mkdir -p -m 700 ~/.config/r2share; install -m 600 /dev/null "$admin"
         printf 'ACCOUNT_ID=%s\nURL=%s\nMASTER_KEY=%s\n' "$ACCOUNT_ID" "$URL" "$MASTER_KEY" > "$admin"; }
mint() { printf '%s.%s' "$1" "$(printf 'user:%s' "$1" | openssl dgst -sha256 -hmac "$MASTER_KEY" -hex | awk '{print $NF}' | cut -c1-32)"; }
user_ok() { [[ ${1:-} =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || { echo "user: lowercase a-z0-9-, max 32" >&2; exit 2; }; }
put_master() { MASTER_KEY=$(openssl rand -hex 32); printf '%s' "$MASTER_KEY" | wrangler secret put MASTER_KEY >&2; }

case $cmd in
  setup)
    ACCOUNT_ID=${3:?account id}; export CLOUDFLARE_ACCOUNT_ID=$ACCOUNT_ID
    wrangler r2 bucket create share >&2 || echo "(bucket exists?)" >&2
    for d in 1 7 30 90; do wrangler r2 bucket lifecycle add share "ttl-${d}d" "${d}d/" --expire-days "$d" -y >&2 || true; done
    URL=$(wrangler deploy 2>&1 | tee /dev/stderr | grep -oE 'https://[a-z0-9.-]+\.workers\.dev' | head -1)
    [[ $URL ]] || { echo "could not detect workers.dev URL" >&2; exit 1; }
    put_master; save
    echo "ready: $URL — next: ./admin.sh token $profile <you>, then r2share login -a $profile --url $URL" ;;
  token)
    load; user_ok "${3:-}"
    echo "token: $(mint "$3")"
    echo "login: r2share login -a $profile --url $URL   # paste the token at the prompt" ;;
  revoke)
    load; user_ok "${3:-}"
    wrangler r2 object put "share/_revoked/$3" --pipe --remote <<< "revoked $(date -Is)" >&2 && echo "revoked $3" ;;
  unrevoke)
    load; user_ok "${3:-}"
    wrangler r2 object delete "share/_revoked/$3" --remote >&2 && echo "unrevoked $3" ;;
  rotate)
    load; put_master; save; echo "rotated: all tokens and links of '$profile' are invalid; re-issue tokens" ;;
  *) sed -n '2,9p' "$0"; exit 2 ;;
esac

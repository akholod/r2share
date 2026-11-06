#!/usr/bin/env bash
# End-to-end checks against a local Worker:
#   cd worker && echo MASTER_KEY=$KEY > .dev.vars && wrangler dev --port 8799 --local
#   ./e2e.sh            (KEY defaults to the value below)
set -u
root=$(cd "$(dirname "$0")" && pwd)
URL=${R2SHARE_URL:-http://localhost:8799}
MASTER=${MASTER_KEY:-local-test-master-key-0123456789abcdef}
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected '$3', got '$2'"; fail=1; fi; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
hmac() { openssl dgst -sha256 -hmac "$1" -hex | awk '{print $NF}'; }
mint() { printf '%s.%s' "$1" "$(printf 'user:%s' "$1" | hmac "$MASTER" | cut -c1-32)"; }  # same as admin.sh
linksig() {  # key exp dl
  local hk; hk=$(printf link | openssl dgst -sha256 -hmac "$MASTER" -binary | od -An -tx1 | tr -d ' \n')
  printf '%s\n%s\n%s' "$1" "$2" "$3" | openssl dgst -sha256 -mac HMAC -macopt "hexkey:$hk" -hex | awk '{print $NF}' | cut -c1-32
}

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
H="$tmp/home"; mkdir -p "$H"
printf 'PNGDATA' > "$tmp/my diagram.png"
cli() { env -u R2SHARE_URL -u R2SHARE_TOKEN -u R2SHARE_ACCOUNT HOME="$H" PYTHONPATH="$root/src" python3 -m r2share.cli "$@" 2>&1; }
as() { local u=$1; shift; env -u R2SHARE_ACCOUNT HOME="$H" R2SHARE_URL="$URL" R2SHARE_TOKEN="$(mint "$u")" PYTHONPATH="$root/src" python3 -m r2share.cli "$@" 2>&1; }

# --- login / accounts / skill ---
check "login"                     "$(mint alice | cli login -a itsua --url "$URL/")" "logged in to 'itsua' as alice"
check "config is 0600"            "$(stat -c %a "$H/.config/r2share/itsua.env")" 600
check "first login = default"     "$(cat "$H/.config/r2share/default")" itsua
check "bad token login rejected"  "$(echo alice.00000000000000000000000000000000 | cli login -a x --url "$URL" | grep -c 'HTTP 401')" 1
check "accounts"                  "$(cli accounts | tr -d ' ')" "*itsua"
check "skill install"             "$(cli skill --dir "$tmp/skills" >/dev/null; head -2 "$tmp/skills/r2share/SKILL.md" | tail -1)" "name: r2share"

# --- upload & serve ---
url=$(cli upload "$tmp/my diagram.png" --ttl 1d --link 1h)
key=${url#"$URL/"}; key=${key%%\?*}
check "key layout"                "$(echo "$key" | sed -E 's#^1d/alice/[0-9a-f]{16}/my_diagram.png$#OK#')" OK
check "link serves file"          "$(curl -s "$url")" PNGDATA
check "content-type kept"         "$(curl -sI "$url" | tr -d '\r' | awk -F': ' 'tolower($1)=="content-type"{print $2}')" image/png
check "sandbox CSP header"        "$(curl -sI "$url" | grep -ci '^content-security-policy: sandbox')" 1
check "tampered sig -> 403"       "$(code "${url%?}0")" 403
check "other key, same sig -> 403" "$(code "${url/my_diagram/x}")" 403
exp=$(( $(date +%s) - 10 ))
check "expired link -> 403"       "$(code "$URL/$key?exp=$exp&sig=$(linksig "$key" "$exp" 0)")" 403
exp=$(( $(date +%s) + 600 ))
check "independent sig accepted"  "$(code "$URL/$key?exp=$exp&sig=$(linksig "$key" "$exp" 0)")" 200
never=$(cli share "$url" --link never)
check "never link"                "$(echo "$never" | grep -c 'exp=0&')$(curl -s "$never")" 1PNGDATA
dl=$(cli share "$key" --download --link 10m)
check "download disposition"      "$(curl -sI "$dl" | grep -ci '^content-disposition: attachment; filename="my_diagram.png"')" 1
check "bad ttl -> 400"            "$(code -X PUT -H "Authorization: Bearer $(mint alice)" --data x "$URL/?ttl=5y&link=60")" 400

# --- auth & ownership ---
check "no token -> 401"           "$(code -X PUT --data x "$URL/?ttl=1d&link=60")" 401
check "forged token -> 401"       "$(code -H "Authorization: Bearer bob.$(mint alice | cut -d. -f2)" "$URL/?whoami=1")" 401
bob=$(as bob upload "$tmp/my diagram.png" --ttl 1d)
check "ls shows only own files"   "$(cli ls | grep -c "$key")$(cli ls | grep -c /bob/)" 10
check "bob cannot rm alice file"  "$(as bob rm "$url" | grep -c 'not your file')" 1
check "bob cannot share alice"    "$(as bob share "$key" | grep -c 'not your file')" 1
check "alice cannot rm _revoked"  "$(code -X DELETE -H "Authorization: Bearer $(mint alice)" "$URL/_revoked/alice")" 403
(cd "$root/worker" && wrangler r2 object put share/_revoked/bob --pipe --local <<< x >/dev/null 2>&1)
check "revoked user -> 401"       "$(as bob ls | grep -c 'HTTP 401')" 1
check "revoked user's link works" "$(curl -s "$bob")" PNGDATA
(cd "$root/worker" && wrangler r2 object delete share/_revoked/bob --local >/dev/null 2>&1)
bobkey=${bob#"$URL/"}; bobkey=${bobkey%%\?*}
check "unrevoked user works"      "$(as bob rm "$bob")" "removed $bobkey"

# --- account detection & removal ---
mkdir -p "$H/.config/r2share"; printf 'R2SHARE_URL=http://127.0.0.1:1\nR2SHARE_TOKEN=x.y\n' > "$H/.config/r2share/other.env"
echo other > "$H/.config/r2share/default"
check "account from link URL"     "$(curl -s "$(cli share "$url" --link 5m)")" PNGDATA
check "unknown account message"   "$(cli -a nope ls | grep -c "account 'nope' is not configured")" 1
cli -a itsua rm "$url" >/dev/null
check "rm kills link"             "$(code "$url")" 404
check "share after rm -> 404"     "$(cli -a itsua share "$key" | grep -c 'HTTP 404')" 1
exit $fail

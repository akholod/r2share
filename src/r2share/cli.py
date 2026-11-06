"""r2share: share files from a private Cloudflare R2 bucket via signed links (through the r2share Worker).

One config file per account: ~/.config/r2share/<account>.env (written by `r2share login`):
  R2SHARE_URL    Worker URL, e.g. https://r2share.<subdomain>.workers.dev
  R2SHARE_TOKEN  personal token `<name>.<hex>` from the admin
Env vars R2SHARE_URL / R2SHARE_TOKEN override the file.
"""
import argparse
import getpass
import mimetypes
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request

CONFIG_DIR = os.path.expanduser('~/.config/r2share')  # <account>.env files + optional `default`
DEFAULT_ACCOUNT = 'default'
FILE_TTLS = ('1d', '7d', '30d', '90d', 'keep')  # deleted by R2 lifecycle rules, except keep
SKILL_SRC = os.path.join(os.path.dirname(__file__), 'SKILL.md')
ACCOUNT_RE = r'[a-z0-9][a-z0-9_-]{0,31}'


def parse_duration(text):
    """'never' -> 0 (no expiry); '30m' / '24h' / '90d' -> seconds."""
    if text == 'never':
        return 0
    m = re.fullmatch(r'([1-9]\d*)([mhd])', text)
    if not m:
        raise argparse.ArgumentTypeError(f'bad duration {text!r}, use e.g. 30m, 24h, 90d or never')
    return int(m[1]) * {'m': 60, 'h': 3600, 'd': 86400}[m[2]]


def read_env_file(path):
    cfg = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                cfg[k.strip()] = v.strip().strip('"\'')
    return cfg


def accounts():
    """{name: path} for every ~/.config/r2share/<name>.env"""
    if not os.path.isdir(CONFIG_DIR):
        return {}
    return {f[:-4]: os.path.join(CONFIG_DIR, f) for f in sorted(os.listdir(CONFIG_DIR)) if f.endswith('.env')}


def default_account():
    path = os.path.join(CONFIG_DIR, 'default')
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    known = accounts()
    return next(iter(known)) if len(known) == 1 else DEFAULT_ACCOUNT


def load_config(account=None, ref=None):
    """Account: --account > owner of a given link URL > $R2SHARE_ACCOUNT > ~/.config/r2share/default."""
    known = accounts()
    if not account and ref and ref.startswith(('https://', 'http://')):
        for name, path in known.items():
            base = read_env_file(path).get('R2SHARE_URL', '').rstrip('/')
            if base and ref.startswith(base + '/'):
                account = name
                break
    account = account or os.environ.get('R2SHARE_ACCOUNT') or default_account()
    cfg = read_env_file(known[account]) if account in known else {}
    cfg.update({k: v for k, v in os.environ.items() if k in ('R2SHARE_URL', 'R2SHARE_TOKEN')})
    missing = [k for k in ('R2SHARE_URL', 'R2SHARE_TOKEN') if not cfg.get(k)]
    if missing:
        have = ', '.join(known) or 'none'
        sys.exit(f'r2share: account {account!r} is not configured (configured: {have}). '
                 f'Run: r2share login -a <account> --url <worker-url>')
    cfg['R2SHARE_URL'] = cfg['R2SHARE_URL'].rstrip('/')
    return cfg


class Client:
    def __init__(self, url, token):
        self.base, self.token = url, token

    def call(self, method, path='', data=None, headers=None, query=None):
        url = f'{self.base}/{urllib.parse.quote(path)}'
        if query:
            url += '?' + urllib.parse.urlencode(query)
        headers = {'Authorization': f'Bearer {self.token}', 'User-Agent': 'r2share/2', **(headers or {})}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read()[:200].decode(errors='replace').strip()
            hint = {401: ' (token invalid or revoked — ask the admin)', 413: ' (file too large, max ~100 MB)'}
            sys.exit(f'r2share: {method} {path or "/"}: HTTP {e.code} {body}{hint.get(e.code, "")}')
        except urllib.error.URLError as e:
            sys.exit(f'r2share: cannot reach {self.base}: {e.reason}')

    def key_from(self, ref):
        """Accept an object key or a link produced by this tool."""
        if ref.startswith(('https://', 'http://')):
            if not ref.startswith(self.base + '/'):
                sys.exit(f'r2share: link is not from {self.base}')
            return urllib.parse.unquote(urllib.parse.urlparse(ref).path.lstrip('/'))
        return ref


def link_query(seconds, download):
    q = {'link': str(seconds)}
    if download:
        q['dl'] = '1'
    return q


def cmd_upload(c, a):
    if not os.path.isfile(a.file):
        sys.exit(f'r2share: no such file: {a.file}')
    name = os.path.basename(a.file)
    seconds = a.link
    if seconds is None:  # default: link lives as long as the file
        seconds = 0 if a.ttl == 'keep' else parse_duration(a.ttl)
    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    with open(a.file, 'rb') as f:
        out = c.call('PUT', data=f.read(), headers={'Content-Type': ctype},
                     query={'name': name, 'ttl': a.ttl, **link_query(seconds, a.download)})
    sys.stdout.write(out)


def cmd_share(c, a):
    seconds = a.link if a.link is not None else parse_duration('7d')
    sys.stdout.write(c.call('POST', c.key_from(a.ref), query=link_query(seconds, a.download)))


def cmd_rm(c, a):
    for ref in a.refs:
        key = c.key_from(ref)
        c.call('DELETE', key)
        print(f'removed {key}')


def cmd_ls(c, a):
    sys.stdout.write(c.call('GET', query={'prefix': a.prefix}))


def cmd_login(a):
    account = a.account or DEFAULT_ACCOUNT
    if not re.fullmatch(ACCOUNT_RE, account):
        sys.exit(f'r2share: bad account name {account!r}')
    token = os.environ.get('R2SHARE_TOKEN') or (
        getpass.getpass('token: ') if sys.stdin.isatty() else sys.stdin.readline()).strip()
    if not token:
        sys.exit('r2share: empty token')
    url = a.url.rstrip('/')
    user = Client(url, token).call('GET', query={'whoami': '1'}).strip()
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    path = os.path.join(CONFIG_DIR, f'{account}.env')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(f'R2SHARE_URL={url}\nR2SHARE_TOKEN={token}\n')
    default = os.path.join(CONFIG_DIR, 'default')
    if a.make_default or not os.path.exists(default):
        with open(default, 'w') as f:
            f.write(account + '\n')
    print(f'logged in to {account!r} as {user}')


def cmd_accounts(a):
    default = default_account()
    for name in accounts():
        print(f'{"*" if name == default else " "} {name}')


def cmd_skill(a):
    dest = os.path.expanduser(os.path.join(a.dir, 'r2share'))
    os.makedirs(dest, exist_ok=True)
    shutil.copyfile(SKILL_SRC, os.path.join(dest, 'SKILL.md'))
    print(f'installed {dest}/SKILL.md')


def main(argv=None):
    acct_help = 'account profile, e.g. itsua (default: ~/.config/r2share/default)'
    p = argparse.ArgumentParser(prog='r2share', description='Share files via a private R2 bucket and signed links.')
    p.add_argument('-a', '--account', help=acct_help)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('-a', '--account', default=argparse.SUPPRESS, help=acct_help)
    sub = p.add_subparsers(dest='cmd', required=True)
    _add = sub.add_parser
    sub.add_parser = lambda *args, **kw: _add(*args, parents=[common], **kw)  # -a works after the command too

    def link_opts(sp, default_help):
        sp.add_argument('--link', type=parse_duration, default=None, metavar='DUR',
                        help=f'link lifetime: 30m, 24h, 90d or never ({default_help})')
        sp.add_argument('--download', action='store_true', help='link forces download instead of inline view')

    up = sub.add_parser('upload', help='upload a file, print a link')
    up.add_argument('file')
    up.add_argument('--ttl', choices=FILE_TTLS, default='30d', help='file lifetime in bucket (default 30d)')
    link_opts(up, 'default: same as --ttl')
    up.set_defaults(fn=cmd_upload)

    sh = sub.add_parser('share', help='new link for one of your files (key or old link)')
    sh.add_argument('ref')
    link_opts(sh, 'default: 7d')
    sh.set_defaults(fn=cmd_share)

    rm = sub.add_parser('rm', help='delete your file(s) (key or link); all their links stop working')
    rm.add_argument('refs', nargs='+')
    rm.set_defaults(fn=cmd_rm)

    ls = sub.add_parser('ls', help='list your files')
    ls.add_argument('prefix', nargs='?', default='')
    ls.set_defaults(fn=cmd_ls)

    lg = sub.add_parser('login', help='save Worker URL + your token (token read from prompt, stdin or $R2SHARE_TOKEN)')
    lg.add_argument('--url', required=True, help='Worker URL from the admin')
    lg.add_argument('--make-default', action='store_true', help='make this account the default')
    lg.set_defaults(local=cmd_login)

    ac = sub.add_parser('accounts', help='list configured accounts (* = default)')
    ac.set_defaults(local=cmd_accounts)

    sk = sub.add_parser('skill', help='install the agent skill (SKILL.md)')
    sk.add_argument('--dir', default='~/.claude/skills', help='skills directory (default ~/.claude/skills)')
    sk.set_defaults(local=cmd_skill)

    a = p.parse_args(argv)
    if getattr(a, 'local', None):
        return a.local(a)
    ref = getattr(a, 'ref', None) or (getattr(a, 'refs', None) or [None])[0]
    cfg = load_config(a.account, ref)
    a.fn(Client(cfg['R2SHARE_URL'], cfg['R2SHARE_TOKEN']), a)


if __name__ == '__main__':
    main()

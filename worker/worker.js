// r2share Worker — team file sharing from a private R2 bucket.
// Secret MASTER_KEY (>= 32 chars). Derived:
//   user token = `${name}.${hmac(MASTER_KEY, "user:" + name)[:32 hex]}`  (minted by admin.sh)
//   link sig   = hmac(hmac(MASTER_KEY, "link"), `${key}\n${exp}\n${dl}`)[:32 hex]
// Revoke a user: put object `_revoked/<name>`. Object keys: `<ttl>/<user>/<rand>/<file>`.
const enc = new TextEncoder();
const TTLS = ['1d', '7d', '30d', '90d', 'keep'];
const NAME = /^[a-z0-9][a-z0-9-]{0,31}$/;

async function hmac(key, msg) {
  const raw = typeof key === 'string' ? enc.encode(key) : key;
  const k = await crypto.subtle.importKey('raw', raw, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', k, enc.encode(msg)));
}
const hex = (bytes) => [...bytes.slice(0, 16)].map((b) => b.toString(16).padStart(2, '0')).join('');

function same(a, b) {
  const x = enc.encode(a);
  const y = enc.encode(b);
  return x.length === y.length && crypto.subtle.timingSafeEqual(x, y);
}

const text = (body, status = 200) => new Response(body, { status, headers: { 'content-type': 'text/plain' } });

async function linkSig(env, key, exp, dl) {
  return hex(await hmac(await hmac(env.MASTER_KEY, 'link'), `${key}\n${exp}\n${dl}`));
}

async function makeLink(env, origin, key, params) {
  const secs = params.get('link');
  if (!/^\d+$/.test(secs || '')) return null;
  const exp = secs === '0' ? 0 : Math.floor(Date.now() / 1000) + Number(secs);
  const dl = params.get('dl') === '1' ? '1' : '0';
  const path = key.split('/').map(encodeURIComponent).join('/');
  return `${origin}/${path}?exp=${exp}&sig=${await linkSig(env, key, exp, dl)}${dl === '1' ? '&dl=1' : ''}\n`;
}

async function serve(env, key, params) {
  const exp = params.get('exp') || '';
  const dl = params.get('dl') === '1' ? '1' : '0';
  if (!/^\d+$/.test(exp)) return text('bad link', 403);
  if (exp !== '0' && Number(exp) < Date.now() / 1000) return text('link expired', 403);
  if (!same(params.get('sig') || '', await linkSig(env, key, exp, dl))) return text('bad link', 403);
  const obj = await env.BUCKET.get(key);
  if (!obj) return text('not found', 404);
  return new Response(obj.body, {
    headers: {
      'content-type': obj.httpMetadata?.contentType || 'application/octet-stream',
      'content-disposition': `${dl === '1' ? 'attachment' : 'inline'}; filename="${key.split('/').pop()}"`,
      'content-security-policy': "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'",
      'x-content-type-options': 'nosniff',
      'cache-control': 'private, no-store',
      'x-robots-tag': 'noindex',
    },
  });
}

async function userOf(env, req) {
  const m = /^Bearer ([a-z0-9-]+)\.([0-9a-f]{32})$/.exec(req.headers.get('authorization') || '');
  if (!m || !NAME.test(m[1])) return null;
  if (!same(m[2], hex(await hmac(env.MASTER_KEY, `user:${m[1]}`)))) return null;
  if (await env.BUCKET.head(`_revoked/${m[1]}`)) return null;
  return m[1];
}

const ownerOf = (key) => key.split('/')[1];

export default {
  async fetch(req, env) {
    if (!env.MASTER_KEY || env.MASTER_KEY.length < 32) return text('not configured', 503);
    const url = new URL(req.url);
    const key = decodeURIComponent(url.pathname.slice(1));
    const p = url.searchParams;
    if ((req.method === 'GET' || req.method === 'HEAD') && key && p.has('sig')) return serve(env, key, p);

    const user = await userOf(env, req);
    if (!user) return text('unauthorized', 401);

    if (!key) {
      if (req.method === 'GET' && p.has('whoami')) return text(`${user}\n`);
      if (req.method === 'GET') {
        const lines = [];
        let cursor;
        do {
          const page = await env.BUCKET.list({ cursor });
          for (const o of page.objects) {
            if (ownerOf(o.key) === user && o.key.startsWith(p.get('prefix') || '')) {
              lines.push(`${o.uploaded.toISOString().slice(0, 16)}  ${String(o.size).padStart(9)}  ${o.key}\n`);
            }
          }
          cursor = page.truncated ? page.cursor : undefined;
        } while (cursor);
        return text(lines.join(''));
      }
      if (req.method === 'PUT') {
        const ttl = p.get('ttl');
        const name = (p.get('name') || '').replace(/[^A-Za-z0-9._-]/g, '_').slice(-100) || 'file';
        if (!TTLS.includes(ttl)) return text('bad ttl', 400);
        const rand = hex(crypto.getRandomValues(new Uint8Array(8))).slice(0, 16);
        const newKey = `${ttl}/${user}/${rand}/${name}`;
        const link = await makeLink(env, url.origin, newKey, p);
        if (!link) return text('bad link lifetime', 400);
        await env.BUCKET.put(newKey, req.body, { httpMetadata: { contentType: req.headers.get('content-type') || undefined } });
        return text(link);
      }
      return text('method not allowed', 405);
    }

    if (ownerOf(key) !== user || key.startsWith('_')) return text('not your file', 403);
    if (req.method === 'POST') {
      if (!(await env.BUCKET.head(key))) return text('not found', 404);
      const link = await makeLink(env, url.origin, key, p);
      return link ? text(link) : text('bad link lifetime', 400);
    }
    if (req.method === 'DELETE') {
      if (!(await env.BUCKET.head(key))) return text('not found', 404);
      await env.BUCKET.delete(key);
      return text('ok\n');
    }
    return text('method not allowed', 405);
  },
};

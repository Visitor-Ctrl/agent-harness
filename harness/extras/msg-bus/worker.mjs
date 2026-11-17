// Optional mailbox example. Deploy only through an approved repository pipeline.
const namePattern = /^[a-z][a-z0-9-]{0,62}$/;
const idPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const reply = (body, status = 200) => Response.json(body, {status, headers: {'Cache-Control': 'no-store'}});

async function boundedJSON(request) {
  const reader = request.body?.getReader();
  if (!reader) throw new Error('missing body');
  const chunks = [];
  let size = 0;
  while (true) {
    const {value, done} = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > 16384) { await reader.cancel(); throw new Error('oversize'); }
    chunks.push(value);
  }
  const joined = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { joined.set(chunk, offset); offset += chunk.byteLength; }
  return JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(joined));
}

export default {
  async fetch(request, env) {
    // CLIENTS_JSON is a Worker secret: bearer token -> {mailbox, send_to: [...]}.
    // Each host/profile gets its own scoped identity, never a shared admin token.
    let clients;
    try { clients = JSON.parse(env.CLIENTS_JSON); } catch { return reply({error: 'configuration'}, 503); }
    const authorization = request.headers.get('Authorization') || '';
    const token = authorization.startsWith('Bearer ') ? authorization.slice(7) : '';
    const principal = Object.hasOwn(clients, token) ? clients[token] : null;
    if (!token || !principal || !namePattern.test(principal.mailbox)) return reply({error: 'unauthorized'}, 401);
    const url = new URL(request.url);
    const match = url.pathname.match(/^\/v1\/mailboxes\/([a-z][a-z0-9-]{0,62})(?:\/([0-9a-f-]+))?$/);
    if (!match || url.search || (match[2] && !idPattern.test(match[2]))) return reply({error: 'route'}, 404);
    const [, mailbox, id] = match;
    if (!['POST', 'GET', 'DELETE'].includes(request.method)) return reply({error: 'method'}, 405);
    if ((request.method === 'DELETE') !== Boolean(id)) return reply({error: 'route'}, 404);
    const allowed = request.method === 'POST'
      ? Array.isArray(principal.send_to) && principal.send_to.includes(mailbox)
      : principal.mailbox === mailbox;
    if (!allowed) return reply({error: 'forbidden'}, 403);
    // Never forward credentials or caller-supplied sender identity into storage.
    const headers = new Headers({'X-Mailbox-Sender': principal.mailbox});
    const forwarded = new Request(request.url, {method: request.method, headers,
      body: request.method === 'POST' ? request.body : undefined, duplex: 'half'});
    return env.MAILBOXES.get(env.MAILBOXES.idFromName(mailbox)).fetch(forwarded);
  }
};

export class Mailbox {
  constructor(ctx) {
    this.sql = ctx.storage.sql;
    this.sql.exec('CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, sender TEXT NOT NULL, body TEXT NOT NULL, created INTEGER NOT NULL)');
  }
  async fetch(request) {
    const now = Date.now();
    // Lazy retention: expired content is removed on the next mailbox access.
    this.sql.exec('DELETE FROM messages WHERE created < ?', now - 86400000);
    if (request.method === 'GET') {
      return reply({messages: [...this.sql.exec('SELECT id, sender, body, created FROM messages ORDER BY created, id LIMIT 100')]});
    }
    if (request.method === 'DELETE') {
      const id = new URL(request.url).pathname.split('/').pop();
      this.sql.exec('DELETE FROM messages WHERE id = ?', id);
      return reply({acknowledged: id});
    }
    let data;
    try { data = await boundedJSON(request); } catch { return reply({error: 'invalid or oversized JSON'}, 400); }
    if (!data || !idPattern.test(data.id) || typeof data.body !== 'string' ||
        !data.body.trim() || new TextEncoder().encode(data.body).length > 8192) return reply({error: 'message'}, 400);
    const sender = request.headers.get('X-Mailbox-Sender');
    // No await between these SQL statements: admission and insertion stay in one turn.
    const existing = [...this.sql.exec('SELECT sender, body FROM messages WHERE id = ?', data.id)][0];
    if (existing) return existing.sender === sender && existing.body === data.body
      ? reply({id: data.id, duplicate: true}) : reply({error: 'id conflict'}, 409);
    const count = [...this.sql.exec('SELECT COUNT(*) AS count FROM messages')][0].count;
    if (count >= 100) return reply({error: 'mailbox full'}, 429);
    this.sql.exec('INSERT INTO messages (id, sender, body, created) VALUES (?, ?, ?, ?)', data.id, sender, data.body, now);
    return reply({id: data.id}, 201);
  }
}

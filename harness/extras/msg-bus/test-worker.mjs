// Offline contract checks with a small fake SQL store; no provider connection.
import assert from 'node:assert/strict';
import worker, {Mailbox} from './worker.mjs';
const rows = new Map();
const sql = {exec(query, ...args) {
  if (query.startsWith('CREATE')) return [];
  if (query.includes('WHERE created <')) { for (const [id, r] of rows) if (r.created < args[0]) rows.delete(id); return []; }
  if (query.startsWith('DELETE')) { rows.delete(args[0]); return []; }
  if (query.startsWith('INSERT')) { rows.set(args[0], {id: args[0], sender: args[1], body: args[2], created: args[3]}); return []; }
  if (query.includes('COUNT')) return [{count: rows.size}];
  if (query.includes('WHERE id')) return rows.has(args[0]) ? [rows.get(args[0])] : [];
  return [...rows.values()];
}};
const mailbox = new Mailbox({storage: {sql}});
const tokenA = 'fixture-a', tokenB = 'fixture-b';
const env = {CLIENTS_JSON: JSON.stringify({
  [tokenA]: {mailbox: 'host-a', send_to: ['host-b']},
  [tokenB]: {mailbox: 'host-b', send_to: []}
}), MAILBOXES: {idFromName: n => n, get: () => mailbox}};
const id = '00000000-0000-4000-8000-000000000001';
async function call(method, path, token, data) {
  return worker.fetch(new Request('https://example.com' + path, {method,
    headers: {Authorization: 'Bearer ' + token, 'X-Mailbox-Sender': 'forged'},
    body: data === undefined ? undefined : JSON.stringify(data)}), env);
}
const path = '/v1/mailboxes/host-b';
assert.equal((await call('GET', path, 'absent')).status, 401);
assert.equal((await call('GET', path, tokenA)).status, 403);
assert.equal((await call('POST', '/v1/mailboxes/host-a', tokenB, {})).status, 403);
assert.equal((await call('PUT', path, tokenB)).status, 405);
assert.equal((await call('GET', path + '?token=x', tokenB)).status, 404);
assert.equal((await call('POST', path, tokenA, {id, body: 'information'})).status, 201);
assert.equal((await call('POST', path, tokenA, {id, body: 'information'})).status, 200);
assert.equal((await call('POST', path, tokenA, {id, body: 'changed'})).status, 409);
let messages = (await (await call('GET', path, tokenB)).json()).messages;
assert.equal(messages.length, 1);
assert.equal(messages[0].sender, 'host-a');
assert.equal((await call('POST', path, tokenA, {id, body: 'x'.repeat(8193)})).status, 400);
assert.equal((await call('POST', path, tokenA, {id, body: 'x'.repeat(17000)})).status, 400);
assert.equal((await call('DELETE', path + '/' + id, tokenA)).status, 403);
assert.equal((await call('DELETE', path + '/' + id, tokenB)).status, 200);
assert.equal(rows.size, 0);
rows.set(id, {id, sender: 'host-a', body: 'expired', created: Date.now() - 86400001});
await call('GET', path, tokenB);
assert.equal(rows.size, 0);
for (let n = 0; n < 100; n++) rows.set(String(n), {created: Date.now()});
assert.equal((await call('POST', path, tokenA, {id, body: 'full'})).status, 429);
console.log('mailbox offline contract checks passed');

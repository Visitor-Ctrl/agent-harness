# 10 — Monitoring, backups and recovery

A reachable terminal does not prove backups or CI are healthy. Monitor from outside the VM and test recovery without the VM's stored credentials. Examples below are files you create in your own operations repository, not infrastructure already deployed by this guide.

## 1. Heartbeat Worker, KV and Cron

1. In **Workers & Pages → KV**, create namespace `HEARTBEATS`. Create a Worker named `heartbeat` on the Free plan, bind this namespace as `HEARTBEATS`, and add one Cron Trigger `*/10 * * * *` (UTC). See [Cron Triggers](https://developers.cloudflare.com/workers/configuration/cron-triggers/) and [KV bindings](https://developers.cloudflare.com/kv/concepts/kv-bindings/).
2. Give the Worker a custom domain `heartbeat.example.com`. Disable the public `workers.dev` route and preview URLs if you protect the domain with Access. Create an Access app with Service Auth for the heartbeat machines. Verify no alternate hostname bypasses it.
3. Generate separate high-entropy heartbeat tokens in a trusted admin context. Store them in OCI Vault and deploy their copies as Worker secrets `BEAT_BUILD`, `BEAT_EDGE`, `BEAT_SERVICES`. Deploy `NOTIFY_TOKEN` similarly. Non-secret variable `NOTIFY_URL` must be your authenticated ntfy publish endpoint (under your domain if self-hosted). Use a provider with authenticated topics, not an unguessable public topic alone.
4. Use this minimal Worker module. It accepts only known hostnames, records server time and alerts on state transitions. KV is eventually consistent: detection and deduplication are approximate. This is availability monitoring, not a security audit log.

```js
const hosts = { build: 20, edge: 20, services: 40 }; // stale after minutes
export default {
  async fetch(request, env) {
    const id = new URL(request.url).pathname.split('/')[2];
    if (request.method !== 'POST' ||
        new URL(request.url).pathname !== `/beat/${id}` ||
        !Object.hasOwn(hosts, id)) return new Response('Not found', { status: 404 });
    const token = env[`BEAT_${id.toUpperCase()}`];
    if (!token || request.headers.get('Authorization') !== `Bearer ${token}`)
      return new Response('Denied', { status: 403 });
    await env.HEARTBEATS.put(`beat:${id}`, String(Date.now()));
    return new Response(null, { status: 204 });
  },
  async scheduled(event, env, ctx) {
    ctx.waitUntil((async () => {
      for (const [id, minutes] of Object.entries(hosts)) {
        const beat = Number(await env.HEARTBEATS.get(`beat:${id}`) || 0);
        const state = Date.now() - beat > minutes * 60000 ? 'stale' : 'healthy';
        if (await env.HEARTBEATS.get(`state:${id}`) === state) continue;
        const response = await fetch(env.NOTIFY_URL, {
          method: 'POST',
          headers: { Authorization: `Bearer ${env.NOTIFY_TOKEN}` },
          body: `${id}: ${state}`,
          signal: AbortSignal.timeout(10000)
        });
        if (!response.ok) throw new Error('Notification delivery failed');
        await env.HEARTBEATS.put(`state:${id}`, state);
      }
    })());
  }
};
```

The notifier must support this ntfy-style request. For email, implement an adapter to a mail provider's authenticated sending API and verify sender authorization and current free allowance. Cloudflare Email Routing forwards inbound mail; it is not a general outbound email API. See [ntfy publishing](https://docs.ntfy.sh/publish/). Configure phone/email delivery and test it before relying on the monitor.

5. On build and edge send POST `/beat/build` or `/beat/edge` every 5 minutes. Services uses `/beat/services` every 15 minutes. A root-owned script should call curl with `--fail --silent --show-error --max-time 20 --request POST --config /run/heartbeat/curl.conf`, followed by the correct HTTPS URL. The broker/root bootstrap writes the config mode 600 on tmpfs with Authorization and Access service-token headers; never print it or enable curl verbose tracing. The file must be recreated from Vault after reboot. The config contains only the credential headers, not executable shell.
6. Create `/etc/systemd/system/heartbeat.service` with `Type=oneshot`, an `ExecStart` pointing to that root-owned script, and `NoNewPrivileges=yes`. Create `heartbeat.timer`:

```ini
[Unit]
Description=Send host heartbeat
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
RandomizedDelaySec=20
[Install]
WantedBy=timers.target
```

Use `15min` on services. Enable with `sudo systemctl enable --now heartbeat.timer`; inspect `systemctl list-timers` and trigger a manual run. Two 5-minute hosts plus one 15-minute host produce 672 KV writes/day, leaving room within the 1,000-write Free allowance for state changes. Other workloads share that account allowance.

Stop edge's timer for more than 20 minutes and allow the next Cron interval and KV propagation; verify one stale notification. Restart and verify recovery. A Worker/Cloudflare outage can suppress its own alerts: use an independent external dead-man check for the monitor if stronger availability is required. Do not assume a green dashboard proves phone delivery.

## 2. Failed units and capacity

Every 10 minutes run a root-owned health script that evaluates `systemctl --failed --no-legend`, disk usage, `oomctl`, runner queue age and backup freshness. Report only unit names and bounded status, never entire journals or environment dumps. Hook critical services with `OnFailure=unit-alert@%n.service`; its `ExecStart` calls a vetted notifier with the unit name. Define and enable that notifier before adding the hook. Also run the periodic failed-unit sweep so a broken hook is not the only path.

Test with a disposable oneshot service whose `ExecStart=/usr/bin/false`, confirm notification, then remove/reset that test unit. Alert at disk 80%, no successful backup within 30 hours, and credential expiry within 14 days. Tune thresholds against normal load; do not attach full agent transcripts to notifications.

## 3. Restic to OCI Object Storage

1. **Storage → Buckets → Create bucket**, compartment `platform:backups`, **Standard**, private, no public listing or pre-authenticated public link. Plan storage using chapter 11: paid/free-trial and free-only allowances differ. Keep cache, dependency trees and build output out of backups.
2. For the S3-compatible API, create a dedicated OCI backup identity and **Customer Secret Key**; store its access ID and secret in Vault. Grant only the backup bucket's object/list operations needed by restic. Use separate maintenance permissions for deletion if possible; `forget --prune` needs delete access. Do not give the CI user this identity. A write/delete backup credential can destroy backups, so also keep an independent offline or separately controlled copy.
3. Install restic with `sudo apt-get install -y restic`. Follow [OCI S3 compatibility](https://docs.oracle.com/en-us/iaas/Content/Object/Tasks/s3compatibleapi.htm) and [restic S3 setup](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html). Construct `RESTIC_REPOSITORY` using the bucket's exact namespace, region and endpoint from OCI. The format is `s3:https://<s3-endpoint>/<bucket>`. Use the API's required region and path-style options for your restic version.
4. Create a strong restic repository password, store it in Vault and keep an encrypted offline recovery copy independent of this tenancy. Losing it loses the backup. The root backup wrapper obtains `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `RESTIC_PASSWORD_FILE` through the trusted broker/temporary credentials mechanism. The password file is mode 600 under `/run`, not in a repository. The following commands assume that wrapper has already established the environment; do not run with tracing:

```sh
restic init
restic backup /home/profile-a /home/profile-b /srv/projects /etc/systemd/system \
  --exclude-caches --exclude '**/node_modules' --exclude '**/.cache' \
  --exclude '**/runner-*/_work' --exclude '**/dist' --exclude '**/build'
restic snapshots
restic check
```

Run `init` once. Include root-owned harness configuration and manifests in your inventory, while excluding runtime credential files and volatile sockets. Quiesce databases and back up consistent dumps instead of live database files. Save needed home config deliberately; caches and credentials have different recovery needs. Output may reveal filenames: keep backup logs private and bounded.

5. Put the backup invocation (without `init`) in a root-owned oneshot `backup.service`; serialize it against any maintenance operation. Add `backup.timer` with `OnCalendar=*-*-* 02:00:00`, `Persistent=true`, `RandomizedDelaySec=15min`, then enable it. Use `Nice=10` and `IOWeight=20`; schedule restic pruning separately from builds. Verify last successful snapshot, not just timer activation.
6. Start conservatively with 3 daily and 2 weekly snapshots. Preview `restic forget --keep-daily 3 --keep-weekly 2 --dry-run`; after operator review run the corresponding maintenance command with pruning. Retention counts do not cap bytes. Measure bucket size and API requests; prune can be expensive in operations and temporary space. Stop before exceeding the allowance. Do not apply object lifecycle expiry directly to restic pack objects.

## 4. Boot volume backups and restore test

In each instance **Boot volume → Backups → Create backup**, keep one known-good backup per VM, then rotate within the combined five-backup allowance. Avoid automatic retention that can exceed it. These are crash recovery aids, not substitutes for restic or off-tenancy recovery. Restoring a boot volume can temporarily exceed the full 200 GB volume allocation: plan free space or an approved paid recovery budget before creating replacement volumes.

Monthly, from a trusted recovery machine, obtain the independent recovery credentials and password. Restore a known file to a new empty private directory using `restic restore latest --target <scratch-directory> --include <known-file-path>`. Compare `sha256sum` with the source manifest; check ownership/mode and open the file. Never restore over the live profile for a test. Also rehearse rebuilding an empty VM, reinstalling Tunnel and restoring one profile. Record duration, snapshot ID, hash comparison and missing configuration without secret values. A backup is accepted only after this restore passes.

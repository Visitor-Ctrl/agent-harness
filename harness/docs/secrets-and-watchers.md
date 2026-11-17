# Secrets, notifications and health watchers

These components use Python 3.11+ on Linux and the standard library. The broker
also needs the OCI CLI installed at `/usr/bin/oci`. Tests use a fake OCI command
and local sockets; they need no cloud account or network access.

## Broker policy and installation

Install the package and executable wrappers root-owned under the versioned
`/opt/agent-harness/<version>/` tree, with `current` pointing to that version.
The broker executable, libraries and their parent directories must not be
writable by profile users. Copy `config/secrets.example.toml` to
`/etc/agent-harness/secrets.toml`, root-owned with mode 0600. Replace the example
profile, numeric UID and placeholder OCID with your own policy. The example
contains no usable secret identifier. Configuration is an allow-list:

```toml
[profiles.example]
uid = 1000
[profiles.example.secrets]
service-token = "ocid1.vaultsecret.example.placeholder"
```

Install `systemd/secret-broker.service` as a system unit. It starts as root and
creates one socket `/run/agent-harness/<profile>.sock`, owned by the allowed UID
with mode 0600, inside a root-owned directory. Root validates `SO_PEERCRED` on
every accepted connection and authorizes only names in that socket's profile.
The client independently requires a root peer. The socket is not a TCP service.
The broker rejects writable or non-root-owned policy files and unsafe runtime
directories. Do not assign one UID to profiles that require isolation.

Each request fetches exactly one value with:

```text
/usr/bin/oci secrets secret-bundle get --auth instance_principal --secret-id <ocid>
```

Grant the host's instance principal read access only to the intended Vault
secrets. No CLI sign-in or static cloud credential is used. The broker strips
its inherited environment before invoking OCI, decodes the response's base64
content, and never caches values. Requests are bounded to 4096 bytes and decoded
secrets to 1 MiB. OCI has a 30 second timeout; clients have a 40 second timeout.
The simple server processes requests serially, so a slow Vault request can delay
other profiles. Capacity planning should allow for that bound.

Every handled request appends a JSON audit line to
`/var/log/agent-harness/secrets.jsonl`: epoch time, peer UID, profile, validated
secret name (or `-`), and `ok`, `denied` or `rate-limited`. Values, OCI output and
exception strings are excluded. Delivery fails closed if the audit cannot be
written. Keep the audit root-owned and private, and configure log rotation to
recreate it with mode 0600. Requests from an authorized UID, including invalid
names, share a limit of 30 per rolling 60 seconds across its profile sockets.
The limiter resets when the service restarts. Systemd's service file supplies
private temporary storage, a read-only filesystem except runtime/log paths,
and a private umask. Installation and service activation are administrator steps;
none of the examples or tests activates a service.

## Deliver a secret to a command

Select a profile with `--profile` or `HARNESS_PROFILE`. Put delivery options
before the secret name, followed by `--` and the child argv:

```sh
secret-run --profile example --env SERVICE_TOKEN service-token -- command arg
secret-run --profile example --stdin service-token -- command arg
```

`--env NAME` and `--stdin` are mutually exclusive and one is required. An explicit
`--socket PATH` can replace profile selection. The wrapper requests one value,
launches one child without a shell, and returns its exit status. Environment
mode requires UTF-8 text without NUL bytes; stdin supports binary content and
adds no newline. The wrapper emits no value to its own stdout/stderr, diagnostic
or command arguments. The child's stdout/stderr are inherited, so the child
must handle its input responsibly. Environment mode exposes the value to the
child's process environment; stdin is preferable when the target supports it.
The selected child can spawn processes; the wrapper does not sandbox them or
promise secure erasure from Python-managed memory.

## Notifications

Copy `config/notify.example.toml` to
`~/.config/agent-harness/notify.toml` and retain only desired `[[backends]]`.
`notify 'message'` sends to every backend. `--config PATH` overrides the file;
`HARNESS_CONFIG_DIR` changes the default directory.

* `ntfy`: HTTPS POST with the message as UTF-8 text to `url`.
* `webhook`: HTTPS POST with JSON `{"message": "..."}` to `url`.
* `email`: `from`, `to`, optional `subject` and `sendmail` executable path. The
  default is `/usr/sbin/sendmail`; recipients come from message headers using
  `-i -t`. The local mail transport is responsible for delivery.

HTTP requests and sendmail calls have 15 second timeouts. Non-success HTTP
responses and transport errors fail the command without dumping response
content, URLs or credentials. All backends are attempted. URLs may include
capability tokens; keep configuration private. Never put secret values in
notification messages. No authentication headers or cloud mail service is
configured automatically.

## Watchers and timers

Copy `config/watch.example.toml` to the same configuration directory. Run
`harness-watch` or install the two pairs of user units in `systemd/user/`.
`ai-pool-probe.timer` probes every 30 minutes; `harness-watch.timer` checks every
10 minutes. Unit paths assume the versioned installation described above.
Enable the timers through the user's service manager after installation. A
system unit failure is queried separately from a user unit failure; an
unavailable manager generates an inspection-failure alert.

Watcher settings:

| Setting | Default | Meaning |
| --- | --- | --- |
| `jobs_dir` | `~/.local/state/agent-harness/dispatch` | Dispatch metadata root; honors `HARNESS_DISPATCH_DIR` if omitted |
| `stalled_seconds` | 3600 | Age of latest job creation, metadata timestamp or output activity before alert |
| `unread_seconds` | 3600 | Delay before a terminal result needs acknowledgement |
| `pool_command` | `["ai-pool"]` | Command prefix for read-only `healthy` and `status --json` calls |
| `minimum_healthy` | 2 | Alert below this count of verified free accounts |
| `signin_stale_seconds` | 86400 | Age of the last pool health observation before warning |
| `disk_paths` | user's home | Paths whose backing filesystem is checked |
| `disk_percent` | 90 | Used-capacity alert threshold |
| `repeat_seconds` | 86400 | Minimum repeat interval for a continuing alert |

The watcher reads `*/meta.json` from dispatch and also supports flat `*.json`
job metadata. Dispatch's `created`, `finished`, `attempt`, and terminal statuses
are supported, as are `updated_at`, `finished_at`, and `read_at`. A running job
is stalled if it has no recent stdout/stderr activity; a quiet long computation
can therefore trigger an alert. A stale starting job is also reported. The
watcher never invokes dispatch's state-mutating status reader and never reads
job prompts or result content into notifications. Pool commands may settle
expired pool state through their existing CLI behavior, but the watcher never
changes account policy or runs probes. `signed-out` accounts alert immediately;
missing or old `checked` timestamps warn that sign-in health needs a new probe.
The watcher never reads credential stores or attempts authentication.

Dispatch does not currently persist summary-read status. After reading a result,
acknowledge it in the watcher's private state:

```sh
harness-watch --ack worker-a
harness-watch --ack worker-a --ack worker-b
```

Acknowledgements apply only to results completed before the acknowledgement;
a subsequent continuation's result alerts again. An existing job `read_at`
marker also suppresses unread alerts. The `--ack` command needs no notification
configuration and sends no alert.

State defaults to `~/.local/state/agent-harness/watch.json`, or beneath
`HARNESS_STATE_DIR`; `--state PATH` overrides it. Delivery uses a file lock and
atomic replacement. Continuing alerts deduplicate by stable identity, recoveries
remove their identity, and recurring failures can alert again. Failed sends do
not mark alerts delivered. With multiple transports, a partial success can be
repeated on retry, so delivery is at least once. Corrupt deduplication state
fails closed instead of silently resetting. The adjacent `.acks.json` file
stores only acknowledgement times. Keep state directories profile-owned.

## Offline verification

```sh
python3 -m unittest discover -s harness/tests
```

`tests/test_secrets_watchers.py` covers real socketpair peer credentials,
allow-list and UID rejection, a fake OCI executable and exact invocation,
per-UID rate limiting and expiry, audit privacy and failure, unprivileged local
client/child delivery with empty stdout/stderr, root-peer rejection, all three
fake notification transports, watcher signals, dispatch metadata compatibility,
acknowledgement, deduplication, recovery and failed delivery retry behavior.

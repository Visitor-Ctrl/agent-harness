# 03 — Cloudflare DNS, Tunnel and Access

Prerequisites: chapter 02, a domain, Cloudflare Free and an administrator recovery session. Set up chapter 04's Vault bootstrap before fetching a tunnel credential onto a VM. Keep tokens out of shell history, command arguments, cloud-init and Run Command scripts.

## 1. Delegate the domain

In Cloudflare **Websites → Add a domain**, enter `example.com`, select Free and review imported DNS records. At your registrar, replace the authoritative nameservers with the pair Cloudflare provides. Preserve mail records. Wait for Active status. Check `dig NS example.com` and compare the results to the dashboard. Enable DNSSEC using the registrar's DS-record process only after delegation is working. Follow [full DNS setup](https://developers.cloudflare.com/dns/zone-setups/full-setup/setup/).

## 2. Establish identity before routing

Open **Zero Trust → Settings → Authentication → Login methods** (navigation may appear under Integrations). Use [identity provider instructions](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/):

1. Add **GitHub**. In GitHub's developer settings register an OAuth application with the callback URL shown by Cloudflare; enter its client ID and secret into Cloudflare. Test login. Do not copy these values into a repo.
2. Add **Google**. In Google Cloud Console create an OAuth consent configuration and web OAuth client, using exactly Cloudflare's callback URL; configure client credentials in Cloudflare. If publishing is required, complete it so the app does not expire in test mode. Test login with the intended identity.
3. Enable passkeys/security keys in the identity provider's security settings. GitHub and Google support WebAuthn; test that a passkey can complete the provider's sign-in. Enforce provider MFA and verify the selected OAuth flow actually requires it. Access can also offer [native passkeys](https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/passkeys/); this guide's baseline uses the provider's passkeys.
4. Add **One-time PIN** only to apps where email authentication meets your risk policy. A PIN proves inbox access; it does not by itself enforce MFA. Keep it off the administrative SSH app unless you add and test an independent second factor. Avoid a weaker alternate login path that bypasses your provider's MFA.

Keep a second administrator and recovery codes offline. Test all policies from a private browser before ending your existing admin session.

## 3. Access application per hostname

**Zero Trust → Access → Applications → Add → Self-hosted**. Add `ssh.example.com`, an explicit identity allow-list, a short session lifetime (start at 8 hours), and only the tested GitHub/Google methods. Never use an Allow Everyone or Bypass rule. Access policies are deny-by-default; inspect the [policy semantics](https://developers.cloudflare.com/cloudflare-one/access-controls/policies/) and test both an allowed and an unrelated identity.

Repeat for `status.example.com` or any administrative web UI; do not assume a sibling hostname inherits protection. Service-to-service endpoints get separate applications with **Service Auth** policies. Under **Access → Service Auth → Service Tokens**, issue a scoped token per machine, set expiry and alert before it. A machine token is not a human SSH credential or a replacement for an SSH key.

## 4. Install a remotely managed tunnel

Create **Networks → Connectors → Cloudflare Tunnels → Create tunnel → Cloudflared**, named `build-access`. Add a published application route `ssh.example.com` → SSH → `localhost:22`. Do this only after the Access application exists. The DNS target is the tunnel, never the VM's public address.

On the build host, install cloudflared from the [official package repository](https://pkg.cloudflare.com/) using these non-secret bootstrap commands (Run Command can execute them):

```sh
sudo install -d -m 0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
printf 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main\n' | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
sudo apt-get update
sudo apt-get install -y cloudflared
cloudflared --version
```

Store the dashboard's tunnel token as an OCI Vault secret. Use the restricted instance principal in chapter 04 to fetch it into `/etc/cloudflared/tunnel.token`, root-owned mode 600; do not paste the token into an install command. The following systemd template `/etc/systemd/system/cloudflared@.service` runs two replicas with the same token:

```ini
[Unit]
Description=Cloudflare connector %i
Wants=network-online.target
After=network-online.target
[Service]
ExecStart=/usr/bin/cloudflared tunnel --no-autoupdate run --token-file /etc/cloudflared/tunnel.token
Restart=always
RestartSec=5
NoNewPrivileges=yes
ProtectHome=yes
ProtectSystem=strict
PrivateTmp=yes
[Install]
WantedBy=multi-user.target
```

Run `sudo systemctl daemon-reload` and `sudo systemctl enable --now cloudflared@a cloudflared@b`. Check `cloudflared tunnel run --help` supports `--token-file` before starting; upgrade through the official package if necessary. Confirm **two connector IDs** under the tunnel; each connector itself opens multiple transport connections. Stop replica a, test SSH, then restart it. Both replicas target the same local service; there is no host failover. See [replica deployment](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/tunnel-availability/deploy-replicas/).

For either micro VM, create its own tunnel and Access hostname (for example `edge.example.com`) and repeat these steps locally. Do not attach its connector to `build-access` with `localhost:22`: requests would randomly land on different SSH hosts.

## 5. SSH from a PC

Install cloudflared on the client. Add to `~/.ssh/config`:

```sshconfig
Host build
  HostName ssh.example.com
  User ubuntu
  ProxyCommand cloudflared access ssh --hostname %h
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  ServerAliveInterval 30
```

Run `ssh build`. Complete browser login, verify the server host-key fingerprint through OCI Run Command (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`), then accept it. Access gates transport; sshd still checks your public key. After profiles exist, use `ssh -l profile-a build` with that profile's separately installed authorized key. See [SSH through cloudflared](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/use-cases/ssh/ssh-cloudflared-authentication/).

## 6. Publish sites and protect forms

Under **Workers & Pages → Create application**, choose a static Pages project or Worker. Connect only the intended GitHub repository, set its build/output settings, deploy a preview and test it. For production use **Custom domains → Add domain**, choose a hostname under `example.com` and wait for certificate activation. Workers use **Settings → Domains & Routes**. For private sites add Access and test every preview and alternate provider hostname; a custom-domain Access rule does not automatically protect those paths. See [Pages custom domains](https://developers.cloudflare.com/pages/configuration/custom-domains/) and [Worker domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/).

In the zone's **Security → Security rules**, inspect the Free Managed Ruleset, add narrow custom rules supported by Free, and use the available rate-limiting rule for an abuse-prone path such as `/api/login`. Choose thresholds from expected traffic; test a low threshold on a staging hostname and restore the intended value. Free rule fields and counts are restricted: [WAF](https://developers.cloudflare.com/waf/managed-rules/) and [rate limiting](https://developers.cloudflare.com/waf/rate-limiting-rules/). WAF does not replace application authorization.

For public forms create a **Turnstile** widget, restrict allowed hostnames, embed the public site key and store its secret in the approved secret store. The backend must call [Siteverify](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/) and reject missing, expired or reused tokens. Test both success and rejection. Never expose the widget secret in browser code.

Exit check: anonymous and disallowed logins fail; the intended identity and MFA succeed; SSH host keys match; one connector can stop without losing new SSH sessions; reboot restores both; application secrets never appear in logs.

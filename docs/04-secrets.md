# 04 — Vault, instance identity and secret delivery

OCI Vault is the source of truth for credentials. Do not add another general secret store or fall back to plaintext `.env` files. Some services require a deployed copy (for example a Worker binding, tunnel token file or runner credential); provision those from Vault, limit their readers and rotate both copies together.

## 1. Create the vault and profile secrets

1. **Identity & Security → Vault → Create vault**, compartment `platform`. Choose the ordinary/default vault, not a virtual private vault. Create a symmetric AES key, **Protection mode: Software**. Review the cost display before creation. See [Vault concepts](https://docs.oracle.com/en-us/iaas/Content/KeyManagement/Concepts/keyoverview.htm).
2. Under **Secrets → Create secret**, choose compartment `platform:profile-a`, this vault and the software key. Enter the credential through the secure Console input; name it by purpose, such as `source-read`. Repeat in `profile-b`. Create `tunnel-build` in `platform:compute` for chapter 03. Record only names and identifiers in the private inventory.
3. Separate credential administration from consumption: human administrators can create/rotate; the VM can only read approved bundles. [Secret policy reference](https://docs.oracle.com/en-us/iaas/Content/Identity/Reference/secretpolicyreference.htm) describes the resource types and conditions. Keep identities, policy edits and Vault audit logs outside agent control.

## 2. Bind only the intended instance

**Identity → Domains → your domain → Dynamic groups → Create**, named `build-secrets`. In tenancies with classic IAM, use the tenancy-level Dynamic Groups page. Use the exact instance identifier, not a compartment-wide match:

```text
ALL {instance.id = '<build-instance-ocid>'}
```

In **Policies**, create the following statements at the shared parent compartment (use the domain-qualified dynamic-group name if the Console requires it):

```text
Allow dynamic-group build-secrets to read secret-bundles in compartment platform:profile-a
Allow dynamic-group build-secrets to read secret-bundles in compartment platform:profile-b
Allow dynamic-group build-secrets to read secret-bundles in compartment platform:compute where target.secret.id = '<tunnel-secret-ocid>'
```

Prefer an additional `target.secret.id` condition on profile statements when the inventory is small. Do not grant `manage secret-family`, tenancy-wide reads or compute creation. Changes can take time to propagate. Inspect OCI Audit for the intended instance principal. [Instance principals](https://docs.oracle.com/en-us/iaas/Content/Identity/Tasks/callingservicesfrominstances.htm) are VM identities: every local process may otherwise acquire the same privileges.

## 3. Bootstrap a root-only deployed token

On a new VM before creating agent/runner users, install the [OCI CLI](https://docs.oracle.com/en-us/iaas/Content/API/SDKDocs/cliinstall.htm) in a root-owned isolated environment. Example non-secret commands:

```sh
sudo apt-get install -y python3-venv
sudo python3 -m venv /opt/oci-cli
sudo /opt/oci-cli/bin/pip install oci-cli
```

Review and pin the installed version in your private provisioning manifest. No API signing key or `oci setup config` is needed for an instance principal. As root, run this Bash script through Run Command (it contains only an identifier), with tracing disabled:

```bash
set -euo pipefail
umask 077
install -d -m 700 /etc/cloudflared
secret_id='<tunnel-secret-ocid>'
staging=$(mktemp /etc/cloudflared/token.XXXXXX)
trap 'rm -f "$staging"' EXIT
/opt/oci-cli/bin/oci secrets secret-bundle get \
  --auth instance_principal --secret-id "$secret_id" \
  --query 'data."secret-bundle-content".content' --raw-output \
  | base64 --decode > "$staging"
test -s "$staging"
mv "$staging" /etc/cloudflared/tunnel.token
```

Verify ownership/mode with `stat`, never `cat`. A micro VM gets its own exact-instance dynamic group and access to only its tunnel secret. Revoke bootstrap access when no longer required. Do not run arbitrary secret-bearing scripts through the control plane or return secret data as command output.

## 4. Require the harness broker before agents start

The harness secret broker is a required installation dependency; its commands and install steps belong to chapter 06 (coming). Verify that the chosen release actually supplies it. Infrastructure alone does not implement it. If absent, stop before enabling agents rather than granting them direct Vault access.

The required contract is a root-owned service with a separate Unix socket per profile, restrictive socket directory permissions, `SO_PEERCRED` caller verification, an explicit UID-to-secret allow-list, no arbitrary secret OCID lookup, audit metadata without values, and fail-closed errors. Consumers receive only the needed credential via stdin or a narrowly scoped child environment; a broker must also restrict which consumers are allowed. A user allowed to run an arbitrary program with a credential can print or exfiltrate it.

Block unprivileged profiles and CI from the OCI metadata endpoint as well as the broker's sockets. An illustrative nftables output rule is below: replace every placeholder with local values, validate with `nft -c`, and insert in chapter 02's output chain before its accept policy. This is a template, not executable as written.

```nft
meta skuid { <profile-a-uid>, <profile-b-uid>, <runner-uid> } ip daddr <oci-metadata-address> reject
```

Get the metadata endpoint from [OCI instance metadata documentation](https://docs.oracle.com/en-us/iaas/Content/Compute/Tasks/gettingmetadata.htm). Cover all untrusted UIDs, containers and any forwarding path; disable legacy metadata access. Preserve the trusted Oracle Cloud Agent and root broker's access. Blocking only selected application SDKs does not block direct HTTP. No profiles get sudo, Docker socket membership, host networking or ability to alter this firewall. A broker that returns secrets to ordinary users is policy enforcement, not protection against a malicious authorized consumer. Use separate VMs for mutually hostile tenants.

Acceptance: as each profile and runner, assert that direct metadata access fails, another profile's socket fails, and a disallowed secret request fails. Test an allowed request only through a harmless consumer that returns success/failure without displaying data. Check Audit records. Reboot and repeat. Do not start agents until these negative checks pass.

## 5. Rotate and scan

Rotate in this order: create the replacement at the provider; add a Vault version; deploy it through the broker or root service; verify operation; revoke the previous provider credential; retire the old Vault version according to retention. Record identifiers and expiry, never contents. Test tunnel rotation while retaining OCI recovery access.

Install [Gitleaks](https://github.com/gitleaks/gitleaks) from a verified release. In each repository install a pre-commit hook (merge with existing hooks rather than overwriting them):

```sh
#!/bin/sh
command -v gitleaks >/dev/null || exit 1
exec gitleaks git --pre-commit --staged --redact .
```

Make `.git/hooks/pre-commit` executable. Also scan history with `gitleaks git --redact .` in CI; hooks can be bypassed by a user and are not the final control. Test in a disposable local repository using the scanner's published synthetic fixture; confirm a staged fixture is rejected and a clean commit succeeds. Never test with a live secret. If one leaks, revoke first, investigate copies/logs and then coordinate history cleanup. Ignoring a file does not remove a committed secret.

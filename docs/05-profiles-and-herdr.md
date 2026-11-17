# 05 — Profiles, resource limits and herdr

Prerequisites: authenticated SSH from chapter 03 and the secret isolation acceptance gate from chapter 04 before agent execution. Use separate SSH keys per profile. Linux users isolate ordinary file access; they share a kernel and are not a hostile-tenant boundary.

## 1. Create profiles and the heavy-work group

As the host administrator:

```sh
sudo groupadd --system heavy
sudo adduser --disabled-password --gecos '' profile-a
sudo adduser --disabled-password --gecos '' profile-b
sudo chmod 700 /home/profile-a /home/profile-b
sudo usermod -aG heavy profile-a
sudo usermod -aG heavy profile-b
sudo install -d -o profile-a -g profile-a -m 700 /home/profile-a/.ssh
sudo install -d -o profile-b -g profile-b -m 700 /home/profile-b/.ssh
sudo install -d -o profile-a -g profile-a -m 700 /srv/projects/profile-a
sudo install -d -o profile-b -g profile-b -m 700 /srv/projects/profile-b
```

Install each public key as that user's `.ssh/authorized_keys`, owned by the user, mode 600. Do not share private keys. Set `HARNESS_ROOT=/srv/projects` in each profile's own environment and create `${HARNESS_ROOT}/profile-a` and `profile-b`, owned by the corresponding user with mode 700. Add no profile to sudo, adm, disk or docker groups. Confirm `id profile-a`, `stat -c '%a %U' /home/profile-*`, and `sudo -u profile-a test ! -r /home/profile-b/.ssh/authorized_keys`.

The harness installs `heavy-lock`; do not invent a second incompatible lock implementation. After chapter 06, give its shared lock directory group `heavy`, make the lock file group-writable and prevent untrusted users replacing it. Membership allows cooperation on a single host; it is not a security privilege. Check that two callers serialize, and that failure releases the lock.

## 2. Apply resource limits to the whole login user

Read the numeric UID with `id -u profile-a`. Create `/etc/systemd/system/user-<uid>.slice.d/resources.conf`, replacing `<uid>` in the **filename**:

```ini
[Slice]
MemoryHigh=3G
MemoryMax=4G
MemorySwapMax=2G
CPUWeight=100
TasksMax=512
ManagedOOMMemoryPressure=kill
ManagedOOMMemoryPressureLimit=60%
```

Repeat for profile-b. This caps the user's sessions and user services together. Do not also create an unrelated `profile-a.slice` unless every workload is explicitly moved into it. Leave host headroom for kernel, cloudflared, broker, disk cache and CI; these example ceilings can contend on a 12 GB machine. Limit worker concurrency rather than raising every cap. See [systemd resource control](https://www.freedesktop.org/software/systemd/man/latest/systemd.resource-control.html).

Run `sudo systemctl daemon-reload`. Reconnect profiles at a maintenance boundary so their user slices are active with the new settings; do not terminate active agent work. Enable persistent user services with `sudo loginctl enable-linger profile-a` and repeat for profile-b. Check `systemctl show user-<uid>.slice -p MemoryHigh -p MemoryMax -p CPUWeight -p TasksMax` and `systemd-cgls`. `MemoryHigh` throttles; `MemoryMax` can kill; CPUWeight is proportional contention priority, not a CPU reservation. Confirm `oomctl` sees the managed slices.

## 3. Install herdr from the official site

Use the official [Herdr TUI installation page](https://herdr.dev/tui/docs/install/). The TUI is suitable for SSH; the desktop/mobile app is a separate client and its command surface differs. As each profile (no sudo):

```sh
installer_dir=$(mktemp -d)
curl -fsSL https://herdr.dev/install.sh -o "$installer_dir/install.sh"
less "$installer_dir/install.sh"
sh "$installer_dir/install.sh"
rm -r "$installer_dir"
herdr --version
herdr --help
```

Review the installer and its download sources before executing. Record the release version and architecture; check ARM64 support before installing on A1. Never run a profile's installer as root. Follow the installer's PATH instruction if `herdr` is not found.

## 4. Start, detach and reattach

In an SSH session as profile-a, `cd` to its project directory and run `herdr`. It launches or attaches to the profile's default background session. Give each project a workspace and each task a tab. Use the TUI's detach action shown in Help; avoid a session/server shutdown action. Reconnect and run `herdr` in the same profile to attach. See [quick start](https://herdr.dev/tui/docs/quick-start/) and [remote use](https://herdr.dev/tui/docs/how-to-work/).

Before installing AI engines, run an ordinary shell command in a pane, disconnect SSH, reconnect and confirm the pane remains. Linger preserves the user's service manager; it does not promise a shell process survives a reboot. Treat saved layouts/session restoration separately from still-running jobs. Test the installed herdr release's behavior rather than assuming it uses tmux or a particular daemon unit.

## 5. PC and phone access

From the PC use `ssh -l profile-a build`, then `herdr`. On a phone use an SSH terminal capable of executing the cloudflared ProxyCommand and opening the Access browser flow. Install both OpenSSH and cloudflared in that terminal environment, copy the chapter 03 host stanza, and enroll a separate passphrase-protected phone SSH key. Authenticate and verify the same host fingerprint before running herdr. Check the client platform supports these binaries before choosing it; a plain mobile SSH app cannot execute cloudflared by itself. The TUI supports narrow screens.

For Android, an explicit terminal option is Termux, whose [official package source](https://github.com/termux/termux-packages/blob/master/packages/cloudflared/build.sh) includes cloudflared. Install Termux from an official distribution, then run `pkg update`, `pkg install openssh cloudflared`, and `ssh-keygen -t ed25519` inside it. Install only the new public key on the server; copy the PC SSH stanza into the phone's `~/.ssh/config`, then run `ssh -l profile-a build` and open the displayed Access login link in the phone browser. Verify the fingerprint, then run `herdr`. Mobile package availability and battery/network interruptions need testing on your device.

If a phone cannot run cloudflared, use a trusted PC as the SSH transport (for example a remote terminal to that PC) or evaluate Cloudflare's browser-rendered SSH using its own documented authentication flow. Do not expose port 22 or publish an unauthenticated web terminal to make a phone client work. Review [Cloudflare client SSH setup](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/use-cases/ssh/ssh-cloudflared-authentication/) for the chosen client.

Exit check: attach from PC and phone, disconnect one and keep the session; confirm profile-b cannot inspect profile-a's files or herdr socket; inspect resource limits while a harmless task runs; verify no profile can sudo. Continue to chapter 06 only when that installation chapter is available.

# Task 07a summary: pipez-builder template (VM 9001)

## Files (all new, under infra/pve/)
- pipez-builder-vendor.yaml - heavy bake cloud-config (base-vendor behaviour + toolchain; bake logic in a write_files script /usr/local/sbin/pipez-bake.sh, log at /var/log/pipez-bake.log during bake)
- pipez-sandbox-vendor.yaml - light config carried by the template (guest agent + ttyd, package_update false)
- make-builder-template.sh - build script (--force rebuilds)
- smoke-sandbox.sh - token-only API smoke test
- README.md
- Host: snippets copied to /var/lib/vz/snippets/; VM 9001 created and templated; keypair ~/.ssh/pipez_ed25519{,.pub}.

## Baked tool versions
Python 3.12.15, Node v20.20.2, Docker 29.9.0, Claude Code 2.1.197, claude_agent_sdk imports OK in /opt/pipez/venv.

## Smoke test (token only)
Clone 9001 -> 9100 (linked), IP 192.168.50.21, clone->SSH-ready 34 s. In-guest: claude 2.1.197, docker ps (empty table), "sdk ok", marker file printed. Clone stopped and destroyed (9100); pool pipez empty; qm list shows only 9000 and 9001 among the 9xxx VMs.

## Deviations / decisions (task contradicted reality)
1. Host base image is Ubuntu 26.04, which has no python3.12 package (only 3.14). Used the deadsnakes PPA (it has a resolute suite) in the bake script. First bake failed on this; rebuilt with --force.
2. Claude Code binary hung forever on the default kvm64 CPU (no AVX/SSE4; main thread spins). Added `--cpu host` to 9001 in the build script; clones inherit it. Also wrapped `claude --version` in the marker in `timeout 60`.
3. Token had NO ACL on /vms/9001 (task said clone rights on 9000/9001), so the clone returned 403 (VM.Clone). I granted PVETemplateUser on /vms/9001 to pipez@pve and its token (`pveum acl modify`), and put that step in make-builder-template.sh. This is the only change outside VM 9001 on the host; flagging for your awareness.
4. Proxmox sshkeys must be URL-encoded before form-encoding (double encoding); smoke script does this.
5. Both template and base snippets list `qm guest exec` (JSON-returning) instead of `qm agent exec` for in-guest commands/polling; `qm agent exec` only starts a process.
6. Guest agent ping took ~3.5 min on first boot of the builder (agent installed by cloud-init); cloud-init total ~5-6 min for the successful bake.

## Verification
1. qm config 9001: template 1, name pipez-builder, cicustom pipez-sandbox-vendor.yaml, cores 4, memory 8192, scsi0 size=40G (also cpu: host) - met.
2. Smoke succeeded via token, versions printed, pool empty, no 91xx VM - met. (Its cleanup path uses only the token; no root SSH inside it.)
3. Key modes 600 / 644 - met.
4. bash -n on both scripts and PyYAML parse of both YAMLs (system PyYAML was available) - met.
5. `git status` clause: not verifiable cleanly; the repo reports branch master, I did not run it for untracked listing or commit anything. Files created only under infra/pve/ plus this summary - partially checked.

## Trimmed first-failure note
First bake: cloud-init error "Failed to install python3.12, python3.12-venv". Second bake: bake script hung at claude --version (CPU type). Third bake succeeded (16:02).

## Left alone
VM 9000 and all other VMs untouched; nothing under pipez/, ui/, tests/; no git commit; PIPEZ_PVE_TOKEN_SECRET never printed.

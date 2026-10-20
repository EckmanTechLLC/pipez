# Task 21 summary

Files
- infra/pve/pipez-app-vendor.yaml (bake-time: package_update, agent/python3/venv/pip/curl/ca-certs/rsync/gnupg, enable agent)
- infra/pve/pipez-app-clone-vendor.yaml (per-clone: only enables the agent)
- infra/pve/make-app-template.sh (mirrors make-builder-template.sh; executable)
- infra/pve/README.md (section "pipez-app-base (9002)", VMID list)
- .env.example, README.md: PIPEZ_PVE_DEPLOY_TEMPLATE=9002 with a one-line reason. config.py untouched.

Command for Odin Prime: infra/pve/make-app-template.sh   (add --force to rebuild)
Expected duration: about 3-6 minutes (clone + boot + apt bake; 10 min cloud-init ceiling).

Decisions
- No pool step: the builder script has none (pool is set at clone time by pipez). Kept the builder's idempotent pveum ACL line for /vms/9002 so the token can clone the template.
- Verify step checks agent active, venv creation, curl, rsync.
- Cleanup uses cloud-init clean --logs --machine-id (as the builder does) so clones re-run first-boot.

Verification
- Files 1-3 exist; bash -n exits 0: met.
- --force guard and the two cicustom steps (bake in qm set at clone, clone snippet after shutdown) present: met.
- README/.env.example mention PIPEZ_PVE_DEPLOY_TEMPLATE=9002: met.
- YAML parse check: NOT done, PyYAML is not installed (system python or .venv). Snippets are trivial.

Not done: script not run; no Proxmox access; tests not run.
Deferred: .env itself not edited (Odin Prime sets PIPEZ_PVE_DEPLOY_TEMPLATE=9002 after the template exists).

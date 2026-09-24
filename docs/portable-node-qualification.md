# Portable node clean-host qualification

Issue #74 requires more than rendering deployment files: the rendered package must actually start the pinned Commander Gym/Argentum services on a host that did not already contain a hand-configured Commander Gym installation.

Two bounded qualifications exercise that deployment contract. They intentionally validate deployment mechanics only; they do not define game, pilot, Binding, evidence, dataset, or learning semantics.

## Rendered-runner qualification

`bash scripts/qualify_portable_node.sh ...` validates the package without installing system services. It:

1. builds a temporary node-package manifest from exact Commander Gym and Argentum commit SHAs;
2. renders the package with `commander_gym.node_package`;
3. runs `commander_gym.node_preflight --no-systemd` against the rendered configuration and initializes an arbitrary temporary storage root;
4. starts Argentum through the rendered `run-argentum-gym.sh` runner;
5. waits for the real Argentum `/health` endpoint;
6. starts the authenticated Commander Gym gateway through the rendered `run-commander-gym-gateway.sh` runner;
7. waits for authenticated gateway health;
8. runs `commander_gym.service_doctor` through that gateway while requiring the exact pinned Argentum build revision;
9. retains the normalized package/storage manifests, service logs, and service-doctor JSON as qualification evidence.

The `clean-host-start` job in `Portable Node Qualification` runs this sequence on a fresh Ubuntu runner with Java 21 and Python 3.12. It pins an exact Commander Gym revision under test and an exact Argentum integration revision.

## Systemd clean-host qualification

`bash scripts/qualify_portable_node_systemd.sh ...` performs the same pinned render and service-doctor contract through the rendered systemd units instead of bypassing them. On a clean systemd host it:

1. renders from exact Commander Gym and Argentum SHAs;
2. runs the normal preflight with `systemctl` required;
3. refuses to overwrite pre-existing `argentum-gym.service` or `commander-gym-gateway.service` units;
4. installs the rendered units into `/etc/systemd/system`;
5. starts Argentum and then the authenticated gateway through systemd;
6. requires both units to remain active while their health endpoints become ready;
7. verifies the live gateway with `commander_gym.service_doctor`, including the exact pinned Argentum build revision;
8. captures `systemctl show` state plus both service journals as qualification evidence;
9. stops and removes the temporary units on exit.

The `systemd-host-start` GitHub Actions job runs this on a fresh Ubuntu VM with systemd active. The workflow exposes its pinned Java 21 runtime through a system-visible `java` executable before service startup; that is host dependency setup, not Commander Gym artifact identity.

A green systemd-host job closes the clean-host **installation/start mechanics** acceptance gap for the rendered package: the checked-in package renderer, ordinary preflight, rendered units, real system manager, pinned source guards, authenticated gateway, and compatibility doctor all work together on a host with no pre-existing Commander Gym service installation.

It does not claim long-running production operations, tunnel/VPN behavior, or the #53 evidence pipeline. Those remain separate concerns.

## Optional local inference and accelerator qualification

Local inference remains deployment configuration rather than Pilot or model-selection policy. A node may configure `local_inference.command`, an optional working directory/environment file, and an optional `local_inference.accelerator_probe` argv list.

When `accelerator_probe` is present, `commander_gym.node_preflight` executes it directly, without a shell, with a bounded timeout. Exit status zero means the deployment-specific capability requirement is available. A nonzero exit status, missing executable, or timeout fails the node preflight closed. Short stdout/stderr excerpts are included in the health report for diagnosis.

Commander Gym does not interpret accelerator vendors or require a particular GPU API. The probe can therefore be a deployment-owned NVIDIA, ROCm, Metal, TPU, CPU-feature, memory-capacity, or provider-specific checker without encoding those technologies into Commander Gym's application contracts. Omitting the probe means the node has no accelerator requirement to validate.

The accelerator probe is intentionally separate from `service_doctor`: preflight verifies that the configured host capability exists before startup, while service doctor verifies reachable running services and Argentum compatibility after startup.

## #53 boundary

These qualifications deliberately stop at storage initialization and service compatibility. They do not invent per-run evidence or byte-accounting records. Issue #53 remains responsible for the run/evidence schema; #74 only supplies the configured storage routes and deployment substrate that those records will use.

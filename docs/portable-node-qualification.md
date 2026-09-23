# Portable node clean-host qualification

Issue #74 requires more than rendering deployment files: the rendered package must actually start the pinned Commander Gym/Argentum services on a host that did not already contain a hand-configured Commander Gym installation.

`bash scripts/qualify_portable_node.sh ...` provides that bounded runtime qualification. It intentionally validates deployment mechanics only; it does not define game, pilot, Binding, evidence, dataset, or learning semantics.

The qualification:

1. builds a temporary node-package manifest from exact Commander Gym and Argentum commit SHAs;
2. renders the package with `commander_gym.node_package`;
3. runs `commander_gym.node_preflight` against the rendered configuration and initializes an arbitrary temporary storage root;
4. starts Argentum through the rendered `run-argentum-gym.sh` runner;
5. waits for the real Argentum `/health` endpoint;
6. starts the authenticated Commander Gym gateway through the rendered `run-commander-gym-gateway.sh` runner;
7. waits for authenticated gateway health;
8. runs `commander_gym.service_doctor` through that gateway while requiring the exact pinned Argentum build revision;
9. retains the normalized package/storage manifests, service logs, and service-doctor JSON as qualification evidence.

The `Portable Node Qualification` GitHub Actions workflow runs this sequence on a fresh Ubuntu runner with Java 21 and Python 3.12. It pins an exact Commander Gym revision under test and an exact Argentum integration revision, so a green run proves the rendered service runners and compatibility checks can bring up a real node from reproducible source inputs rather than relying on the existing Linode's state.

## Production-systemd boundary

The CI runner is not treated as a production systemd host. Its preflight uses `--no-systemd` and starts the rendered runner scripts directly. Therefore this qualification proves the clean-host package/runtime path but does **not** by itself close the final production-systemd installation/start acceptance criterion.

The remaining production qualification should install the rendered units on a real clean systemd host, run the normal preflight without `--no-systemd`, start the units through systemd, and verify the same service-doctor contract. That later host may be local hardware, a fresh VM, or another replaceable deployment target; Linode, Cloudflare, and Tailscale are not architectural requirements.

## #53 boundary

This qualification deliberately stops at storage initialization and service compatibility. It does not invent per-run evidence or byte-accounting records. Issue #53 remains responsible for the run/evidence schema; #74 only supplies the configured storage routes and deployment substrate that those records will use.

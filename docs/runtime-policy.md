# Docker sandbox runtime policy

The skill worker and CSV release smoke use the same conservative project policy in `scripts/runtime_policy.py`. Docker must report stable versions of `runc` at least `1.4.3` and `containerd` at least `2.3.6`. The policy also rejects `containerd 2.4.0`, including version strings with build metadata, because that release is affected by CVE-2026-53493. Missing, malformed, prerelease, or unavailable versions fail closed. The skill worker checks the policy at startup and before each queued job.

This is a project support baseline, not a statement that every lower version is vulnerable or every accepted version is free from vulnerabilities. Passing the check does not replace reviewing current advisories and testing the actual isolation and cleanup behavior. The worker's locally built image uses `--pull=never`; the image-pull advisory still matters to the surrounding Docker installation and its other image pulls.

## Advisory fixes and project support

The [runc advisory for CVE-2026-41579](https://github.com/opencontainers/runc/security/advisories/GHSA-xjvp-4fhw-gc47) lists fixes in `1.3.6`, `1.4.3`, and `1.5.0-rc.3`. It states that Docker masks the malicious `/dev` symlink involved in that specific issue. The project nevertheless does not accept the older `1.3.x` branch under its current baseline.

The [containerd advisory for CVE-2026-53493](https://github.com/containerd/containerd/security/advisories/GHSA-pg57-6jwg-q645) lists fixes including `2.2.9`, `2.3.6`, and `2.4.1`; it identifies `2.4.0` as affected. The project still rejects `2.2.x`, even when a particular version contains this fix. Build metadata alone does not prove that a vendor backport has been independently verified.

| Component version | Current project policy | Reason |
| --- | --- | --- |
| runc `1.3.6` | Reject | Contains the cited fix, but is below the project baseline. |
| runc `1.4.3` | Accept | Meets the project baseline. |
| containerd `2.2.9` | Reject | Contains the cited fix, but is below the project baseline. |
| containerd `2.3.6` | Accept | Meets the project baseline. |
| containerd `2.4.0` | Reject | Explicitly listed as affected by the cited advisory. |
| containerd `2.4.1` | Accept | Above the baseline and contains the cited advisory fix. |

Acceptance in this table concerns the version gate only. It is not a release approval for any installation. Change the policy only with explicit project review, updated regression tests, and applicable runtime verification.

## Checks

Run `python -m django test tests.test_runtime_policy` with the project's Django settings configured. These tests cover the conservative baseline, older fixed branches, the explicit `2.4.0` exclusion, malformed versions, and runtime unavailability. Run the Docker sandbox smoke tests with the gate enabled on a supported runtime for behavioral evidence.

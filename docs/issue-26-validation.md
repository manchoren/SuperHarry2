# Issue 26: skill lifecycle validation

Date: 2026-11-25. Base revision: `5dadec18704521dd3af0ee8a7b2a005f191a6286`; the implementation and tests below were an uncommitted working tree. The SHA-256 of the UTF-8 file-hash manifest below, including its final newline, is `10db6fb58520b9770eb065fa757729127f907e0d2cadfbaeb4e068c9ebc59002`. The manifest covers changed source, templates, migration, and tests; unchanged files are identified by the base revision. This is local development evidence, not a deployment or production acceptance.

At staging, Git normalized line endings in `superharry/chat.py`, `superharry/scheduler.py`, `tests/test_chat.py`, and `tests/test_skills.py`; their content otherwise matched the tested working tree. Applying the same manifest procedure to the staged blobs yields SHA-256 `a59bbbcc065e9fa1b5ce4df5eda5400a4295637ec12c34229d259628fe3df2d5`.

## Results

- `DJANGO_SETTINGS_MODULE=superharry.settings DJANGO_SECRET_KEY=synthetic-test-only SKILL_NETWORK_DOCKER_SMOKE=1 python -m django test --noinput`: 201 tests passed on Windows, including the real Docker lifecycle, network, concurrency, interrupted worker, and failure cases.
- `SKILL_TEST_IMAGE=superharry-skill-network-test:local python -m tests.skill_desktop_smoke`: passed with a disposable Django container, temporary database and shared spool, including interruption and restart from the Windows host worker.
- `SKILL_TEST_IMAGE=superharry-skill-network-test:local python -m tests.skill_sandbox_smoke`: passed isolation, limits, and cleanup checks.
- `python -m django makemigrations --check --dry-run` with the synthetic Django settings above: no changes detected.
- Docker Desktop Engine 29.9.3, runc 1.5.3, containerd 2.4.2. Test worker image ID: `sha256:5da2f3a435b2a8d603df830585c820bc98591628e6b08faacd56fd34daa07980`.

The checks cover updated and deleted active jobs, original input, changed dependencies, pending and already-performed external actions without automatic repetition, owner-only notices and retained results, concurrent edit/delete/execution, database contention, interrupted publication and worker recovery, invalid controls, and legacy jobs with unknown action history. The current deployment was not changed. Dedicated Linux VM acceptance remains separately tracked in #28.

## Changed-file SHA-256 manifest

```text
d2c01b2e08a499b3ea154cb1261a9a60201a45a94560aac917f9fa9a9c373a56  scripts/run_skill_jobs.py
28495d6d0c43e487e4551c67e09da5e56c8b59cab1d181af9c45f60af5fe302b  scripts/skill_sandbox.py
827a84adc513fd629d8781986b61797453c51db9420ad3ac3a983e33670d2a89  superharry/chat.py
952aa8b385e46f2361f22c976caa3405e309cab572f23f241ad54e24e0c4b66b  superharry/migrations/0026_skillrun_lifecycle.py
59c1d48af723fc643a4b5ed4621ffe459c39517d06a145590fa0bab3b2831bd8  superharry/models.py
5fded2a6f54165f5f82e0acf05bd0586d1af0291b6e4704d57490e2956d6a469  superharry/scheduler.py
4dc06a977b1236ec0c5fca592bc006714a6bb120a297fc527e5867e63e9b0b0d  superharry/skill_jobs.py
480e11a3acf6999fbe3ae309e375f684a5c98e77470b4a40e3084abc7d1537f3  superharry/skill_views.py
ddbe5ad71f9585ce81d4d63966adf9f89fd5ea382dd74175775d0ed2d76d6059  superharry/templates/skills/catalog.html
d8859a2907f60db44538e4a24b314898e9fabc224989d1554a7d4b1d76f96b17  superharry/templates/skills/run_detail.html
b049ad092502da71b2b55415dc6993aba4ba1dee8793157c6c5652307b2c0c89  tests/skill_desktop_smoke.py
8946b551eb30e3090e80d591b17e811d96b559d6b1e237dae474c387152ecd3a  tests/test_chat.py
b29a21a9cfa11761cc06b9a74cfa093d105ac4f54e72412d43c10c47b5564dfa  tests/test_skill_network_approval.py
7a654820afb43332635ddf47d81df5037a0a4f36de56242c70504e8a8e126386  tests/test_skill_network_docker.py
368ff4a80aaffc946cedabdebad1cc8928a7d25a3ecf08820c1376ad7c203642  tests/test_skill_sandbox_cleanup.py
72a400bacf9efb629b6a3fac1af0c5b8cf44f7dab9209c15751a3d0160d1b3e1  tests/test_skills.py
```

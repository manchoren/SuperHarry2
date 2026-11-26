# File preview dependencies

The chat interface includes local copies of FilePond 4.32.14 (MIT) and PDF.js distribution 6.5.438 (Apache-2.0). Their license texts are distributed alongside the files. The versions were reviewed on 2026-11-26 from the npm distributions and the projects' official documentation. FilePond supplies multi-file selection; PDF.js renders PDF pages in the browser with its local worker. The application sends selected files through its existing authenticated request rather than FilePond's upload server.

PDF and image previews are local to the browser. PDF rendering disables PDF.js dynamic evaluation. The server still enforces attachment limits and ownership. Downloads use attachment disposition and are not made public by the preview. These dependencies should be updated deliberately with their licenses and browser smoke test rechecked.

Sources: [FilePond properties](https://pqina.nl/filepond/docs/api/instance/properties/) and [PDF.js examples](https://mozilla.github.io/pdf.js/examples/).

Generated skill files are exported from the sandbox's `output` directory. `DJANGO_SKILL_OUTPUT_MAX_COUNT` defaults to 5 files and `DJANGO_SKILL_OUTPUT_MAX_BYTES` defaults to 2 MiB total per run. Operators can lower either value; the worker rejects settings above those hard limits. Output files are staged on the restricted skill spool before the container is removed, then imported into the application's database. Results from failed or timed-out executions are marked partial when their output could be recovered.

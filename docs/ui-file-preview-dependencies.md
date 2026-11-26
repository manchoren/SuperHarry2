# File preview dependencies

The chat interface includes local copies of FilePond 4.32.14 (MIT) and PDF.js distribution 6.5.438 (Apache-2.0). Their license texts are distributed alongside the files. The versions were reviewed on 2026-11-26 from the npm distributions and the projects' official documentation. FilePond supplies multi-file selection; PDF.js renders PDF pages in the browser with its local worker. The application sends selected files through its existing authenticated request rather than FilePond's upload server.

PDF and image previews are local to the browser. PDF rendering disables PDF.js dynamic evaluation. The server still enforces attachment limits and ownership. Downloads use attachment disposition and are not made public by the preview. These dependencies should be updated deliberately with their licenses and browser smoke test rechecked.

Sources: [FilePond properties](https://pqina.nl/filepond/docs/api/instance/properties/) and [PDF.js examples](https://mozilla.github.io/pdf.js/examples/).

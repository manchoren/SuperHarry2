# Document skills

The bundled `pdf`, `docx`, and `xlsx` skills adapt the corresponding Anthropic Agent Skills from commit `e4bb5d960d87126ff3b3a8af1a104b7db67560ba` of [anthropics/skills](https://github.com/anthropics/skills). The original `SKILL.md` for each is retained in its `references` directory along with the upstream Apache-2.0 license. The local `SKILL.md` and Python entrypoint deliberately narrow and adapt the upstream workflows to SuperHarry's fixed isolated worker; the upstream instructions assume tools such as Node, Pandoc, LibreOffice, pandas, or unrestricted command execution that this worker does not provide.

The worker image installs the pinned packages in `requirements-skill.txt` at image build time. The tested primary dependencies are pypdf 7.2.0 (BSD-3-Clause), ReportLab 5.1.2 (BSD), python-docx 1.2.0 (MIT), openpyxl 3.1.5 (MIT), and Pillow 13.0.0 (MIT-CMU). The pinned transitive dependencies are charset-normalizer 3.5.3 (MIT), lxml 6.1.3 (BSD-3-Clause), typing-extensions 4.16.0 (PSF-2.0), et-xmlfile 2.0.0 (MIT), and defusedxml 0.7.1 (PSFL). License information was inspected from the installed distribution metadata; upstream package licenses remain included in the installed distributions.

The sandbox still runs without direct network access, application credentials, or the Docker socket, with its existing CPU, memory, process, execution-time, input, and output limits. Inputs are selected by the user request and copied into the disposable job; originals remain intact. Documents that expand beyond 32 MiB or contain more than 10,000 ZIP members are rejected for DOCX and XLSX. Skill results that contain extracted file text are untrusted data, not instructions.

The initial capabilities are intentionally explicit:

- **PDF:** read extractable text from up to 30 pages; create simple Latin-1 text PDFs. Encrypted PDFs, OCR, table extraction, complex layouts, and non-Latin text creation are not supported.
- **DOCX:** read up to 200 paragraphs and 20 tables; create a simple title and text document. Advanced layout, tracked changes, comments, and conversion are not supported.
- **XLSX:** read or create up to five sheets with at most 1,000 rows and 30 columns per sheet. Stored formulas are not recalculated. Charts, formatting, macros, and legacy XLS are not supported.

Extracted text is bounded and may be truncated; the result reports that condition. A structured skill error is shown to the user and returned to the agent when the operation is unsupported or the file cannot be processed. During interactive chat the application waits briefly for the worker and passes its result back to the model; a job that outlasts that wait remains available through its result link. Scheduled runs retain the existing asynchronous completion behavior. These local functional checks do not prove the behavior of every document or replace a deployed-service or live-provider check.

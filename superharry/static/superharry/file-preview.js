import * as pdfjs from "./vendor/pdf.min.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = new URL("./vendor/pdf.worker.min.mjs", import.meta.url).href;
const panel = document.querySelector("#file-preview");
const title = document.querySelector("#preview-name");
const status = document.querySelector("#preview-status");
const image = document.querySelector("#preview-image");
const canvas = document.querySelector("#preview-pdf");
const pages = document.querySelector("#preview-pages");
const pageNumber = document.querySelector("#preview-page-number");
const download = document.querySelector("#preview-download");
const previous = document.querySelector("#preview-prev");
const next = document.querySelector("#preview-next");
let pdf = null;
let loadingTask = null;
let currentPage = 1;
let objectUrl = null;
let generation = 0;

function reset() {
  generation++;
  if (objectUrl) URL.revokeObjectURL(objectUrl);
  objectUrl = null;
  if (loadingTask) loadingTask.destroy();
  loadingTask = null;
  pdf = null;
  image.hidden = true;
  canvas.hidden = true;
  pages.hidden = true;
  download.hidden = true;
  status.textContent = "";
}

async function renderPage() {
  const document = pdf;
  previous.disabled = true;
  next.disabled = true;
  try {
    const page = await document.getPage(currentPage);
    if (document !== pdf) return;
    const viewport = page.getViewport({scale: 1});
    const scale = Math.min(1.5, 800 / viewport.width, 1200 / viewport.height);
    const scaled = page.getViewport({scale});
    canvas.width = Math.ceil(scaled.width);
    canvas.height = Math.ceil(scaled.height);
    await page.render({canvas, canvasContext: canvas.getContext("2d"), viewport: scaled}).promise;
    if (document === pdf) pageNumber.textContent = `Page ${currentPage} of ${document.numPages}`;
  } finally {
    if (document === pdf) {
      previous.disabled = currentPage <= 1;
      next.disabled = currentPage >= document.numPages;
    }
  }
}

async function showFile(name, file, url = null) {
  reset();
  const request = generation;
  panel.hidden = false;
  title.textContent = name;
  status.textContent = "Loading preview…";
  if (url) {
    download.href = url;
    download.hidden = false;
  }
  try {
    if (!file && url) {
      const response = await fetch(url);
      if (!response.ok) throw new Error("File is unavailable.");
      file = await response.blob();
    }
    if (request !== generation) return;
    if (/\.pdf$/i.test(name)) {
      const task = pdfjs.getDocument({data: await file.arrayBuffer(), isEvalSupported: false});
      loadingTask = task;
      const loaded = await task.promise;
      if (request !== generation) { await task.destroy(); return; }
      pdf = loaded;
      currentPage = 1;
      canvas.hidden = false;
      pages.hidden = false;
      await renderPage();
      status.textContent = "";
    } else if (/\.(png|jpe?g)$/i.test(name)) {
      objectUrl = URL.createObjectURL(new Blob([file], {type: /\.png$/i.test(name) ? "image/png" : "image/jpeg"}));
      image.src = objectUrl;
      image.alt = `Preview of ${name}`;
      image.hidden = false;
      status.textContent = "";
    } else {
      status.textContent = `File: ${name} (${file.size} bytes). Preview is not available for this format.`;
    }
  } catch {
    if (request === generation) status.textContent = "Preview is unavailable for this file.";
  }
}

window.previewChatFile = (name, url) => showFile(name, null, url);
document.querySelector("#preview-close").addEventListener("click", () => { reset(); panel.hidden = true; });
previous.addEventListener("click", () => { if (pdf && currentPage > 1) { currentPage--; renderPage().catch(() => { status.textContent = "Preview is unavailable for this file."; }); } });
next.addEventListener("click", () => { if (pdf && currentPage < pdf.numPages) { currentPage++; renderPage().catch(() => { status.textContent = "Preview is unavailable for this file."; }); } });
document.querySelectorAll(".preview-file").forEach((button) => button.addEventListener("click", () => showFile(button.dataset.name, null, button.dataset.url)));

if (window.FilePond && FilePond.supported()) {
  window.chatFilePond = FilePond.create(document.querySelector("#attachment"), {allowMultiple: true, allowProcess: false});
  window.chatFilePond.on("addfile", (error, item) => { if (!error) showFile(item.filename, item.file); });
}

"use strict";

const STATUS_POLL_MS = 1000;
const MAX_THRESHOLD = 0.7;
const SELFIE_QUALITY = 0.92;
const SELFIE_WIDTH = 1280;

const el = Object.fromEntries(
  ["status", "video", "preview", "placeholder", "cameraButton", "captureButton",
   "fileInput", "message", "results", "summary", "threshold", "thresholdValue",
   "downloadButton", "grid", "canvas"].map((id) => [id, document.getElementById(id)])
);

let state = { ready: false, busy: false, matches: [], stream: null };

function update(changes) {
  state = { ...state, ...changes };
}

function showMessage(text) {
  el.message.textContent = text || "";
  el.message.hidden = !text;
}

function setStatus(text, isError = false) {
  el.status.textContent = text;
  el.status.classList.toggle("error", isError);
}

async function readEnvelope(response) {
  let body;
  try {
    body = await response.json();
  } catch (error) {
    throw new Error(`Unexpected response from the server (${response.status}).`);
  }
  if (!response.ok || !body.success) {
    throw new Error(body.error || `Request failed (${response.status}).`);
  }
  return body.data;
}

function describeStatus(data) {
  if (data.state === "ready") {
    const skipped = data.unreadable ? `, ${data.unreadable} unreadable` : "";
    return `Gallery ready: ${data.photos} photos, ${data.faces} faces found${skipped}.`;
  }
  if (data.state === "error") {
    return `The gallery could not be indexed: ${data.error}`;
  }
  return data.total
    ? `Indexing the gallery… ${data.processed} / ${data.total} photos`
    : "Indexing the gallery…";
}

async function pollStatus() {
  try {
    const data = await readEnvelope(await fetch("/api/status"));
    setStatus(describeStatus(data), data.state === "error");
    if (data.state === "ready" && !state.ready) {
      el.threshold.min = data.min_score;
      el.threshold.max = MAX_THRESHOLD;
      el.threshold.value = data.default_threshold;
      update({ ready: true });
    }
    if (data.state === "indexing") {
      setTimeout(pollStatus, STATUS_POLL_MS);
    }
  } catch (error) {
    setStatus(`Cannot reach the server: ${error.message}`, true);
    setTimeout(pollStatus, STATUS_POLL_MS * 3);
  }
}

function stopCamera() {
  if (state.stream) {
    state.stream.getTracks().forEach((track) => track.stop());
  }
  el.video.srcObject = null;
  el.video.hidden = true;
  el.captureButton.hidden = true;
  el.cameraButton.textContent = "Start camera";
  update({ stream: null });
}

async function startCamera() {
  showMessage("");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showMessage("This browser cannot access the camera. Upload a photo instead.");
    return;
  }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "user", width: { ideal: SELFIE_WIDTH } },
      audio: false,
    });
    el.video.srcObject = stream;
    el.video.hidden = false;
    el.preview.hidden = true;
    el.placeholder.hidden = true;
    el.captureButton.hidden = false;
    el.cameraButton.textContent = "Stop camera";
    update({ stream });
  } catch (error) {
    showMessage(`Could not start the camera (${error.message}). Upload a photo instead.`);
  }
}

function captureSelfie() {
  const { videoWidth, videoHeight } = el.video;
  if (!videoWidth || !videoHeight) {
    showMessage("The camera is not ready yet, try again in a moment.");
    return;
  }
  el.canvas.width = videoWidth;
  el.canvas.height = videoHeight;
  el.canvas.getContext("2d").drawImage(el.video, 0, 0);
  el.canvas.toBlob((blob) => {
    if (!blob) {
      showMessage("Could not capture the image, try again.");
      return;
    }
    stopCamera();
    findPhotos(blob);
  }, "image/jpeg", SELFIE_QUALITY);
}

function showPreview(blob) {
  if (el.preview.src) {
    URL.revokeObjectURL(el.preview.src);
  }
  el.preview.src = URL.createObjectURL(blob);
  el.preview.hidden = false;
  el.placeholder.hidden = true;
}

async function findPhotos(blob) {
  if (state.busy) {
    return;
  }
  showPreview(blob);
  if (!state.ready) {
    showMessage("The gallery is not ready yet. Try again when indexing has finished.");
    return;
  }
  update({ busy: true });
  showMessage("");
  el.summary.textContent = "Searching…";
  el.results.hidden = false;
  el.grid.replaceChildren();
  try {
    const form = new FormData();
    form.append("selfie", blob, "selfie.jpg");
    const data = await readEnvelope(await fetch("/api/search", { method: "POST", body: form }));
    update({ matches: data.matches });
    renderResults();
  } catch (error) {
    el.results.hidden = true;
    showMessage(error.message);
  } finally {
    update({ busy: false });
  }
}

function visibleMatches() {
  const threshold = Number(el.threshold.value);
  return state.matches.filter((match) => match.score >= threshold);
}

function createCard(match) {
  const card = document.createElement("a");
  card.className = "card";
  card.href = `/photos/${encodeURIComponent(match.filename)}`;
  card.target = "_blank";
  card.rel = "noopener";

  const image = document.createElement("img");
  image.src = card.href;
  image.alt = match.filename;
  image.loading = "lazy";

  const score = document.createElement("span");
  score.className = "score";
  score.textContent = match.score.toFixed(2);

  const name = document.createElement("div");
  name.className = "name";
  name.textContent = match.filename;

  card.append(image, score, name);
  return card;
}

function renderResults() {
  const matches = visibleMatches();
  el.thresholdValue.textContent = Number(el.threshold.value).toFixed(2);
  el.summary.textContent = matches.length === 1
    ? "1 photo with you in it"
    : `${matches.length} photos with you in it`;
  el.downloadButton.disabled = matches.length === 0;
  el.grid.replaceChildren(...matches.map(createCard));
}

async function downloadZip() {
  const filenames = visibleMatches().map((match) => match.filename);
  if (filenames.length === 0) {
    return;
  }
  el.downloadButton.disabled = true;
  showMessage("");
  try {
    const response = await fetch("/api/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filenames }),
    });
    if (!response.ok) {
      await readEnvelope(response);
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = "my-photos.zip";
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (error) {
    showMessage(`Download failed: ${error.message}`);
  } finally {
    el.downloadButton.disabled = false;
  }
}

el.cameraButton.addEventListener("click", () => (state.stream ? stopCamera() : startCamera()));
el.captureButton.addEventListener("click", captureSelfie);
el.fileInput.addEventListener("change", () => {
  const file = el.fileInput.files[0];
  el.fileInput.value = "";
  if (file) {
    stopCamera();
    findPhotos(file);
  }
});
el.threshold.addEventListener("input", renderResults);
el.downloadButton.addEventListener("click", downloadZip);
window.addEventListener("beforeunload", stopCamera);

pollStatus();

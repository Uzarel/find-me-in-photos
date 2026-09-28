"use strict";

const STATUS_POLL_MS = 1000;
const MAX_THRESHOLD = 0.7;
const SELFIE_QUALITY = 0.92;
const SELFIE_WIDTH = 1280;
const UPLOAD_MAX_SIDE = 1280;
const CODE_PARAMETER = "code";
const UNAUTHORIZED = 401;

const el = Object.fromEntries(
  ["title", "login", "loginForm", "codeInput", "loginMessage", "finder", "status",
   "video", "preview", "placeholder", "cameraButton", "captureButton", "fileInput",
   "notice", "message", "results", "summary", "threshold", "thresholdValue", "downloadButton",
   "grid", "canvas"].map((id) => [id, document.getElementById(id)])
);

let state = { ready: false, busy: false, polling: false, matches: [], stream: null };

function update(changes) {
  state = { ...state, ...changes };
}

function showText(element, text) {
  element.textContent = text || "";
  element.hidden = !text;
}

const showMessage = (text) => showText(el.message, text);
const showNotice = (text) => showText(el.notice, text);

// The search uses one face. With several in the photo, the guest may get the
// photos of whoever is closest to the camera, so the page says what it did.
function describeFaces(count) {
  if (count <= 1) {
    return "";
  }
  return `There are ${count} faces in your photo, so we searched for the largest one. `
    + "If these are not your photos, use a photo with only you in it.";
}

function setStatus(text, isError = false) {
  el.status.textContent = text;
  el.status.classList.toggle("error", isError);
}

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function readEnvelope(response) {
  let body;
  try {
    body = await response.json();
  } catch (error) {
    throw new ApiError(`Unexpected response from the server (${response.status}).`,
                       response.status);
  }
  if (!response.ok || !body.success) {
    throw new ApiError(body.error || `Request failed (${response.status}).`,
                       response.status);
  }
  return body.data;
}

// Every call to a protected endpoint goes through here, so an expired
// session always brings the guest back to the access code screen.
async function request(url, options) {
  const response = await fetch(url, options);
  if (response.status === UNAUTHORIZED) {
    showLogin("Your session has ended. Enter the access code again.");
    throw new ApiError("Enter the access code to continue.", UNAUTHORIZED);
  }
  return response;
}

function showLogin(text) {
  stopCamera();
  el.finder.hidden = true;
  el.login.hidden = false;
  showText(el.loginMessage, text);
  el.codeInput.focus();
}

function showFinder() {
  el.login.hidden = true;
  el.finder.hidden = false;
  if (!state.polling) {
    update({ polling: true });
    pollStatus();
  }
}

async function login(code) {
  return readEnvelope(await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  }));
}

// A join link carries the code after the "#", which browsers never send to
// the server, so it stays out of logs. It is removed from the address bar.
function takeCodeFromLink() {
  const parameters = new URLSearchParams(window.location.hash.slice(1));
  const code = parameters.get(CODE_PARAMETER);
  if (code) {
    history.replaceState(null, "", window.location.pathname);
  }
  return code;
}

async function start() {
  const linkCode = takeCodeFromLink();
  try {
    let session = await readEnvelope(await fetch("/api/session"));
    el.title.textContent = session.event_name;
    document.title = session.event_name;
    if (session.auth_required && !session.authenticated && linkCode) {
      session = await login(linkCode);
    }
    if (session.authenticated) {
      showFinder();
    } else {
      showLogin("");
    }
  } catch (error) {
    showLogin(error.message);
  }
}

async function submitLogin(event) {
  event.preventDefault();
  try {
    await login(el.codeInput.value);
    el.codeInput.value = "";
    showFinder();
  } catch (error) {
    showText(el.loginMessage, error.message);
  }
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
    const data = await readEnvelope(await request("/api/status"));
    setStatus(describeStatus(data), data.state === "error");
    if (data.state === "ready" && !state.ready) {
      el.threshold.min = data.min_score;
      el.threshold.max = MAX_THRESHOLD;
      el.threshold.value = data.default_threshold;
      update({ ready: true });
    }
    if (data.state === "indexing") {
      setTimeout(pollStatus, STATUS_POLL_MS);
    } else {
      update({ polling: false });
    }
  } catch (error) {
    if (error.status === UNAUTHORIZED) {
      update({ polling: false });
      return;
    }
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
  showNotice("");
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showMessage("This browser cannot access the camera here. Upload a photo instead.");
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

function canvasToBlob(width, height, source) {
  el.canvas.width = width;
  el.canvas.height = height;
  el.canvas.getContext("2d").drawImage(source, 0, 0, width, height);
  return new Promise((resolve) => el.canvas.toBlob(resolve, "image/jpeg", SELFIE_QUALITY));
}

async function captureSelfie() {
  const { videoWidth, videoHeight } = el.video;
  if (!videoWidth || !videoHeight) {
    showMessage("The camera is not ready yet, try again in a moment.");
    return;
  }
  const blob = await canvasToBlob(videoWidth, videoHeight, el.video);
  if (!blob) {
    showMessage("Could not capture the image, try again.");
    return;
  }
  stopCamera();
  findPhotos(blob);
}

// Phone photos are large. Shrinking them in the browser keeps uploads fast
// on event Wi-Fi; if the browser cannot do it, the original is sent as it is.
async function shrinkImage(file) {
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, UPLOAD_MAX_SIDE / Math.max(bitmap.width, bitmap.height));
    const blob = await canvasToBlob(Math.round(bitmap.width * scale),
                                    Math.round(bitmap.height * scale), bitmap);
    bitmap.close();
    return blob || file;
  } catch (error) {
    console.warn("Could not shrink the image, uploading the original.", error);
    return file;
  }
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
  showNotice("");
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
    const response = await request("/api/search", { method: "POST", body: form });
    const data = await readEnvelope(response);
    update({ matches: data.matches });
    showNotice(describeFaces(data.faces_in_selfie));
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
    const response = await request("/api/download", {
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

el.loginForm.addEventListener("submit", submitLogin);
el.cameraButton.addEventListener("click", () => (state.stream ? stopCamera() : startCamera()));
el.captureButton.addEventListener("click", captureSelfie);
el.fileInput.addEventListener("change", async () => {
  const file = el.fileInput.files[0];
  el.fileInput.value = "";
  if (file) {
    stopCamera();
    findPhotos(await shrinkImage(file));
  }
});
el.threshold.addEventListener("input", renderResults);
el.downloadButton.addEventListener("click", downloadZip);
window.addEventListener("beforeunload", stopCamera);

start();

// =====================
// ELEMENTS
// =====================
const imgView = document.getElementById("img-view");
const toggleBtn = document.getElementById("toggle-btn");
const recalcBtn = document.getElementById("recalc-btn");
const exportBtn = document.getElementById("export-btn");
const newGuestBtn = document.getElementById("new-guest-btn");
const addSecondaryBtn = document.getElementById("add-secondary-btn");
const retryBtn = document.getElementById("retry-btn");

const inputFile = document.getElementById("input-file");
const previewImg = document.getElementById("preview-img");
const camera = document.getElementById("camera");
const snapBtn = document.getElementById("snap-btn");
const dropText = document.getElementById("drop-text");
const loading = document.getElementById("loading-indicator");

const refIdInput = document.getElementById("ref-id");
const ageInput = document.getElementById("age");

const idSlotSelect = document.getElementById("id-slot");
const idCategoryInput = document.getElementById("id-category");
const secondaryCountInput = document.getElementById("secondary-count");
const canProceedInput = document.getElementById("can-proceed");

const idTypeInput = document.getElementById("id-type");
const idNoInput = document.getElementById("id-no");
const backHomeBtn = document.getElementById("back-home-btn");

const firstNameInput = document.getElementById("first-name");
const middleNameInput = document.getElementById("middle-name");
const lastNameInput = document.getElementById("last-name");
const dobInput = document.getElementById("dob");
const genderInput = document.getElementById("gender");
const contactInput = document.getElementById("contact");
const addressInput = document.getElementById("address");

// =====================
// STATE
// =====================
let cameraActive = false;
let stream = null;
let currentRefId = "";
const PLACEHOLDER_SRC = previewImg.src;
let currentObjectUrl = null;

// ✅ Retry state (works for BOTH upload + camera)
let lastScanSource = null; // "camera" | "upload"
let lastScanFile = null;   // File
let lastScanSlot = "primary";
let lastPredictedType = "";

// =====================
// TEACHABLE MACHINE
// =====================
let tmModel = null;
let tmLoadingPromise = null;

async function ensureTM() {
  if (tmModel) return tmModel;
  if (!tmLoadingPromise) {
    const URL = "/static/my_model/";
    tmLoadingPromise = tmImage
      .load(URL + "model.json", URL + "metadata.json")
      .then((m) => (tmModel = m));
  }
  return tmLoadingPromise;
}

async function predictIDType(imgEl) {
  try {
    const model = await ensureTM();
    const prediction = await model.predict(imgEl);
    const best = prediction.reduce((a, b) =>
      a.probability > b.probability ? a : b
    );
    return best.className || "";
  } catch {
    return "";
  }
}

// =====================
// HELPERS
// =====================
function showLoading(text = "Scanning...") {
  loading.style.display = "flex";
  loading.textContent = text;
}
function hideLoading() {
  loading.style.display = "none";
  loading.textContent = "Scanning...";
}

function setRetryEnabled(on) {
  retryBtn.disabled = !on;
}

async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  const text = await res.text();

  let data;
  try {
    data = JSON.parse(text);
  } catch {
    console.error("Non-JSON response:", text);
    throw new Error("Server returned HTML/non-JSON. Check Flask console.");
  }

  if (!res.ok) {
    throw new Error(
      (data.error || "Request failed") +
        (data.details ? " | " + data.details : "")
    );
  }
  return data;
}

function computeAge(dobStr) {
  if (!dobStr || !/^\d{4}-\d{2}-\d{2}$/.test(dobStr)) return "";
  const [y, m, d] = dobStr.split("-").map(Number);
  const dob = new Date(y, m - 1, d);
  if (isNaN(dob.getTime())) return "";

  const today = new Date();
  let age = today.getFullYear() - dob.getFullYear();
  const hasHadBirthday =
    today.getMonth() > dob.getMonth() ||
    (today.getMonth() === dob.getMonth() && today.getDate() >= dob.getDate());
  if (!hasHadBirthday) age--;

  if (age < 0 || age > 130) return "";
  return String(age);
}

function refreshAgePreview() {
  ageInput.value = computeAge(dobInput.value.trim());
  updateCanProceedUI();
}

function updateCanProceedUI() {
  const serverProceed = canProceedInput.value === "YES";
  const idCat = (idCategoryInput.value || "").trim().toUpperCase();

  const notUnknown = idCat && idCat !== "UNKNOWN";
  exportBtn.disabled = !(serverProceed && notUnknown);

  if (idCat === "SECONDARY") {
    addSecondaryBtn.style.display = "inline-block";
  } else {
    addSecondaryBtn.style.display = "none";
  }
}

function setPreviewSrc(src, isObjectUrl = false) {
  if (currentObjectUrl) {
    try { URL.revokeObjectURL(currentObjectUrl); } catch {}
    currentObjectUrl = null;
  }
  if (isObjectUrl) currentObjectUrl = src;

  previewImg.src = src;
  previewImg.style.display = "block";
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    camera.srcObject = null;
    stream = null;
  }
}

async function startCamera() {
  stream = await navigator.mediaDevices.getUserMedia({
    video: {
      width: { ideal: 1920 },
      height: { ideal: 1080 },
      facingMode: { ideal: "environment" },
    },
  });
  camera.srcObject = stream;
  await camera.play();
}

if (backHomeBtn) {
  backHomeBtn.addEventListener("click", () => {
    window.location.href = "/";
  });
}

// ✅ Unified scan function (used by Upload + Camera + Retry)
async function scanFileToServer(file, { slot, predictedType } = {}) {
  const formData = new FormData();
  formData.append("file", file);

  if (currentRefId) formData.append("reference_id", currentRefId);
  formData.append("slot", slot || idSlotSelect.value);
  if (predictedType) formData.append("predicted_id_type", predictedType);

  return await fetchJSON("/upload", { method: "POST", body: formData });
}

// =====================
// MODES
// =====================
function setUploadMode(keepPreview = false) {
  cameraActive = false;
  imgView.classList.remove("camera-mode");
  stopCamera();

  if (!keepPreview) setPreviewSrc(PLACEHOLDER_SRC, false);

  dropText.textContent = "Upload your ID here";
  toggleBtn.textContent = "Use Camera";
}

async function setCameraMode() {
  cameraActive = true;
  imgView.classList.add("camera-mode");

  dropText.textContent = "Point your ID to the camera";
  toggleBtn.textContent = "Use Upload";

  await startCamera();
}

// =====================
// APPLY RECORD
// =====================
function applyRecordToUI(rec) {
  if (!rec) return;

  currentRefId = rec.Reference_id || currentRefId;
  refIdInput.value = currentRefId;

  const guest = rec.Guest || {};

  if (!firstNameInput.value) firstNameInput.value = guest.First_name || "";
  if (!middleNameInput.value) middleNameInput.value = guest.Middle_name || "";
  if (!lastNameInput.value) lastNameInput.value = guest.Last_name || "";
  if (!dobInput.value) dobInput.value = guest.Date_of_birth || "";
  if (!genderInput.value) genderInput.value = guest.Gender || "";
  if (!contactInput.value) contactInput.value = guest.Contact || "";
  if (!addressInput.value) addressInput.value = guest.Address || "";

  if (!idTypeInput.value) idTypeInput.value = guest.ID_type || "";
  if (!idNoInput.value) idNoInput.value = guest.ID_no || "";

  ageInput.value =
    guest.Age != null ? String(guest.Age) : computeAge(dobInput.value.trim());

  idCategoryInput.value = rec.ID_category || "Unknown";
  secondaryCountInput.value = rec.Secondary_count ?? 0;
  canProceedInput.value = rec.Can_proceed ? "YES" : "NO";

  updateCanProceedUI();
}

// =====================
// RESET (UI + server record optional)
// =====================
async function resetAll({ resetServer = false } = {}) {
  const ref = currentRefId || refIdInput.value;

  if (resetServer && ref) {
    try {
      await fetchJSON("/reset-record", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference_id: ref }),
      });
    } catch {
      // ignore reset errors
    }
  }

  currentRefId = "";

  // ✅ clear retry state
  lastScanSource = null;
  lastScanFile = null;
  lastScanSlot = "primary";
  lastPredictedType = "";
  setRetryEnabled(false);

  refIdInput.value = "";
  ageInput.value = "";

  idCategoryInput.value = "";
  secondaryCountInput.value = "";
  canProceedInput.value = "";

  idTypeInput.value = "";
  idNoInput.value = "";

  firstNameInput.value = "";
  middleNameInput.value = "";
  lastNameInput.value = "";
  dobInput.value = "";
  genderInput.value = "";
  contactInput.value = "";
  addressInput.value = "";

  exportBtn.disabled = true;
  addSecondaryBtn.style.display = "none";

  setUploadMode(false);
}

// =====================
// EVENTS
// =====================
imgView.addEventListener("click", () => {
  if (!cameraActive) inputFile.click();
});

toggleBtn.addEventListener("click", async () => {
  if (!cameraActive) await setCameraMode();
  else setUploadMode(false);
});

addSecondaryBtn.addEventListener("click", () => {
  idSlotSelect.value = "secondary";
  inputFile.click();
});

// ✅ Retry (now actually retries upload OR camera)
retryBtn.addEventListener("click", async () => {
  try {
    if (!lastScanFile) {
      alert("Nothing to retry yet.");
      return;
    }

    showLoading("Retrying scan...");

    const rec = await scanFileToServer(lastScanFile, {
      slot: lastScanSlot,
      predictedType: lastPredictedType,
    });

    applyRecordToUI(rec);
    setUploadMode(true);
    setRetryEnabled(true);
  } catch (err) {
    alert("Retry failed: " + err.message);
  } finally {
    hideLoading();
  }
});

// Upload scan
inputFile.addEventListener("change", async () => {
  const file = inputFile.files[0];
  if (!file) return;

  const imgSrc = URL.createObjectURL(file);

  try {
    showLoading("Loading model...");

    let predictedType = "";
    if (file.type.startsWith("image/")) {
      const img = new Image();
      img.src = imgSrc;
      await new Promise((r) => (img.onload = r));
      predictedType = await predictIDType(img);
    }

    showLoading("Scanning...");

    const rec = await scanFileToServer(file, {
      slot: idSlotSelect.value,
      predictedType,
    });

    setPreviewSrc(imgSrc, true);
    if (predictedType && !idTypeInput.value) idTypeInput.value = predictedType;

    applyRecordToUI(rec);
    setUploadMode(true);

    // ✅ Save for retry
    lastScanSource = "upload";
    lastScanFile = file;
    lastScanSlot = idSlotSelect.value;
    lastPredictedType = predictedType || "";
    setRetryEnabled(true);
  } catch (err) {
    try { URL.revokeObjectURL(imgSrc); } catch {}
    alert("Error scanning upload: " + err.message);

    // ✅ Still allow retry with same file
    lastScanSource = "upload";
    lastScanFile = file;
    lastScanSlot = idSlotSelect.value;
    lastPredictedType = "";
    setRetryEnabled(true);
  } finally {
    hideLoading();
    inputFile.value = "";
  }
});

// Snap camera
snapBtn.addEventListener("click", async (e) => {
  e.stopPropagation();

  if (!camera.videoWidth || !camera.videoHeight) {
    alert("Camera not ready yet");
    return;
  }

  // ✅ reduce size for speed
  const targetW = 1280;
  const scale = targetW / camera.videoWidth;
  const w = targetW;
  const h = Math.round(camera.videoHeight * scale);

  const cvs = document.createElement("canvas");
  cvs.width = w;
  cvs.height = h;
  cvs.getContext("2d").drawImage(camera, 0, 0, w, h);

  try {
    showLoading("Loading model...");

    const blob = await new Promise((resolve) =>
      cvs.toBlob(resolve, "image/jpeg", 0.82)
    );

    if (!blob) throw new Error("Failed to capture image.");

    const file = new File([blob], "camera.jpg", { type: "image/jpeg" });

    // optional: predicted type via teachable machine
    let predictedType = "";
    try {
      const tmpUrl = URL.createObjectURL(blob);
      const img = new Image();
      img.src = tmpUrl;
      await new Promise((r) => (img.onload = r));
      predictedType = await predictIDType(img);
      URL.revokeObjectURL(tmpUrl);
    } catch {}

    showLoading("Scanning...");

    const rec = await scanFileToServer(file, {
      slot: idSlotSelect.value,
      predictedType,
    });

    setPreviewSrc(URL.createObjectURL(blob), true);
    if (predictedType && !idTypeInput.value) idTypeInput.value = predictedType;

    applyRecordToUI(rec);
    setUploadMode(true);

    // ✅ Save for retry (camera too)
    lastScanSource = "camera";
    lastScanFile = file;
    lastScanSlot = idSlotSelect.value;
    lastPredictedType = predictedType || "";
    setRetryEnabled(true);
  } catch (err) {
    alert("Error scanning camera: " + err.message);
  } finally {
    hideLoading();
  }
});

dobInput.addEventListener("input", refreshAgePreview);
recalcBtn.addEventListener("click", refreshAgePreview);

exportBtn.addEventListener("click", async () => {
  refreshAgePreview();

  const payload = {
    reference_id: currentRefId || refIdInput.value,
    ID_type: idTypeInput.value.trim(),
    ID_no: idNoInput.value.trim(),
    First_name: firstNameInput.value.trim(),
    Middle_name: middleNameInput.value.trim(),
    Last_name: lastNameInput.value.trim(),
    Date_of_birth: dobInput.value.trim(),
    Gender: genderInput.value.trim(),
    Contact: contactInput.value.trim(),
    Address: addressInput.value.trim(),
  };

  try {

    // ✅ 1 SAVE TO DATABASE
    const saveRes = await fetch("/save-guest", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    const saveData = await saveRes.json();

    if (!saveRes.ok) {
      alert("Database save failed: " + (saveData.error || ""));
      return;
    }

    // ✅ 2 GENERATE PDF
    const pdfRes = await fetch("/export-pdf", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!pdfRes.ok) {
      alert("Export failed");
      return;
    }

    const blob = await pdfRes.blob();
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = `${payload.reference_id}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();

    URL.revokeObjectURL(url);

    alert("Guest saved to database successfully!");

  } catch (err) {
    alert("Error: " + err.message);
  }
});

// New guest (also resets server record)
newGuestBtn.addEventListener("click", async () => {
  await resetAll({ resetServer: true });
});

// ✅ Reset fields when user returns via browser back (BFCache)
window.addEventListener("pageshow", async (e) => {
  if (e.persisted) {
    await resetAll({ resetServer: true });
  }
});

// init
setUploadMode(false);
exportBtn.disabled = true;
addSecondaryBtn.style.display = "none";
setRetryEnabled(false);

// ✅ Safety: ensure buttons don’t submit forms
try { retryBtn.type = "button"; } catch {}
try { toggleBtn.type = "button"; } catch {}
try { snapBtn.type = "button"; } catch {}
try { recalcBtn.type = "button"; } catch {}
try { exportBtn.type = "button"; } catch {}
try { newGuestBtn.type = "button"; } catch {}
try { addSecondaryBtn.type = "button"; } catch {}
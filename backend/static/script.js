// static/script.js (FULL) - shows DB error details + sends reference_id correctly
const imgView = document.getElementById("img-view");
const toggleBtn = document.getElementById("toggle-btn");
const recalcBtn = document.getElementById("recalc-btn");
const exportBtn = document.getElementById("export-btn");
const newGuestBtn = document.getElementById("new-guest-btn");
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
const canProceedInput = document.getElementById("can-proceed");

const idTypeInput = document.getElementById("id-type");
const idNoInput = document.getElementById("id-no");
const backHomeBtn = document.getElementById("back-home-btn");

const firstNameInput = document.getElementById("first-name");
const middleNameInput = document.getElementById("middle-name");
const lastNameInput = document.getElementById("last-name");
const dobInput = document.getElementById("dob");

const genderSelect = document.getElementById("gender_id");
const contactInput = document.getElementById("contact");
const addressInput = document.getElementById("address");

// STATE
let cameraActive = false;
let stream = null;
let currentRefId = "";
const PLACEHOLDER_SRC = previewImg.src;
let currentObjectUrl = null;

let lastScanFile = null;
let lastScanSlot = "primary";
let lastPredictedType = "";

// TM
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
      (data.error || "Request failed") + (data.details ? " | " + data.details : "")
    );
  }
  return data;
}

function computeAge(dobStr) {
  // accept YYYY-MM-DD only (backend will normalize if DD/MM/YYYY)
  if (!dobStr) return "";
  const s = dobStr.trim();

  if (/^\d{2}\/\d{2}\/\d{4}$/.test(s)) {
    const [dd, mm, yyyy] = s.split("/");
    dobStr = `${yyyy}-${mm}-${dd}`;
  }

  if (!/^\d{4}-\d{2}-\d{2}$/.test(dobStr)) return "";
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

function getSlotValue() {
  const v = (idSlotSelect && idSlotSelect.value) ? String(idSlotSelect.value) : "primary";
  return v || "primary";
}

async function scanFileToServer(file, { slot, predictedType } = {}) {
  const formData = new FormData();
  formData.append("file", file);

  if (currentRefId) formData.append("reference_id", currentRefId);

  formData.append("slot", slot || getSlotValue());
  if (predictedType) formData.append("predicted_id_type", predictedType);

  return await fetchJSON("/upload", { method: "POST", body: formData });
}

// load genders
async function loadGenders() {
  try {
    const rows = await fetchJSON("/meta/genders", { method: "GET" });

    genderSelect.innerHTML = "";
    const opt0 = document.createElement("option");
    opt0.value = "";
    opt0.textContent = "Select Gender";
    genderSelect.appendChild(opt0);

    for (const r of rows) {
      const opt = document.createElement("option");
      opt.value = String(r.Gender_id);
      opt.textContent = r.gender_name;
      genderSelect.appendChild(opt);
    }
  } catch (e) {
    console.error("Failed to load genders:", e);
    genderSelect.innerHTML = `<option value="">Select Gender</option>`;
  }
}

function setGenderByNameIfPossible(nameOrLetter) {
  const s = String(nameOrLetter || "").trim().toLowerCase();
  if (!s) return;
  let target = "";
  if (s === "m" || s === "male") target = "male";
  else if (s === "f" || s === "female") target = "female";
  else return;

  const opts = [...genderSelect.options];
  const found = opts.find(o => (o.textContent || "").trim().toLowerCase() === target);
  if (found) genderSelect.value = found.value;
}

function normalizeContactUI() {
  const digits = (contactInput.value || "").replace(/\D/g, "").slice(0, 11);
  contactInput.value = digits;
}
contactInput.addEventListener("input", normalizeContactUI);

// modes
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

// apply record
function applyRecordToUI(rec, { forceAll = false } = {}) {
  if (!rec) return;

  currentRefId = rec.Reference_id || rec.Reference_code || rec.reference_id || currentRefId;
  refIdInput.value = currentRefId || "";

  const guest = rec.Guest || {};

  if (forceAll || !firstNameInput.value) firstNameInput.value = guest.First_name || "";
  if (forceAll || !middleNameInput.value) middleNameInput.value = guest.Middle_name || "";
  if (forceAll || !lastNameInput.value) lastNameInput.value = guest.Last_name || "";
  if (forceAll || !dobInput.value) dobInput.value = guest.Date_of_birth || "";

  if (forceAll) genderSelect.value = "";
  if (forceAll || !genderSelect.value) setGenderByNameIfPossible(guest.Gender || "");

  if (forceAll || !contactInput.value) contactInput.value = guest.Contact || "";
  normalizeContactUI();

  if (forceAll || !addressInput.value) addressInput.value = guest.Address || "";

  if (forceAll || !idTypeInput.value) idTypeInput.value = guest.ID_type || "";
  if (forceAll || !idNoInput.value) idNoInput.value = guest.ID_no || "";

  ageInput.value = guest.Age != null ? String(guest.Age) : computeAge(dobInput.value.trim());

  idCategoryInput.value = rec.ID_category || "PRIMARY";
  canProceedInput.value = rec.Can_proceed ? "YES" : "NO";

  updateCanProceedUI();
}

// reset
async function resetAll({ resetServer = false } = {}) {
  const ref = currentRefId || refIdInput.value;

  if (resetServer && ref) {
    try {
      await fetchJSON("/reset-record", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reference_id: ref }),
      });
    } catch {}
  }

  currentRefId = "";
  lastScanFile = null;
  lastScanSlot = "primary";
  lastPredictedType = "";
  setRetryEnabled(false);

  refIdInput.value = "";
  ageInput.value = "";

  idCategoryInput.value = "PRIMARY";
  canProceedInput.value = "";

  idTypeInput.value = "";
  idNoInput.value = "";

  firstNameInput.value = "";
  middleNameInput.value = "";
  lastNameInput.value = "";
  dobInput.value = "";
  genderSelect.value = "";
  contactInput.value = "";
  addressInput.value = "";

  exportBtn.disabled = true;
  setUploadMode(false);
}

// events
imgView.addEventListener("click", () => {
  if (!cameraActive) inputFile.click();
});

toggleBtn.addEventListener("click", async () => {
  if (!cameraActive) await setCameraMode();
  else setUploadMode(false);
});

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
    applyRecordToUI(rec, { forceAll: true });
    setUploadMode(true);
    setRetryEnabled(true);
  } catch (err) {
    alert("Retry failed: " + err.message);
  } finally {
    hideLoading();
  }
});

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
    const rec = await scanFileToServer(file, { slot: getSlotValue(), predictedType });

    setPreviewSrc(imgSrc, true);
    applyRecordToUI(rec, { forceAll: true });

    if (predictedType && !idTypeInput.value) idTypeInput.value = predictedType;

    setUploadMode(true);

    lastScanFile = file;
    lastScanSlot = getSlotValue();
    lastPredictedType = predictedType || "";
    setRetryEnabled(true);
  } catch (err) {
    try { URL.revokeObjectURL(imgSrc); } catch {}
    alert("Error scanning upload: " + err.message);

    lastScanFile = file;
    lastScanSlot = getSlotValue();
    lastPredictedType = "";
    setRetryEnabled(true);
  } finally {
    hideLoading();
    inputFile.value = "";
  }
});

snapBtn.addEventListener("click", async (e) => {
  e.stopPropagation();

  if (!camera.videoWidth || !camera.videoHeight) {
    alert("Camera not ready yet");
    return;
  }

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
    const blob = await new Promise((resolve) => cvs.toBlob(resolve, "image/jpeg", 0.82));
    if (!blob) throw new Error("Failed to capture image.");

    const file = new File([blob], "camera.jpg", { type: "image/jpeg" });

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
    const rec = await scanFileToServer(file, { slot: getSlotValue(), predictedType });

    setPreviewSrc(URL.createObjectURL(blob), true);
    if (predictedType && !idTypeInput.value) idTypeInput.value = predictedType;

    applyRecordToUI(rec, { forceAll: true });
    setUploadMode(true);

    lastScanFile = file;
    lastScanSlot = getSlotValue();
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
  normalizeContactUI();

  if (contactInput.value && contactInput.value.length !== 11) {
    alert("Contact must be exactly 11 digits.");
    return;
  }

  if (!genderSelect.value) {
    alert("Please select Gender.");
    return;
  }

  const payload = {
    reference_id: currentRefId || refIdInput.value,
    ID_type: idTypeInput.value.trim(),
    ID_no: idNoInput.value.trim(),
    First_name: firstNameInput.value.trim(),
    Middle_name: middleNameInput.value.trim(),
    Last_name: lastNameInput.value.trim(),
    Date_of_birth: dobInput.value.trim(),
    Gender_id: Number(genderSelect.value),
    Contact: contactInput.value.trim(),
    Address: addressInput.value.trim(),
  };

  try {
    const saveRes = await fetch("/save-guest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const saveText = await saveRes.text();
    let saveData = {};
    try { saveData = JSON.parse(saveText); } catch {}

    if (!saveRes.ok) {
      alert("Database save failed: " + (saveData.error || "DB save failed") + (saveData.details ? (" | " + saveData.details) : ""));
      return;
    }

    const pdfRes = await fetch("/export-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!pdfRes.ok) {
      const errText = await pdfRes.text().catch(() => "");
      alert("Export failed. " + errText);
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

newGuestBtn.addEventListener("click", async () => {
  await resetAll({ resetServer: true });
});

window.addEventListener("pageshow", async (e) => {
  if (e.persisted) {
    await resetAll({ resetServer: true });
  }
});

// init
setUploadMode(false);
exportBtn.disabled = true;
setRetryEnabled(false);
loadGenders();
const toggleBtn = document.getElementById("toggle-btn");
const recalcBtn = document.getElementById("recalc-btn");
const exportBtn = document.getElementById("export-btn");

const inputFile = document.getElementById("input-file");
const previewImg = document.getElementById("preview-img");
const camera = document.getElementById("camera");
const snapBtn = document.getElementById("snap-btn");
const dropText = document.getElementById("drop-text");
const loading = document.getElementById("loading-indicator");

const refIdInput = document.getElementById("ref-id");
const ageInput = document.getElementById("age");
const minorStatusInput = document.getElementById("minor-status");

const idTypeInput = document.getElementById("id-type");
const firstNameInput = document.getElementById("first-name");
const middleNameInput = document.getElementById("middle-name");
const lastNameInput = document.getElementById("last-name");
const dobInput = document.getElementById("dob");
const genderInput = document.getElementById("gender");
const contactInput = document.getElementById("contact");
const addressInput = document.getElementById("address");

let cameraActive = false;
let stream = null;
let currentImgPath = "";

// -----------------------------
// Teachable Machine (lazy load)
// -----------------------------
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
  const model = await ensureTM(); // loads only first time
  const prediction = await model.predict(imgEl);
  const best = prediction.reduce((a, b) =>
    a.probability > b.probability ? a : b
  );
  return best.className;
}

// -----------------------------
// Helpers
// -----------------------------
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

function computeAgeAndMinor(dobStr) {
  if (!dobStr || !/^\d{4}-\d{2}-\d{2}$/.test(dobStr)) {
    return { age: "", status: "UNKNOWN" };
  }

  const [y, m, d] = dobStr.split("-").map(Number);
  const dob = new Date(y, m - 1, d);
  if (isNaN(dob.getTime())) return { age: "", status: "UNKNOWN" };

  const today = new Date();
  let age = today.getFullYear() - dob.getFullYear();

  const hasHadBirthday =
    today.getMonth() > dob.getMonth() ||
    (today.getMonth() === dob.getMonth() && today.getDate() >= dob.getDate());

  if (!hasHadBirthday) age--;

  if (age < 0 || age > 130) return { age: "", status: "UNKNOWN" };

  return { age: String(age), status: age < 18 ? "MINOR" : "ADULT" };
}

function refreshMinorPreview() {
  const { age, status } = computeAgeAndMinor(dobInput.value.trim());
  ageInput.value = age;
  minorStatusInput.value = status;
}

function generateReferenceId() {
  const now = new Date();

  const y = now.getFullYear();
  const m = String(now.getMonth() + 1).padStart(2, "0");
  const d = String(now.getDate()).padStart(2, "0");

  const hh = String(now.getHours()).padStart(2, "0");
  const mm = String(now.getMinutes()).padStart(2, "0");
  const ss = String(now.getSeconds()).padStart(2, "0");

  return `REF-${y}${m}${d}-${hh}${mm}${ss}`;
}

// Set once on page load
refIdInput.value = generateReferenceId();

function updateFields(data, imgSrc) {
  refIdInput.value = generateReferenceId();
  currentImgPath = data.Img_path || currentImgPath;

  idTypeInput.value = data.ID_type || "";
  firstNameInput.value = data.First_name || "";
  middleNameInput.value = data.Middle_name || "";
  lastNameInput.value = data.Last_name || "";
  dobInput.value = data.Date_of_birth || ""; // editable
  genderInput.value = data.Gender || "";
  contactInput.value = data.Contact || "";
  addressInput.value = data.Address || "";

  refreshMinorPreview();

  if (imgSrc) {
    previewImg.src = imgSrc;
    previewImg.style.display = "block";
  }

  camera.style.display = "none";
  snapBtn.style.display = "none";
  dropText.textContent = "Upload your ID here";
  toggleBtn.textContent = "Use Camera";
  stopCamera();
  cameraActive = false;
}

// -----------------------------
// Click upload area to upload
// -----------------------------
document.getElementById("img-view").addEventListener("click", () => {
  if (!cameraActive) inputFile.click();
});

// -----------------------------
// Upload -> /upload (FIXED)
// -----------------------------
inputFile.addEventListener("change", async () => {
  const file = inputFile.files[0];
  if (!file) return;

  loading.style.display = "flex";
  loading.textContent = "Scanning...";

  const imgSrc = URL.createObjectURL(file);

  try {
    // Predict ID type ONLY if it's an image
    let predictedType = "";
    if (file.type.startsWith("image/")) {
      loading.textContent = "Loading model...";
      const img = new Image();
      img.src = imgSrc;
      await new Promise((r) => (img.onload = r));
      predictedType = await predictIDType(img);
      loading.textContent = "Scanning...";
    }

    const formData = new FormData();
    formData.append("file", file);

    const data = await fetchJSON("/upload", { method: "POST", body: formData });

    // Inject predicted ID type
    if (predictedType) data.ID_type = predictedType;

    updateFields(data, imgSrc);
  } catch (err) {
    alert("Error scanning upload: " + err.message);
  } finally {
    loading.style.display = "none";
    loading.textContent = "Scanning...";
  }
});

// -----------------------------
// Toggle camera
// -----------------------------
toggleBtn.addEventListener("click", () => {
  cameraActive = !cameraActive;

  if (cameraActive) {
    previewImg.style.display = "none";
    camera.style.display = "block";
    snapBtn.style.display = "block";
    dropText.textContent = "Point your ID to the camera";
    toggleBtn.textContent = "Use Upload";
    startCamera();
  } else {
    previewImg.style.display = "block";
    camera.style.display = "none";
    snapBtn.style.display = "none";
    dropText.textContent = "Upload your ID here";
    toggleBtn.textContent = "Use Camera";
    stopCamera();
  }
});

function startCamera() {
  navigator.mediaDevices
    .getUserMedia({ video: true })
    .then((s) => {
      stream = s;
      camera.srcObject = s;
      camera.play();
    })
    .catch((err) => alert("Camera error: " + err));
}

function stopCamera() {
  if (stream) {
    stream.getTracks().forEach((t) => t.stop());
    camera.srcObject = null;
    stream = null;
  }
}

// -----------------------------
// Snap -> /scan (with TM prediction)
// -----------------------------
snapBtn.addEventListener("click", async (e) => {
  e.stopPropagation();

  if (!camera.videoWidth || !camera.videoHeight) {
    alert("Camera not ready yet");
    return;
  }

  loading.style.display = "flex";
  loading.textContent = "Scanning...";

  const cvs = document.createElement("canvas");
  cvs.width = camera.videoWidth;
  cvs.height = camera.videoHeight;
  cvs.getContext("2d").drawImage(camera, 0, 0, cvs.width, cvs.height);
  const dataURL = cvs.toDataURL("image/png");

  try {
    // TM prediction
    loading.textContent = "Loading model...";
    const img = new Image();
    img.src = dataURL;
    await new Promise((r) => (img.onload = r));
    const predictedType = await predictIDType(img);

    loading.textContent = "Scanning...";

    const data = await fetchJSON("/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: dataURL }),
    });

    data.ID_type = predictedType;
    updateFields(data, dataURL);
  } catch (err) {
    alert("Error scanning camera: " + err.message);
  } finally {
    loading.style.display = "none";
    loading.textContent = "Scanning...";
  }
});

// -----------------------------
// live preview when DOB edited
// -----------------------------
dobInput.addEventListener("input", refreshMinorPreview);
recalcBtn.addEventListener("click", refreshMinorPreview);

// -----------------------------
// Export PDF using current fields
// -----------------------------
exportBtn.addEventListener("click", async () => {
  refreshMinorPreview();

  const payload = {
    Reference_id: refIdInput.value,
    ID_type: idTypeInput.value,
    First_name: firstNameInput.value,
    Middle_name: middleNameInput.value,
    Last_name: lastNameInput.value,
    Date_of_birth: dobInput.value,
    Gender: genderInput.value,
    Contact: contactInput.value,
    Address: addressInput.value,
    Img_path: currentImgPath
  };

  try {
    const res = await fetch("/export-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      const txt = await res.text();
      alert("Export failed: " + txt);
      return;
    }

    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${payload.Reference_id || "guest"}.pdf`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (e) {
    alert("Export error: " + e.message);
  }
});

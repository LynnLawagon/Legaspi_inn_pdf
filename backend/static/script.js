const toggleBtn = document.getElementById("toggle-btn");
const inputFile = document.getElementById("input-file");
const previewImg = document.getElementById("preview-img");
const camera = document.getElementById("camera");
const snapBtn = document.getElementById("snap-btn");
const dropText = document.getElementById("drop-text");
const loading = document.getElementById("loading-indicator");

const idTypeInput = document.getElementById("id-type");
const firstNameInput = document.getElementById("first-name");
const middleNameInput = document.getElementById("middle-name");
const lastNameInput = document.getElementById("last-name");
const dobInput = document.getElementById("dob");
const genderInput = document.getElementById("gender");
const contactInput = document.getElementById("contact");
const addressInput = document.getElementById("address");
const exportBtn = document.getElementById("export-btn");

let cameraActive = false, stream = null, currentImgPath = "";
let tmModel;

// TM model (optional)
async function loadTMModel() {
  try {
    const URL = "/static/my_model/";
    tmModel = await tmImage.load(URL + "model.json", URL + "metadata.json");
  } catch (e) {
    console.warn("TM model not loaded (ok if you don’t need it):", e);
  }
}
loadTMModel();

async function predictIDType(img) {
  if (!tmModel) return "";
  const prediction = await tmModel.predict(img);
  return prediction.reduce((a, b) => (a.probability > b.probability ? a : b), { probability: 0 }).className;
}

// robust fetchJSON
async function fetchJSON(url, options) {
  const res = await fetch(url, options);
  const text = await res.text();

  let data;
  try {
    data = JSON.parse(text);
  } catch {
    console.error("Server returned non-JSON:", text);
    throw new Error("Server returned HTML/non-JSON. Check Flask console.");
  }

  if (!res.ok) {
    throw new Error((data.error || "Request failed") + (data.details ? " | " + data.details : ""));
  }

  return data;
}

function updateFields(data, imgSrc) {
  firstNameInput.value = data.First_name || "";
  middleNameInput.value = data.Middle_name || "";
  lastNameInput.value = data.Last_name || "";
  dobInput.value = data.Date_of_birth || "";
  genderInput.value = data.Gender || "";
  contactInput.value = data.Contact || "";
  addressInput.value = data.Address || "";
  idTypeInput.value = data.ID_type || "";
  currentImgPath = data.Img_path || "";

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

// upload
inputFile.addEventListener("change", async () => {
  const file = inputFile.files[0];
  if (!file) return;

  loading.style.display = "flex";

  try {
    const isImage = file.type.startsWith("image/");
    const imgSrc = isImage ? URL.createObjectURL(file) : "";

    let idType = "";
    if (isImage) {
      const img = new Image();
      img.src = imgSrc;
      await new Promise((r) => (img.onload = r));
      idType = await predictIDType(img);
    }

    const formData = new FormData();
    formData.append("file", file);

    const data = await fetchJSON("/upload", { method: "POST", body: formData });
    data.ID_type = idType;

    updateFields(data, imgSrc);
  } catch (err) {
    alert("Error scanning upload: " + err.message);
  } finally {
    loading.style.display = "none";
  }
});

// Toggle camera
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
  navigator.mediaDevices.getUserMedia({ video: true })
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

// camera snap
snapBtn.addEventListener("click", async () => {
  if (!camera.videoWidth || !camera.videoHeight) {
    alert("Camera not ready yet");
    return;
  }

  loading.style.display = "flex";

  const canv = document.createElement("canvas");
  canv.width = camera.videoWidth;
  canv.height = camera.videoHeight;
  canv.getContext("2d").drawImage(camera, 0, 0, canv.width, canv.height);
  const dataURL = canv.toDataURL("image/png");

  try {
    const img = new Image();
    img.src = dataURL;
    await new Promise((r) => (img.onload = r));
    const idType = await predictIDType(img);

    const data = await fetchJSON("/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: dataURL }),
    });

    data.ID_type = idType;
    updateFields(data, dataURL);
  } catch (err) {
    alert("Error scanning snap: " + err.message);
  } finally {
    loading.style.display = "none";
  }
});

// ✅ Export PDF from current form (POST)
exportBtn.addEventListener("click", async () => {
  const payload = {
    ID_type: idTypeInput.value,
    First_name: firstNameInput.value,
    Middle_name: middleNameInput.value,
    Last_name: lastNameInput.value,
    Date_of_birth: dobInput.value,
    Gender: genderInput.value,
    Contact: contactInput.value,
    Address: addressInput.value,
    Img_path: currentImgPath,
  };

  try {
    const res = await fetch("/export-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const text = await res.text();
      alert("Export failed: " + text);
      return;
    }

    // download pdf
    const blob = await res.blob();
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "guest.pdf";
    document.body.appendChild(a);
    a.click();
    a.remove();
    window.URL.revokeObjectURL(url);
  } catch (e) {
    alert("Export error: " + e.message);
  }
});

// click preview area to upload
document.getElementById("img-view").addEventListener("click", () => {
  if (!cameraActive) inputFile.click();
});

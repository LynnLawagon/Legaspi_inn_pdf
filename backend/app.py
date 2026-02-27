from flask import Flask, request, jsonify, render_template, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException

import os
import re
import cv2
import base64
import numpy as np
import traceback
import random
from datetime import datetime, date

import easyocr

app = Flask(__name__, template_folder="templates")

# =========================
# COMPANY INFO (UPDATED)
# =========================
COMPANY_ADDRESS = "115 Pelayo St, Poblacion District, Davao City, 8000 Davao del Sur"
COMPANY_NUMBER = "936 456 8920"

# Place your logo here:
# backend/static/img/logo.png
LOGO_REL_PATH = "img/logo.png"

@app.route("/")
def home():
    return render_template("landing.html")

@app.route("/scan-page")
def scan_page():
    return render_template("index.html")

PDF_FOLDER = os.path.join(app.root_path, "static", "PDFs")
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

reader = None
RECORDS = {}

# =====================
# HELPERS
# =====================
def get_reader():
    global reader
    if reader is None:
        reader = easyocr.Reader(["en"], gpu=False)
    return reader


def json_error(msg, code=400, **extra):
    payload = {"error": msg}
    payload.update(extra)
    return jsonify(payload), code


def safe_resize(img, max_w=1600):
    h, w = img.shape[:2]
    if w > max_w:
        scale = max_w / w
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return img


def generate_reference_id():
    return f"REF-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{random.randint(1000, 9999)}"


def compute_age(dob_str: str):
    if not dob_str:
        return None
    try:
        dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    except Exception:
        return None

    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 0 or age > 130:
        return None
    return age


PRIMARY_KEYWORDS = [
    "PHILIPPINE IDENTIFICATION", "PHILIPPINE IDENTIFICATION CARD",
    "PHILSYS", "NATIONAL ID",
    "SCHOOL", "STUDENT", "COLLEGE", "UNIVERSITY",
]
SECONDARY_KEYWORDS = [
    "BIRTH CERTIFICATE", "PSA",
    "BARANGAY", "CLEARANCE",
]

def guess_id_category(full_text_upper: str) -> str:
    t = (full_text_upper or "").upper()
    if any(k in t for k in PRIMARY_KEYWORDS):
        return "Primary"
    if any(k in t for k in SECONDARY_KEYWORDS):
        return "Secondary"
    return "Unknown"


def can_proceed(record: dict) -> bool:
    cat = (record.get("ID_category") or "Unknown").strip()
    if cat == "Primary":
        return True
    if cat == "Secondary":
        return len(record.get("Secondary_ids", [])) >= 2
    return False


def pdf_first_page_to_bgr(pdf_path):
    try:
        import fitz
    except Exception:
        return None, "PyMuPDF not installed. Install: pip install pymupdf"

    try:
        doc = fitz.open(pdf_path)
        if doc.page_count == 0:
            return None, "PDF has no pages"
        page = doc.load_page(0)
        pix = page.get_pixmap(dpi=200)
        img_bytes = pix.tobytes("png")
        data = np.frombuffer(img_bytes, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img, None
    except Exception as e:
        return None, f"PDF render failed: {e}"


def order_pts(pts):
    pts = np.array(pts, dtype="float32")
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1)
    tl = pts[np.argmin(s)]
    br = pts[np.argmax(s)]
    tr = pts[np.argmin(diff)]
    bl = pts[np.argmax(diff)]
    return np.array([tl, tr, br, bl], dtype="float32")


def find_contours_compat(bin_img):
    out = cv2.findContours(bin_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if len(out) == 2:
        cnts, _ = out
    else:
        _, cnts, _ = out
    return cnts


def warp_card(img_bgr):
    img = safe_resize(img_bgr, max_w=2000)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    edges = cv2.Canny(gray, 50, 150)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=2)

    cnts = find_contours_compat(edges)
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)[:15]

    best = None
    best_area = 0
    H, W = img.shape[:2]

    for c in cnts:
        area = cv2.contourArea(c)
        if area < 0.08 * (H * W):
            continue

        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) != 4:
            continue

        x, y, w, h = cv2.boundingRect(approx)
        ar = w / float(h)
        if ar < 1.2 or ar > 2.3:
            continue

        if area > best_area:
            best_area = area
            best = approx.reshape(4, 2)

    if best is None:
        return img_bgr

    pts = order_pts(best)
    (tl, tr, br, bl) = pts
    widthA = np.linalg.norm(br - bl)
    widthB = np.linalg.norm(tr - tl)
    maxW = int(max(widthA, widthB))

    heightA = np.linalg.norm(tr - br)
    heightB = np.linalg.norm(tl - bl)
    maxH = int(max(heightA, heightB))

    dst = np.array([[0, 0], [maxW - 1, 0], [maxW - 1, maxH - 1], [0, maxH - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(pts, dst)
    warped = cv2.warpPerspective(img, M, (maxW, maxH))
    return warped


def preprocess_for_ocr(img_bgr):
    img_bgr = safe_resize(img_bgr, max_w=1600)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    gray = cv2.fastNlMeansDenoising(gray, None, 18, 7, 21)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    thr = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35, 11
    )
    return gray, thr


def ocr_text(img, min_conf=0.35):
    r = get_reader()
    results = r.readtext(img, detail=0, paragraph=False)
    up = []
    for t in results:
        t = (t or "").strip()
        if t:
            up.append(t.upper())
    return " ".join(up)


def extract_id_number(full_text_upper: str):
    t = full_text_upper or ""

    m = re.search(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b", t)
    if m:
        return m.group(0)

    m2 = re.search(r"\b\d{16}\b", t)
    if m2:
        return m2.group(0)

    m3 = re.search(r"ID\s*NO\.?\s*([0-9]{4,})", t)
    if m3:
        return m3.group(1)

    return ""


def parse_fields_from_image(img_bgr):
    img = warp_card(img_bgr)
    gray, thr = preprocess_for_ocr(img)

    full_text = ocr_text(thr, min_conf=0.35)
    if len(full_text) < 10:
        full_text = ocr_text(gray, min_conf=0.35)

    dob_mysql = ""
    m1 = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2}\s+\d{4}",
        full_text
    )
    m2 = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", full_text)
    if m1:
        try:
            dob_mysql = datetime.strptime(m1.group(0), "%B %d %Y").strftime("%Y-%m-%d")
        except Exception:
            dob_mysql = ""
    elif m2:
        mm, dd, yyyy = m2.groups()
        try:
            dob_mysql = datetime.strptime(f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}", "%Y-%m-%d").strftime("%Y-%m-%d")
        except Exception:
            dob_mysql = ""

    address = ""
    maddr = re.search(r"(TIRAHAN|ADDRESS)\s+(.+)", full_text)
    if maddr:
        address = maddr.group(2).strip()

    gender = ""
    mg = re.search(r"(SEX|GENDER)\s+([A-Z])\b", full_text)
    if mg:
        gender = mg.group(2)

    id_no = extract_id_number(full_text)

    return {
        "First_name": "",
        "Middle_name": "",
        "Last_name": "",
        "Date_of_birth": dob_mysql,
        "Gender": gender,
        "Contact": "",
        "Address": address,
        "ID_no": id_no,
        "__full_text": full_text,
    }


@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    if request.path in ["/upload", "/scan", "/export-pdf"]:
        traceback.print_exc()
        return json_error("Server error", 500, details=str(e))
    raise e


def ensure_record(ref: str):
    if ref not in RECORDS:
        RECORDS[ref] = {
            "Reference_id": ref,
            "Created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ID_category": "Unknown",
            "Primary_id": None,
            "Secondary_ids": [],
            "Guest": {
                "ID_type": "",
                "ID_no": "",
                "First_name": "",
                "Middle_name": "",
                "Last_name": "",
                "Date_of_birth": "",
                "Age": None,
                "Gender": "",
                "Contact": "",
                "Address": "",
            },
            "Secondary_count": 0,
            "Can_proceed": False
        }
    return RECORDS[ref]


def merge_extracted_into_record(extracted: dict, ref: str, slot: str, predicted_id_type: str = ""):
    record = ensure_record(ref)

    guessed_category = guess_id_category(extracted.get("__full_text", ""))

    if slot == "primary":
        record["ID_category"] = "Primary"
    else:
        record["ID_category"] = "Secondary" if guessed_category != "Primary" else "Primary"

    g = record["Guest"]
    g["First_name"] = extracted.get("First_name", g["First_name"])
    g["Middle_name"] = extracted.get("Middle_name", g["Middle_name"])
    g["Last_name"] = extracted.get("Last_name", g["Last_name"])
    g["Date_of_birth"] = extracted.get("Date_of_birth", g["Date_of_birth"])
    g["Gender"] = extracted.get("Gender", g["Gender"])
    g["Address"] = extracted.get("Address", g["Address"])
    g["ID_no"] = extracted.get("ID_no", g["ID_no"])

    if predicted_id_type:
        g["ID_type"] = predicted_id_type

    g["Age"] = compute_age(g.get("Date_of_birth") or "")

    img_path = extracted.get("Img_path", "")
    if slot == "primary":
        record["Primary_id"] = {
            "Img_path": img_path,
            "Uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    else:
        if img_path and not any(x.get("Img_path") == img_path for x in record["Secondary_ids"]):
            record["Secondary_ids"].append({
                "Img_path": img_path,
                "Uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })

    record["Secondary_count"] = len(record["Secondary_ids"])
    record["Can_proceed"] = can_proceed(record)

    return record


def apply_manual_overrides(record: dict, payload: dict):
    g = record.get("Guest", {})

    def pick(key):
        v = payload.get(key)
        if v is None:
            return None
        return str(v).strip()

    for k in ["ID_type", "ID_no", "First_name", "Middle_name", "Last_name", "Date_of_birth", "Gender", "Contact", "Address"]:
        v = pick(k)
        if v is not None:
            g[k] = v

    g["Age"] = compute_age(g.get("Date_of_birth") or "")
    record["Guest"] = g
    record["Can_proceed"] = can_proceed(record)


@app.route("/upload", methods=["POST"])
def upload():
    try:
        if "file" not in request.files:
            return json_error("No file uploaded", 400)

        file = request.files["file"]
        if not file or file.filename == "":
            return json_error("No file selected", 400)

        ref = request.form.get("reference_id") or generate_reference_id()
        slot = request.form.get("slot", "secondary")
        predicted_id_type = request.form.get("predicted_id_type", "")

        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()
        if ext not in ALLOWED_EXT:
            return json_error("Invalid file type. Use JPG/PNG/WEBP or PDF.", 400)

        unique = f"upload_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        saved_path = os.path.join(UPLOAD_FOLDER, unique)
        file.save(saved_path)
        img_path_db = f"uploads/{unique}"

        if ext == ".pdf":
            img, err = pdf_first_page_to_bgr(saved_path)
            if err:
                return json_error("PDF error", 500, details=err)
            if img is None:
                return json_error("Could not read PDF", 400)
        else:
            img = cv2.imread(saved_path)
            if img is None:
                return json_error("Could not read the uploaded image", 400)

        extracted = parse_fields_from_image(img)
        extracted["Img_path"] = img_path_db

        record = merge_extracted_into_record(extracted, ref=ref, slot=slot, predicted_id_type=predicted_id_type)
        return jsonify(record), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning upload", 500, details=str(e))


@app.route("/scan", methods=["POST"])
def scan():
    try:
        data = request.get_json(silent=True) or {}
        if "image" not in data:
            return json_error("Missing image in request JSON", 400)

        ref = data.get("reference_id") or generate_reference_id()
        slot = data.get("slot", "secondary")
        predicted_id_type = data.get("predicted_id_type", "")

        data_url = data["image"]
        if "," not in data_url:
            return json_error("Invalid image data URL", 400)

        img_b64 = data_url.split(",", 1)[1]
        img_bytes = base64.b64decode(img_b64)
        img_array = np.frombuffer(img_bytes, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is None:
            return json_error("Could not decode camera image", 400)

        filename = f"scan_{datetime.now().strftime('%Y%m%d%H%M%S')}.jpg"
        saved_path = os.path.join(UPLOAD_FOLDER, filename)
        cv2.imwrite(saved_path, img)
        img_path_db = f"uploads/{filename}"

        extracted = parse_fields_from_image(img)
        extracted["Img_path"] = img_path_db

        record = merge_extracted_into_record(extracted, ref=ref, slot=slot, predicted_id_type=predicted_id_type)
        return jsonify(record), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning camera", 500, details=str(e))


def wrap_text_by_width(c, text, max_width, font_name="Helvetica", font_size=11):
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return [""]

    c.setFont(font_name, font_size)
    words = text.split()
    lines = []
    current = ""

    for w in words:
        test = (current + " " + w).strip()
        if c.stringWidth(test, font_name, font_size) <= max_width:
            current = test
        else:
            if current:
                lines.append(current)
                current = w
            else:
                lines.append(w)
                current = ""

    if current:
        lines.append(current)

    return lines


@app.route("/export-pdf", methods=["POST"])
def export_pdf():
    try:
        payload = request.get_json(silent=True) or {}
        ref = payload.get("Reference_id") or payload.get("reference_id")
        if not ref:
            return json_error("Missing Reference_id", 400)

        record = RECORDS.get(ref)
        if not record:
            return json_error("No record found for that Reference ID.", 404)

        apply_manual_overrides(record, payload)

        cat = (record.get("ID_category") or "Unknown").strip()
        if cat == "Unknown":
            return json_error("Cannot export: ID Category is Unknown.", 400)

        if not record.get("Can_proceed"):
            if cat == "Secondary":
                return json_error("Cannot export: Need 2 Secondary IDs to proceed.", 400)
            return json_error("Cannot export: Not allowed to proceed.", 400)

        guest = record.get("Guest") or {}
        sec_ids = record.get("Secondary_ids") or []

        filename = f"{ref}.pdf"
        pdf_path = os.path.join(PDF_FOLDER, filename)

        c = canvas.Canvas(pdf_path, pagesize=A4)
        w, h = A4
        margin = 45

        # ======================
        # HEADER
        # ======================
        header_top = h - margin
        logo_abs = os.path.join(app.root_path, "static", LOGO_REL_PATH)

        logo_w = 210
        logo_h = 50
        logo_y = header_top - logo_h
        if os.path.exists(logo_abs):
            try:
                c.drawImage(
                    ImageReader(logo_abs),
                    margin, logo_y,
                    width=logo_w, height=logo_h,
                    preserveAspectRatio=True, mask="auto"
                )
            except Exception:
                pass

        right_x = w - margin
        c.setFont("Helvetica", 10)
        c.drawRightString(right_x, header_top - 10, COMPANY_ADDRESS)
        c.drawRightString(right_x, header_top - 24, COMPANY_NUMBER)
        c.setFont("Helvetica-Bold", 10)
        c.drawRightString(right_x, header_top - 40, f"{ref}")
        c.setFont("Helvetica", 9)
        c.drawRightString(right_x, header_top - 54, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        c.setLineWidth(1)
        header_line_y = header_top - 70
        c.line(margin, header_line_y, w - margin, header_line_y)

        # ======================
        # BODY: FIXED 2 COLUMNS
        # ======================
        body_top = header_top - 105

        photo_w = 240
        photo_h = 140
        photo_x = w - margin - photo_w

        # ✅ FIX: move the photo box DOWN so it never enters company/header space
        photo_top_padding = 10
        photo_y = (header_line_y - photo_top_padding) - photo_h

        gutter = 18
        left_col_x = margin
        left_col_right = photo_x - gutter
        left_col_w = max(50, left_col_right - left_col_x)

        details_label_x = left_col_x
        details_value_x = left_col_x + 150
        value_max_w = max(50, left_col_right - details_value_x)

        c.setFont("Helvetica-Bold", 18)
        c.drawString(left_col_x, body_top, "GUEST DETAILS")

        y = body_top - 30

        def kv(label, value, max_lines=2):
            nonlocal y
            value = value if value else "N/A"

            c.setFont("Helvetica-Bold", 11)
            c.drawString(details_label_x, y, f"{label}:")

            lines = wrap_text_by_width(
                c, value, value_max_w,
                font_name="Helvetica", font_size=11
            )
            lines = lines[:max_lines] if max_lines else lines

            c.setFont("Helvetica", 11)
            vy = y
            for ln in lines:
                c.drawString(details_value_x, vy, ln)
                vy -= 14

            used = max(1, len(lines))
            y -= (18 + (used - 1) * 14)

        kv("ID Category", record.get("ID_category", "Unknown"), max_lines=1)
        kv("ID Type", guest.get("ID_type", ""), max_lines=2)
        kv("ID Number", guest.get("ID_no", ""), max_lines=2)
        kv("First name", guest.get("First_name", ""), max_lines=2)
        kv("Middle name", guest.get("Middle_name", ""), max_lines=2)
        kv("Last name", guest.get("Last_name", ""), max_lines=2)
        kv("Birthdate", guest.get("Date_of_birth", ""), max_lines=1)

        age = guest.get("Age")
        kv("Age", str(age) if age is not None else "", max_lines=1)
        kv("Gender", guest.get("Gender", ""), max_lines=1)
        kv("Contact", guest.get("Contact", ""), max_lines=2)

        c.setFont("Helvetica-Bold", 11)
        c.drawString(details_label_x, y, "Address:")
        addr_lines = wrap_text_by_width(
            c, guest.get("Address", "") or "N/A",
            value_max_w,
            font_name="Helvetica", font_size=11
        )

        c.setFont("Helvetica", 11)
        addr_y = y
        for ln in addr_lines[:3]:
            c.drawString(details_value_x, addr_y, ln)
            addr_y -= 14
        y = addr_y - 10

        # right image box
        c.rect(photo_x, photo_y, photo_w, photo_h)

        img_rel = ""
        if record.get("Primary_id") and record["Primary_id"].get("Img_path"):
            img_rel = record["Primary_id"]["Img_path"]
        elif sec_ids:
            img_rel = sec_ids[0].get("Img_path", "")

        img_rel = (img_rel or "").replace("\\", "/")
        img_abs = os.path.join(app.root_path, "static", img_rel)

        if img_rel and os.path.exists(img_abs):
            try:
                c.drawImage(
                    ImageReader(img_abs),
                    photo_x + 4, photo_y + 4,
                    width=photo_w - 8, height=photo_h - 8,
                    preserveAspectRatio=True, anchor="c"
                )
            except Exception:
                pass

        # footer
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(margin, 25, "Generated by AIntelli OCR Guest System")
        c.drawRightString(w - margin, 25, "Page 1")

        c.save()
        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        traceback.print_exc()
        return json_error("PDF export failed", 500, details=str(e))


if __name__ == "__main__":
    app.run(debug=True)
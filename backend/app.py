from flask import Flask, request, jsonify, render_template, send_file
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

import os, re, cv2, traceback, random
import numpy as np
from datetime import datetime, date
import easyocr

# -----------------------
# FLASK
# -----------------------
app = Flask(__name__, template_folder="templates")

# -----------------------
# COMPANY INFO
# -----------------------
COMPANY_ADDRESS = "115 Pelayo St, Poblacion District, Davao City, 8000 Davao del Sur"
COMPANY_NUMBER = "936 456 8920"
LOGO_REL_PATH = "img/logo.png"  # static/img/logo.png

PDF_FOLDER = os.path.join(app.root_path, "static", "PDFs")
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

# -----------------------
# OCR (cached)
# -----------------------
reader = None
def get_reader():
    global reader
    if reader is None:
        # gpu=False = stable for Windows
        reader = easyocr.Reader(["en"], gpu=False)
    return reader

# in-memory records
RECORDS = {}

# -----------------------
# ROUTES (PAGES)
# -----------------------
@app.route("/")
def home():
    return render_template("landing.html")

@app.route("/scan-page")
def scan_page():
    return render_template("index.html")

# -----------------------
# HELPERS
# -----------------------
def json_error(msg, code=400, **extra):
    payload = {"error": msg}
    payload.update(extra)
    return jsonify(payload), code

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
    return age if 0 <= age <= 130 else None

def safe_resize(img, target_w=1200):
    h, w = img.shape[:2]
    if w <= target_w:
        return img
    scale = target_w / float(w)
    return cv2.resize(img, (target_w, int(h * scale)))

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
    # PHILID words
    if "PAMBANSANG" in t or "PAGKAKAKILANLAN" in t:
        return "Primary"
    return "Unknown"

def can_proceed(record: dict) -> bool:
    cat = (record.get("ID_category") or "Unknown").strip()
    if cat == "Primary":
        return True
    if cat == "Secondary":
        return len(record.get("Secondary_ids", [])) >= 2
    return False

# -----------------------
# OCR + PARSING (BETTER)
# -----------------------
STOPWORDS = {
    "REPUBLIC", "PHILIPPINES", "PHILIPPINE", "IDENTIFICATION", "CARD", "NATIONAL", "ID",
    "PHILSYS", "DOB", "BIRTH", "DATE", "SEX", "GENDER", "ADDRESS", "TIRAHAN",
    "SIGNATURE", "ISSUED", "VALID", "BARANGAY", "CLEARANCE", "CERTIFICATE", "PSA",
    "PAMBANSANG", "PAGKAKAKILANLAN", "PILIPINAS",
    "APELYIDO", "GIVEN", "MIDDLE", "NAME", "PANGALAN", "KAPANGANAKAN", "KASARIAN",
}

def preprocess(img_bgr):
    # ✅ speed: resize
    img_bgr = safe_resize(img_bgr, target_w=1200)

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    # adaptive thresh helps labels
    thr = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31, 9
    )
    return gray, thr

def ocr_items(img):
    """
    returns list of:
      {text, conf, cx, cy, y1, y2}
    sorted top-to-bottom
    """
    r = get_reader()
    res = r.readtext(
        img,
        detail=1,
        paragraph=False,
        # ✅ speed / quality tweaks
        batch_size=8,
        text_threshold=0.6,
        low_text=0.35,
        link_threshold=0.35,
        mag_ratio=1.5
    )

    items = []
    full = []
    for (bbox, text, conf) in res:
        if not text:
            continue
        up = str(text).strip().upper()
        if not up:
            continue
        full.append(up)

        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cx = float(sum(xs)) / 4.0
        cy = float(sum(ys)) / 4.0
        y1, y2 = float(min(ys)), float(max(ys))
        items.append({
            "text": up,
            "conf": float(conf) if conf is not None else 0.0,
            "cx": cx, "cy": cy, "y1": y1, "y2": y2
        })

    items.sort(key=lambda x: x["cy"])
    return items, " ".join(full)

def clean_name(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"[^A-Z\s\-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    toks = [t for t in s.split() if t not in STOPWORDS]
    return " ".join(toks).strip()

def looks_like_name(s: str) -> bool:
    if not s:
        return False
    toks = s.split()
    if not (1 <= len(toks) <= 5):
        return False
    if any(len(t) < 2 for t in toks):
        return False
    # avoid header words
    if "PAMBANSANG" in s or "PAGKAKAKILANLAN" in s:
        return False
    return True

def get_value_near_label(items, label_variants, max_lines_ahead=8):
    """
    Find label line, then return best next line (below) that looks like a name/value.
    """
    texts = [x["text"] for x in items]
    for i, t in enumerate(texts):
        tt = t.upper()
        if any(v in tt for v in label_variants):
            for j in range(i + 1, min(i + 1 + max_lines_ahead, len(texts))):
                cand = clean_name(texts[j])
                if looks_like_name(cand):
                    return cand
    return ""

def extract_philid_names(items, full_text):
    # label-based first
    last = get_value_near_label(items, ["APELYIDO", "LAST NAME", "SURNAME"])
    first = get_value_near_label(items, ["MGA PANGALAN", "GIVEN NAMES", "GIVEN NAME", "FIRST NAME"])
    middle = get_value_near_label(items, ["GITNANG APELYIDO", "MIDDLE NAME"])

    # fallback: find 2-3 consecutive name lines under the header area
    if not (first and last):
        # choose top candidates (high conf) excluding stopwords
        cands = []
        for it in items:
            if it["conf"] < 0.45:
                continue
            cand = clean_name(it["text"])
            if looks_like_name(cand):
                cands.append(cand)
        # remove duplicates
        seen = set()
        cands2 = []
        for c in cands:
            if c not in seen:
                seen.add(c)
                cands2.append(c)

        # try best combo (3 lines: last/first/middle)
        if len(cands2) >= 2:
            # heuristic: if 3 candidates exist, assume last/first/middle
            if len(cands2) >= 3 and not middle:
                last = last or cands2[0]
                first = first or cands2[1]
                middle = middle or cands2[2]
            else:
                first = first or cands2[0]
                last = last or cands2[1]

    return first, middle, last

def extract_id_number(full_text_upper: str):
    t = full_text_upper or ""
    m = re.search(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b", t)
    if m:
        return m.group(0)
    m2 = re.search(r"\b\d{16}\b", t)
    if m2:
        return m2.group(0)
    # fallback any long digits with hyphen style
    m3 = re.search(r"\b\d{3,}-\d{3,}-\d{3,}\b", t)
    if m3:
        return m3.group(0)
    return ""

def extract_dob(full_text):
    full_text = (full_text or "").upper()
    m1 = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2}\s+\d{4}",
        full_text
    )
    if m1:
        try:
            return datetime.strptime(m1.group(0), "%B %d %Y").strftime("%Y-%m-%d")
        except Exception:
            pass

    m2 = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", full_text)
    if m2:
        mm, dd, yyyy = m2.groups()
        try:
            return datetime.strptime(f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}", "%Y-%m-%d").strftime("%Y-%m-%d")
        except Exception:
            pass

    # philid format is usually "JANUARY 28, 2006" (with comma)
    m3 = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2},\s+\d{4}",
        full_text
    )
    if m3:
        try:
            return datetime.strptime(m3.group(0), "%B %d, %Y").strftime("%Y-%m-%d")
        except Exception:
            pass

    return ""

def extract_address(full_text):
    t = (full_text or "").upper()
    # best-effort: get text after TIRAHAN/ADDRESS
    m = re.search(r"(TIRAHAN|ADDRESS)\s*[:\-]?\s*(.+)", t)
    if m:
        return m.group(2).strip()
    # fallback: find CITY / BARANGAY lines
    m2 = re.search(r"\bBARANGAY\b\s+(.+)", t)
    if m2:
        return m2.group(0).strip()
    return ""

def extract_gender(full_text):
    t = (full_text or "").upper()
    m = re.search(r"\b(SEX|GENDER)\b\s*[:\-]?\s*([A-Z])\b", t)
    return m.group(2) if m else ""

def parse_fields_from_image(img_bgr):
    gray, thr = preprocess(img_bgr)

    items, full1 = ocr_items(thr)
    if len(full1.strip()) < 10:
        items, full1 = ocr_items(gray)

    full_text = full1.upper()

    # Names (PhilID tuned)
    first, middle, last = extract_philid_names(items, full_text)

    dob = extract_dob(full_text)
    id_no = extract_id_number(full_text)
    address = extract_address(full_text)
    gender = extract_gender(full_text)

    return {
        "First_name": first,
        "Middle_name": middle,
        "Last_name": last,
        "Date_of_birth": dob,
        "Gender": gender,
        "Contact": "",
        "Address": address,
        "ID_no": id_no,
        "__full_text": full_text,
    }, None

# -----------------------
# ERROR HANDLER
# -----------------------
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e
    if request.path in ["/upload", "/export-pdf", "/reset-record"]:
        traceback.print_exc()
        return json_error("Server error", 500, details=str(e))
    raise e

# -----------------------
# RECORD MANAGEMENT
# -----------------------
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

    for key in ["First_name", "Middle_name", "Last_name", "Date_of_birth", "Gender", "Address", "ID_no"]:
        v = (extracted.get(key) or "").strip()
        if v:
            g[key] = v

    if predicted_id_type:
        g["ID_type"] = predicted_id_type

    g["Age"] = compute_age(g.get("Date_of_birth") or "")
    record["Guest"] = g

    img_path = extracted.get("Img_path", "")
    if slot == "primary":
        record["Primary_id"] = {"Img_path": img_path, "Uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    else:
        if img_path and not any(x.get("Img_path") == img_path for x in record["Secondary_ids"]):
            record["Secondary_ids"].append({"Img_path": img_path, "Uploaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})

    record["Secondary_count"] = len(record["Secondary_ids"])
    record["Can_proceed"] = can_proceed(record)
    return record

def apply_manual_overrides(record: dict, payload: dict):
    g = record.get("Guest", {})
    def pick(key):
        v = payload.get(key)
        return None if v is None else str(v).strip()
    for k in ["ID_type", "ID_no", "First_name", "Middle_name", "Last_name", "Date_of_birth", "Gender", "Contact", "Address"]:
        v = pick(k)
        if v is not None:
            g[k] = v
    g["Age"] = compute_age(g.get("Date_of_birth") or "")
    record["Guest"] = g
    record["Can_proceed"] = can_proceed(record)

# -----------------------
# RESET
# -----------------------
@app.route("/reset-record", methods=["POST"])
def reset_record():
    data = request.get_json(silent=True) or {}
    ref = data.get("reference_id") or data.get("Reference_id")
    if not ref:
        return json_error("Missing reference_id", 400)
    RECORDS.pop(ref, None)
    return jsonify({"ok": True}), 200

# -----------------------
# UPLOAD (used by BOTH upload + camera now)
# -----------------------
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

        unique = f"upload_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}_{filename}"
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

        extracted, err = parse_fields_from_image(img)
        if err:
            return json_error(err, 400)

        extracted["Img_path"] = img_path_db
        record = merge_extracted_into_record(extracted, ref=ref, slot=slot, predicted_id_type=predicted_id_type)
        return jsonify(record), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning", 500, details=str(e))

# -----------------------
# PDF EXPORT (same as yours)
# -----------------------
def wrap_text_by_width(c, text, max_width, font_name="Helvetica", font_size=11):
    text = (text or "").replace("\n", " ").strip()
    if not text:
        return [""]
    c.setFont(font_name, font_size)
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if c.stringWidth(test, font_name, font_size) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
                cur = w
            else:
                lines.append(w)
                cur = ""
    if cur:
        lines.append(cur)
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

        header_top = h - margin
        logo_abs = os.path.join(app.root_path, "static", LOGO_REL_PATH)

        logo_w, logo_h = 210, 50
        logo_y = header_top - logo_h
        if os.path.exists(logo_abs):
            try:
                c.drawImage(ImageReader(logo_abs), margin, logo_y, width=logo_w, height=logo_h,
                            preserveAspectRatio=True, mask="auto")
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

        body_top = header_top - 105

        photo_w, photo_h = 240, 140
        photo_x = w - margin - photo_w
        photo_y = (header_line_y - 10) - photo_h

        gutter = 18
        left_col_x = margin
        left_col_right = photo_x - gutter

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
            lines = wrap_text_by_width(c, value, value_max_w, "Helvetica", 11)[:max_lines]
            c.setFont("Helvetica", 11)
            vy = y
            for ln in lines:
                c.drawString(details_value_x, vy, ln)
                vy -= 14
            used = max(1, len(lines))
            y -= (18 + (used - 1) * 14)

        kv("ID Category", record.get("ID_category", "Unknown"), 1)
        kv("ID Type", guest.get("ID_type", ""), 2)
        kv("ID Number", guest.get("ID_no", ""), 2)
        kv("First name", guest.get("First_name", ""), 2)
        kv("Middle name", guest.get("Middle_name", ""), 2)
        kv("Last name", guest.get("Last_name", ""), 2)
        kv("Birthdate", guest.get("Date_of_birth", ""), 1)
        kv("Age", str(guest.get("Age")) if guest.get("Age") is not None else "", 1)
        kv("Gender", guest.get("Gender", ""), 1)
        kv("Contact", guest.get("Contact", ""), 2)

        c.setFont("Helvetica-Bold", 11)
        c.drawString(details_label_x, y, "Address:")
        addr_lines = wrap_text_by_width(c, guest.get("Address", "") or "N/A", value_max_w, "Helvetica", 11)
        c.setFont("Helvetica", 11)
        for ln in addr_lines[:3]:
            c.drawString(details_value_x, y, ln)
            y -= 14
        y -= 10

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
                c.drawImage(ImageReader(img_abs),
                            photo_x + 4, photo_y + 4,
                            width=photo_w - 8, height=photo_h - 8,
                            preserveAspectRatio=True, anchor="c")
            except Exception:
                pass

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
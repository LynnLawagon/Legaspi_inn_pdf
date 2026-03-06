# app.py
from flask import Flask, request, jsonify, render_template, send_file, send_from_directory, abort
from werkzeug.utils import secure_filename
from werkzeug.exceptions import HTTPException

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from dotenv import load_dotenv
from db import get_supabase

import os
import re
import cv2
import traceback
import random
import numpy as np

from datetime import datetime, date
import easyocr

load_dotenv()

app = Flask(__name__, template_folder="templates")

# FOLDERS
PDF_FOLDER = os.path.join(app.root_path, "static", "PDFs")
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

# OCR (cached)
reader = None


def get_reader():
    global reader
    if reader is None:
        reader = easyocr.Reader(["en", "tl"], gpu=False)
    return reader


# In-memory records 
RECORDS = {}

# ROUTES (PAGES)
@app.route("/")
def home():
    return render_template("landing.html")


@app.route("/scan-page")
def scan_page():
    return render_template("index.html")

# DB TEST
@app.route("/test-db")
def test_db():
    try:
        supabase = get_supabase()
        res = (
            supabase.table("tbl_guests")
            .select("reference_id, reference_code, first_name, last_name, created_at")
            .order("reference_id", desc=True)
            .limit(10)
            .execute()
        )
        return jsonify({"ok": True, "rows": res.data or []})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# genders list from tbl_gender
@app.route("/meta/genders", methods=["GET"])
def meta_genders():
    try:
        supabase = get_supabase()
        res = (
            supabase.table("tbl_gender")
            .select("gender_id, gender_name")
            .order("gender_id")
            .execute()
        )

        rows = [
            {
                "Gender_id": row["gender_id"],
                "gender_name": row["gender_name"]
            }
            for row in (res.data or [])
        ]
        return jsonify(rows), 200

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": "Failed to load genders", "details": str(e)}), 500


# HELPERS
def json_error(msg, code=400, **extra):
    payload = {"error": msg}
    payload.update(extra)
    return jsonify(payload), code


def normalize_contact(contact: str) -> str:
    return re.sub(r"\D", "", contact or "")


def is_valid_contact_11(contact: str) -> bool:
    return bool(re.fullmatch(r"\d{11}", contact or ""))


def gender_exists(gender_id):
    if gender_id is None:
        return None

    s = str(gender_id).strip()
    if not s:
        return None

    supabase = get_supabase()
    res = (
        supabase.table("tbl_gender")
        .select("gender_id, gender_name")
        .eq("gender_id", int(s))
        .limit(1)
        .execute()
    )

    if not res.data:
        return None

    row = res.data[0]
    return {
        "Gender_id": row["gender_id"],
        "gender_name": row["gender_name"]
    }


def normalize_dob(dob_str: str):
    s = (dob_str or "").strip()
    if not s:
        return None

    # YYYY-MM-DD
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").strftime("%Y-%m-%d")
        except Exception:
            return None

    # DD/MM/YYYY or DD-MM-YYYY = YYYY-MM-DD
    m = re.fullmatch(r"(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})", s)
    if m:
        dd, mm, yyyy = m.groups()
        try:
            return datetime.strptime(
                f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}",
                "%Y-%m-%d"
            ).strftime("%Y-%m-%d")
        except Exception:
            return None

    return None


def compute_age(dob_str: str):
    dob_str = normalize_dob(dob_str)
    if not dob_str:
        return None

    try:
        dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    except Exception:
        return None

    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age if 0 <= age <= 130 else None


def safe_resize(img, target_w=900):
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


def generate_ref_code():
    return f"REF-{datetime.now().year}{random.randint(1000, 9999)}"


def ensure_unique_ref_code():
    supabase = get_supabase()

    for _ in range(20):
        code = generate_ref_code()
        res = (
            supabase.table("tbl_guests")
            .select("reference_code")
            .eq("reference_code", code)
            .limit(1)
            .execute()
        )
        if not res.data:
            return code

    return f"REF-{datetime.now().year}{random.randint(100000, 999999)}"

# OCR + PARSING
STOPWORDS = {
    "REPUBLIC", "PHILIPPINES", "PHILIPPINE", "IDENTIFICATION", "CARD", "NATIONAL", "ID",
    "PHILSYS", "DOB", "BIRTH", "DATE", "SEX", "GENDER", "ADDRESS", "TIRAHAN",
    "SIGNATURE", "ISSUED", "VALID", "BARANGAY", "CLEARANCE", "CERTIFICATE", "PSA",
    "PAMBANSANG", "PAGKAKAKILANLAN", "PILIPINAS",
    "APELYIDO", "GIVEN", "MIDDLE", "NAME", "PANGALAN", "KAPANGANAKAN", "KASARIAN",
    "SENIOR", "HIGH", "SCHOOL", "STUDENT", "SIGN", "NO", "NUMBER",
    "COLLEGE", "UNIVERSITY", "CAMPUS", "DEPARTMENT", "CITY", "PROVINCE",
}


def preprocess(img_bgr):
    img_bgr = safe_resize(img_bgr, target_w=900)
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    thr = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31, 9
    )
    return gray, thr


def ocr_items(img):
    r = get_reader()
    res = r.readtext(
        img,
        detail=1,
        paragraph=False,
        batch_size=16,
        text_threshold=0.55,
        low_text=0.30,
        link_threshold=0.30,
        mag_ratio=1.2
    )

    items, full = [], []
    for (bbox, text, conf) in res:
        if not text:
            continue

        up = str(text).strip().upper()
        if not up:
            continue

        full.append(up)

        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        cy = float(sum(ys)) / 4.0
        items.append({
            "text": up,
            "conf": float(conf) if conf is not None else 0.0,
            "cy": cy
        })

    items.sort(key=lambda x: x["cy"])
    return items, " ".join(full)


def clean_name(s: str) -> str:
    s = (s or "").upper()
    s = re.sub(r"[^A-Z\s\-\.]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    toks = []
    for t in s.split():
        t2 = t.strip(".")
        if not t2:
            continue
        if t2 in STOPWORDS:
            continue
        toks.append(t2)

    return " ".join(toks).strip()


def looks_like_name(s: str) -> bool:
    if not s:
        return False

    toks = s.split()
    if not (1 <= len(toks) <= 6):
        return False

    bad = {
        "REPUBLIC", "PHILIPPINES", "PAMBANSANG", "PAGKAKAKILANLAN",
        "PHILIPPINE", "IDENTIFICATION", "CARD", "STUDENT", "SCHOOL"
    }
    if any(b in toks for b in bad):
        return False

    return True


def get_value_near_label(items, label_variants, max_lines_ahead=8):
    texts = [x["text"] for x in items]

    for i, t in enumerate(texts):
        tt = (t or "").upper()
        hit = None

        for v in label_variants:
            if v in tt:
                hit = v
                break

        if not hit:
            continue

        after = tt.split(hit, 1)[-1].strip()
        after = clean_name(after)
        if looks_like_name(after):
            return after

        for j in range(i + 1, min(i + 1 + max_lines_ahead, len(texts))):
            cand = clean_name(texts[j])
            if looks_like_name(cand):
                return cand

    return ""


def extract_names(items):
    last_name = get_value_near_label(items, ["APELYIDO", "LAST NAME", "SURNAME"])
    first_name = get_value_near_label(items, ["GIVEN NAMES", "GIVEN NAME", "FIRST NAME", "MGA PANGALAN"])
    middle_name = get_value_near_label(items, ["MIDDLE NAME", "GITNANG APELYIDO"])

    if not (first_name and last_name):
        full = get_value_near_label(items, ["FULL NAME", "STUDENT NAME", "NAME"], max_lines_ahead=6)
        if full:
            toks = clean_name(full).split()
            if len(toks) >= 2:
                first_name = first_name or toks[0]
                last_name = last_name or toks[-1]
                if len(toks) > 2:
                    middle_name = middle_name or " ".join(toks[1:-1])

    return first_name, middle_name, last_name


def extract_id_number(full_text_upper: str):
    t = full_text_upper or ""

    m = re.search(r"\b\d{4}-\d{4}-\d{4}-\d{4}\b", t)
    if m:
        return m.group(0)

    m2 = re.search(r"\b\d{16}\b", t)
    if m2:
        return m2.group(0)

    m3 = re.search(r"\b\d{3,}-\d{3,}-\d{3,}\b", t)
    if m3:
        return m3.group(0)

    return ""


def extract_dob(full_text):
    full_text = (full_text or "").upper()

    m0 = re.search(r"\b\d{4}-\d{2}-\d{2}\b", full_text)
    if m0:
        return m0.group(0)

    m2 = re.search(r"\b(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{4})\b", full_text)
    if m2:
        dd, mm, yyyy = m2.groups()
        return normalize_dob(f"{dd}/{mm}/{yyyy}") or ""

    m3 = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2},\s+\d{4}",
        full_text
    )
    if m3:
        try:
            return datetime.strptime(m3.group(0), "%B %d, %Y").strftime("%Y-%m-%d")
        except Exception:
            pass

    m1 = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2}\s+\d{4}",
        full_text
    )
    if m1:
        try:
            return datetime.strptime(m1.group(0), "%B %d %Y").strftime("%Y-%m-%d")
        except Exception:
            pass

    return ""


def extract_address(full_text):
    t = (full_text or "").upper()

    m = re.search(r"(TIRAHAN|ADDRESS)\s*[:\-]?\s*(.+)", t)
    if m:
        return m.group(2).strip()

    m2 = re.search(r"\bBARANGAY\b\s+(.+)", t)
    if m2:
        return m2.group(0).strip()

    return ""


def extract_gender(full_text):
    t = (full_text or "").upper()
    m = re.search(r"\b(SEX|GENDER)\b\s*[:\-]?\s*([A-Z])\b", t)
    return m.group(2) if m else ""


def parse_fields_from_image(img_bgr):
    gray, _thr = preprocess(img_bgr)
    items, full = ocr_items(gray)
    full_text = (full or "").upper()

    first_name, middle_name, last_name = extract_names(items)
    dob = extract_dob(full_text)
    id_no = extract_id_number(full_text)
    address = extract_address(full_text)
    gender = extract_gender(full_text)

    return {
        "First_name": first_name,
        "Middle_name": middle_name,
        "Last_name": last_name,
        "Date_of_birth": dob,
        "Gender": gender,
        "Contact": "",
        "Address": address,
        "ID_no": id_no,
        "__full_text": full_text,
    }, None

# ERROR HANDLER
@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return e

    if request.path in ["/upload", "/export-pdf", "/reset-record", "/save-guest", "/meta/genders"]:
        traceback.print_exc()
        return json_error("Server error", 500, details=str(e))

    raise e

# RECORD HELPERS
def ensure_record(ref_code: str):
    if ref_code not in RECORDS:
        RECORDS[ref_code] = {
            "Reference_code": ref_code,
            "Guest": {
                "ID_num": None,
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
            "Img_path": "",
            "Can_proceed": True,
            "ID_category": "PRIMARY",
        }
    return RECORDS[ref_code]


def apply_manual_overrides(record: dict, payload: dict):
    g = record.get("Guest", {})

    def pick(key):
        v = payload.get(key)
        return None if v is None else str(v).strip()

    id_num = payload.get("ID_num")
    if id_num is not None and str(id_num).strip() != "":
        try:
            g["ID_num"] = int(id_num)
        except Exception:
            g["ID_num"] = None

    for k in ["ID_type", "ID_no", "First_name", "Middle_name", "Last_name", "Date_of_birth", "Gender", "Contact", "Address"]:
        v = pick(k)
        if v is not None:
            if k == "Contact":
                v = normalize_contact(v)
            if k == "Date_of_birth":
                v = normalize_dob(v) or ""
            g[k] = v

    g["Age"] = compute_age(g.get("Date_of_birth") or "")
    record["Guest"] = g

# RESET
@app.route("/reset-record", methods=["POST"])
def reset_record():
    data = request.get_json(silent=True) or {}
    ref_code = data.get("reference_id") or data.get("Reference_id")

    if not ref_code:
        return json_error("Missing reference_id", 400)

    RECORDS.pop(str(ref_code), None)
    return jsonify({"ok": True}), 200


# UPLOAD / OCR
@app.route("/upload", methods=["POST"])
def upload():
    try:
        if "file" not in request.files:
            return json_error("No file uploaded", 400)

        file = request.files["file"]
        if not file or file.filename == "":
            return json_error("No file selected", 400)

        ref_code = request.form.get("reference_id") or request.form.get("Reference_id")
        if not ref_code:
            ref_code = ensure_unique_ref_code()

        predicted_id_type = request.form.get("predicted_id_type", "")

        filename = secure_filename(file.filename or "upload.jpg")
        ext = os.path.splitext(filename)[1].lower()

        if ext not in ALLOWED_EXT:
            mt = (file.mimetype or "").lower()
            if "jpeg" in mt or "jpg" in mt:
                ext = ".jpg"
            elif "png" in mt:
                ext = ".png"
            elif "pdf" in mt:
                ext = ".pdf"

        if ext not in ALLOWED_EXT:
            return json_error("Invalid file type. Use JPG/PNG/WEBP or PDF.", 400)

        data = file.read()
        if not data:
            return json_error("Empty file", 400)

        unique = f"{ref_code}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}{ext}"
        saved_path = os.path.join(UPLOAD_FOLDER, unique)

        with open(saved_path, "wb") as f:
            f.write(data)

        if ext == ".pdf":
            img, err = pdf_first_page_to_bgr(saved_path)
            if err:
                return json_error("PDF error", 500, details=err)
            if img is None:
                return json_error("Could not read PDF", 400)
        else:
            nparr = np.frombuffer(data, np.uint8)
            img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            if img is None:
                return json_error("Could not read the uploaded image", 400)

        extracted, err = parse_fields_from_image(img)
        if err:
            return json_error(err, 400)

        record = ensure_record(ref_code)
        g = record["Guest"]

        for key in ["First_name", "Middle_name", "Last_name", "Date_of_birth", "Gender", "Address", "ID_no"]:
            v = (extracted.get(key) or "").strip()
            if v:
                if key == "Date_of_birth":
                    v = normalize_dob(v) or v
                g[key] = v

        if predicted_id_type:
            g["ID_type"] = predicted_id_type

        g["Age"] = compute_age(g.get("Date_of_birth") or "")
        record["Guest"] = g
        record["Img_path"] = f"uploads/{unique}"
        record["Can_proceed"] = True
        record["ID_category"] = "PRIMARY"

        return jsonify({
            "Reference_id": ref_code,
            "Reference_code": ref_code,
            "Guest": record["Guest"],
            "Img_path": record["Img_path"],
            "Can_proceed": True,
            "ID_category": "PRIMARY"
        }), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning", 500, details=str(e))

# SAVE GUEST (DB)
@app.route("/save-guest", methods=["POST"])
def save_guest():
    try:
        payload = request.get_json(silent=True) or {}
        ref_code = payload.get("reference_id") or payload.get("Reference_code") or payload.get("Reference_id")

        if not ref_code:
            return json_error("Missing reference_id", 400)

        record = RECORDS.get(str(ref_code))
        if not record:
            return json_error("No record found for that Reference ID.", 404)

        apply_manual_overrides(record, payload)
        guest = record.get("Guest") or {}

        if not guest.get("ID_type"):
            return json_error("ID Type is required.", 400)
        if not guest.get("First_name"):
            return json_error("First Name is required.", 400)
        if not guest.get("Last_name"):
            return json_error("Last Name is required.", 400)

        dob_norm = normalize_dob(guest.get("Date_of_birth") or "")
        if not dob_norm:
            return json_error("Date of birth must be valid (YYYY-MM-DD).", 400)
        guest["Date_of_birth"] = dob_norm
        guest["Age"] = compute_age(dob_norm)

        addr = (guest.get("Address") or "").strip()
        if not addr:
            return json_error("Address is required.", 400)

        img_rel = (record.get("Img_path") or "").strip()
        if not img_rel:
            return json_error("Image path missing. Please rescan.", 400)

        contact = normalize_contact(guest.get("Contact", ""))
        if contact and not is_valid_contact_11(contact):
            return json_error("Contact must be exactly 11 digits.", 400)
        guest["Contact"] = contact

        row = gender_exists(payload.get("Gender_id"))
        if not row:
            return json_error("Please select Gender from the list.", 400)

        supabase = get_supabase()

        insert_payload = {
            "id_num": guest.get("ID_num"),
            "reference_code": str(ref_code),
            "id_type": guest.get("ID_type", ""),
            "id_no": guest.get("ID_no") or None,
            "first_name": guest.get("First_name", ""),
            "middle_name": guest.get("Middle_name") or None,
            "last_name": guest.get("Last_name", ""),
            "date_of_birth": guest.get("Date_of_birth"),
            "age": guest.get("Age"),
            "can_proceed": True,
            "gender_id": int(row["Gender_id"]),
            "contact": guest.get("Contact") or None,
            "address": addr,
            "img_path": img_rel
        }

        res = (
            supabase.table("tbl_guests")
            .insert(insert_payload)
            .execute()
        )

        if not res.data:
            return json_error("Insert failed", 500)

        saved = res.data[0]

        return jsonify({
            "ok": True,
            "reference_code": saved["reference_code"],
            "reference_id": saved["reference_id"]
        }), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("DB save failed", 500, details=str(e))

# PDF EXPORT
@app.route("/export-pdf", methods=["POST"])
def export_pdf():
    try:
        payload = request.get_json(silent=True) or {}
        ref_code = payload.get("reference_id") or payload.get("Reference_code") or payload.get("Reference_id")
        if not ref_code:
            return json_error("Missing reference_id", 400)

        record = RECORDS.get(str(ref_code))
        if not record:
            return json_error("No record found for that Reference ID.", 404)

        apply_manual_overrides(record, payload)
        guest = record.get("Guest") or {}

        guest["Date_of_birth"] = normalize_dob(guest.get("Date_of_birth") or "") or (guest.get("Date_of_birth") or "")
        guest["Age"] = compute_age(guest.get("Date_of_birth") or "")

        filename = f"{ref_code}.pdf"
        pdf_path = os.path.join(PDF_FOLDER, filename)

        c = canvas.Canvas(pdf_path, pagesize=A4)
        w, h = A4
        margin = 45

        header_top = h - margin
        header_bottom = h - 120

        logo_path = os.path.join(app.root_path, "static", "img", "logo.png")
        logo_x = margin
        logo_y = header_top - 35

        if os.path.exists(logo_path):
            try:
                c.drawImage(ImageReader(logo_path), logo_x, logo_y, width=90, height=30, mask="auto")
            except Exception:
                pass

        c.setFont("Helvetica", 9)
        right_x = w - margin
        c.drawRightString(right_x, header_top - 10, "115 Pelayo St, Poblacion District, Davao City, 8000 Davao del Sur")
        c.drawRightString(right_x, header_top - 25, "096 456 8920")

        c.setFont("Helvetica-Bold", 9)
        c.drawRightString(right_x, header_top - 45, f"{ref_code}")
        c.setFont("Helvetica", 8)
        c.drawRightString(right_x, header_top - 58, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

        c.setLineWidth(0.7)
        c.line(margin, header_bottom, w - margin, header_bottom)

        body_top = header_bottom - 25

        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, body_top, "GUEST DETAILS")

        photo_w, photo_h = 190, 115
        photo_x = w - margin - photo_w
        photo_y = body_top - 10 - photo_h
        c.rect(photo_x, photo_y, photo_w, photo_h)

        img_rel = (record.get("Img_path") or "").replace("\\", "/")
        img_abs = os.path.join(app.root_path, "static", img_rel)
        if img_rel and os.path.exists(img_abs):
            try:
                c.drawImage(
                    ImageReader(img_abs),
                    photo_x + 3, photo_y + 3,
                    width=photo_w - 6, height=photo_h - 6,
                    preserveAspectRatio=True, anchor="c"
                )
            except Exception:
                pass

        left_block_right_limit = photo_x - 20
        label_x = margin
        value_x = margin + 115
        y = body_top - 35
        gap = 16

        def draw_row(label, value):
            nonlocal y
            c.setFont("Helvetica-Bold", 10)
            c.drawString(label_x, y, f"{label}:")
            c.setFont("Helvetica", 10)

            txt = (value or "").strip()
            if not txt:
                txt = "N/A"

            max_chars = 55
            if len(txt) > max_chars:
                txt = txt[:max_chars - 3] + "..."

            c.drawString(value_x, y, txt)
            y -= gap

        draw_row("ID Category", (record.get("ID_category") or "Primary").title())
        draw_row("ID Type", guest.get("ID_type", ""))
        draw_row("ID Number", guest.get("ID_no", ""))

        draw_row("First name", guest.get("First_name", ""))
        draw_row("Middle name", guest.get("Middle_name", ""))
        draw_row("Last name", guest.get("Last_name", ""))

        draw_row("Birthdate", guest.get("Date_of_birth", ""))
        draw_row("Age", str(guest.get("Age")) if guest.get("Age") is not None else "")
        draw_row("Gender", payload.get("gender_name", ""))
        draw_row("Contact", guest.get("Contact", ""))

        addr = guest.get("Address", "") or "N/A"
        c.setFont("Helvetica-Bold", 10)
        c.drawString(label_x, y, "Address:")
        c.setFont("Helvetica", 10)

        max_width = left_block_right_limit - value_x
        words = addr.split()
        line = ""
        y_addr = y

        for wword in words:
            test = (line + " " + wword).strip()
            if c.stringWidth(test, "Helvetica", 10) <= max_width:
                line = test
            else:
                c.drawString(value_x, y_addr, line)
                y_addr -= gap
                line = wword

        if line:
            c.drawString(value_x, y_addr, line)
            y_addr -= gap

        c.setFont("Helvetica-Oblique", 8)
        c.drawString(margin, 25, "Generated by AIntelli OCR Guest System")
        c.drawRightString(w - margin, 25, "Page 1")

        c.save()
        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        traceback.print_exc()
        return json_error("PDF export failed", 500, details=str(e))

# RECORDS
@app.route("/records")
def records():
    files = []
    if os.path.exists(PDF_FOLDER):
        files = [f for f in os.listdir(PDF_FOLDER) if f.lower().endswith(".pdf")]
    files.sort(reverse=True)
    return render_template("records.html", files=files)


@app.route("/view/<path:filename>")
def view_pdf(filename):
    safe = secure_filename(filename)
    if not safe.lower().endswith(".pdf"):
        abort(404)
    return send_from_directory(PDF_FOLDER, safe, as_attachment=False)


@app.route("/download/<path:filename>")
def download_pdf(filename):
    safe = secure_filename(filename)
    if not safe.lower().endswith(".pdf"):
        abort(404)
    return send_from_directory(PDF_FOLDER, safe, as_attachment=True)


if __name__ == "__main__":
    app.run(debug=True)
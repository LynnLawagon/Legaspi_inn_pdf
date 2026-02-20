from flask import Flask, request, jsonify, render_template, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename

import os
import io
import re
import cv2
import base64
import numpy as np
import traceback
import random
from datetime import datetime, date

#Pillow
from PIL import Image
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

import easyocr

app = Flask(__name__, template_folder="templates")

PDF_FOLDER = os.path.join(app.root_path, "static", "PDFs")
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

# OCR
reader = None

# store last scanned record (no DB)
LAST_RECORD = None


def get_reader():
    global reader
    if reader is None:
        reader = easyocr.Reader(["en"], gpu=False)
    return reader


def json_error(msg, code=400, **extra):
    payload = {"error": msg}
    payload.update(extra)
    return jsonify(payload), code


def safe_resize(img, max_w=1200):
    h, w = img.shape[:2]
    if w > max_w:
        scale = max_w / w
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return img

# reference ID format
def generate_reference_id():
    return f"REF-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{random.randint(1000,9999)}"

#compute age and minor status from DOB string in "YYYY-MM-DD" format
def compute_age_and_minor(dob_str: str):
    if not dob_str:
        return None, None
    try:
        dob = datetime.strptime(dob_str, "%Y-%m-%d").date()
    except:
        return None, None

    today = date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age, (age < 18)


#PDF to image using PyMuPDF (fitz)
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

# Extract fields using regex from OCR text
def parse_fields_from_image(img):
    r = get_reader()
    img = safe_resize(img)

    results = r.readtext(img)
    texts = [t.strip() for _, t, s in results if s > 0.4]
    full_text = " ".join(texts).upper()

    dob_match = re.search(
        r"(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2}\s+\d{4}",
        full_text
    )

    dob_mysql = ""
    if dob_match:
        try:
            dob_mysql = datetime.strptime(dob_match.group(0), "%B %d %Y").strftime("%Y-%m-%d")
        except:
            dob_mysql = ""

    last_name = re.search(r"APELYIDO/ LAST NAME\s+([A-Z\s]+?)(?=\sMGA PANGALAN/)", full_text)
    first_name = re.search(r"MGA PANGALAN/ GIVEN NAMES\s+([A-Z\s]+?)(?=\sGITNANG APELYIDO/)", full_text)
    middle_name = re.search(r"GITNANG APELYIDO/.*?MIDDLE NAME\s*([A-Z\s]+?)(?=\sPETSA NG)", full_text)
    address = re.search(r"TIRAHAN/ADDRESS\s+(.+)", full_text)
    contact = re.search(r"CONTACT\s+(.+)", full_text)

    gender = re.search(r"SEX\s+([A-Z])", full_text)

    return {
        "ID_type": "",
        "First_name": first_name.group(1).strip() if first_name else "",
        "Middle_name": middle_name.group(1).strip() if middle_name else "",
        "Last_name": last_name.group(1).strip() if last_name else "",
        "Date_of_birth": dob_mysql,
        "Gender": gender.group(1).strip() if gender else "",
        "Contact": contact.group(1).strip() if contact else "",
        "Address": address.group(1).strip() if address else "",
    }

# Global error handler for unexpected exceptions
@app.errorhandler(Exception)
def handle_exception(e):
    if request.path in ["/upload", "/scan", "/export-pdf"]:
        traceback.print_exc()
        return json_error("Server error", 500, details=str(e))
    raise e


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    global LAST_RECORD
    try:
        if "file" not in request.files:
            return json_error("No file uploaded", 400)

        file = request.files["file"]
        if not file or file.filename == "":
            return json_error("No file selected", 400)

        filename = secure_filename(file.filename)
        ext = os.path.splitext(filename)[1].lower()

        if ext not in ALLOWED_EXT:
            return json_error("Invalid file type. Use JPG/PNG/WEBP or PDF.", 400)

        unique = f"upload_{datetime.now().strftime('%Y%m%d%H%M%S')}_{filename}"
        saved_path = os.path.join(UPLOAD_FOLDER, unique)
        file.save(saved_path)

        img_path_db = f"uploads/{unique}"

        # decode
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

        extracted["Reference_id"] = generate_reference_id()
        age, is_minor = compute_age_and_minor(extracted.get("Date_of_birth"))
        extracted["Age"] = age
        extracted["Is_minor"] = is_minor

        LAST_RECORD = extracted

        return jsonify(extracted), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning upload", 500, details=str(e))


@app.route("/scan", methods=["POST"])
def scan():
    global LAST_RECORD
    try:
        data = request.get_json(silent=True)
        if not data or "image" not in data:
            return json_error("Missing image in request JSON", 400)

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

        extracted["Reference_id"] = generate_reference_id()
        age, is_minor = compute_age_and_minor(extracted.get("Date_of_birth"))
        extracted["Age"] = age
        extracted["Is_minor"] = is_minor

        LAST_RECORD = extracted
        return jsonify(extracted), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning camera", 500, details=str(e))


def wrap_text(text, width=95):
    text = (text or "").replace("\n", " ").strip()
    lines = []
    while len(text) > width:
        lines.append(text[:width])
        text = text[width:]
    if text:
        lines.append(text)
    return lines

# Generate PDF report from extracted data
@app.route("/export-pdf", methods=["POST"])
def export_pdf():
    try:
        data = request.get_json(silent=True) or {}
        age, is_minor = compute_age_and_minor(data.get("Date_of_birth", ""))
        data["Age"] = age
        data["Is_minor"] = is_minor

        ref = data.get("Reference_id") or generate_reference_id()
        data["Reference_id"] = ref

        filename = f"guest_{ref}.pdf"
        pdf_path = os.path.join(PDF_FOLDER, filename)

        c = canvas.Canvas(pdf_path, pagesize=A4)
        w, h = A4
        margin = 45

        # Header
        c.setFont("Helvetica-Bold", 14)
        c.drawString(margin, h - margin, "GUEST INFORMATION REPORT")

        c.setFont("Helvetica", 9)
        c.drawRightString(w - margin, h - margin, f"Reference ID: {ref}")
        c.drawRightString(w - margin, h - margin - 14, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        c.line(margin, h - margin - 22, w - margin, h - margin - 22)

        # Image
        photo_x = margin
        photo_y = h - margin - 150
        photo_w = 140
        photo_h = 105
        c.rect(photo_x, photo_y, photo_w, photo_h)

        img_rel = (data.get("Img_path") or "").replace("\\", "/")
        img_abs = os.path.join(app.root_path, "static", img_rel)
        if img_rel and os.path.exists(img_abs):
            try:
                c.drawImage(ImageReader(img_abs), photo_x + 3, photo_y + 3,
                            width=photo_w - 6, height=photo_h - 6,
                            preserveAspectRatio=True, anchor='c')
            except:
                pass

        # Fields aligned
        x_label = photo_x + photo_w + 20
        x_val = x_label + 115
        y = h - margin - 45

        def kv(label, value):
            nonlocal y
            c.setFont("Helvetica-Bold", 10)
            c.drawString(x_label, y, f"{label}:")
            c.setFont("Helvetica", 10)
            c.drawString(x_val, y, value if value else "N/A")
            y -= 16

        kv("ID Type", data.get("ID_type", ""))
        kv("First Name", data.get("First_name", ""))
        kv("Middle Name", data.get("Middle_name", ""))
        kv("Last Name", data.get("Last_name", ""))
        kv("Birthdate", data.get("Date_of_birth", ""))

        kv("Age", str(age) if age is not None else "N/A")
        status_text = "MINOR" if is_minor is True else ("ADULT" if is_minor is False else "UNKNOWN")
        kv("Status", status_text)

        kv("Gender", data.get("Gender", ""))
        kv("Contact", data.get("Contact", ""))

        # Address wrapped
        y = photo_y - 25
        c.setFont("Helvetica-Bold", 10)
        c.drawString(margin, y, "Address:")
        y -= 14
        c.setFont("Helvetica", 10)
        for line in wrap_text(data.get("Address", ""), width=95):
            c.drawString(margin, y, line)
            y -= 13

        # Footer
        c.setFont("Helvetica-Oblique", 8)
        c.drawString(margin, 25, "Generated by OCR Guest System")
        c.drawRightString(w - margin, 25, "Page 1")

        c.save()
        return send_file(pdf_path, as_attachment=True)

    except Exception as e:
        traceback.print_exc()
        return json_error("PDF export failed", 500, details=str(e))

if __name__ == "__main__":
    app.run(debug=True)

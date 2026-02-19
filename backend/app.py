from flask import Flask, request, jsonify, render_template, send_file
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from werkzeug.utils import secure_filename

# ngi
from PIL import Image
if not hasattr(Image, "ANTIALIAS"):
    Image.ANTIALIAS = Image.Resampling.LANCZOS

import cv2
import base64
import easyocr
import re
import numpy as np
import os
import io
import traceback
from datetime import datetime

app = Flask(__name__, template_folder="templates")

PDF_FOLDER = os.path.join(app.root_path, "static", "PDFs")
UPLOAD_FOLDER = os.path.join(app.root_path, "static", "uploads")
os.makedirs(PDF_FOLDER, exist_ok=True)
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

# Lazy OCR init
reader = None
def get_reader():
    global reader
    if reader is None:
        reader = easyocr.Reader(["en"], gpu=False)
    return reader

def json_error(msg, code=400, **extra):
    payload = {"error": msg}
    payload.update(extra)
    return jsonify(payload), code

# PDF -> image (first page)
def pdf_first_page_to_bgr(pdf_path):
    try:
        import fitz  # PyMuPDF
    except Exception:
        return None, "PyMuPDF not installed. Run: pip install pymupdf"

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

def safe_resize(img, max_w=1200):
    h, w = img.shape[:2]
    if w > max_w:
        scale = max_w / w
        img = cv2.resize(img, (int(w * scale), int(h * scale)))
    return img

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
    gender = re.search(r"SEX\s+([A-Z])", full_text)  # M/F usually

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

# Return JSON for API crashes (no HTML)
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

        if ext == ".pdf":
            img, err = pdf_first_page_to_bgr(saved_path)
            if err:
                return json_error("PDF upload error", 500, details=err)
            if img is None:
                return json_error("Could not read PDF", 400)
        else:
            img = cv2.imread(saved_path)
            if img is None:
                return json_error("Could not read the uploaded image", 400)

        extracted = parse_fields_from_image(img)
        extracted["Img_path"] = img_path_db
        return jsonify(extracted), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning upload", 500, details=str(e))

@app.route("/scan", methods=["POST"])
def scan():
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
        img_path = os.path.join(UPLOAD_FOLDER, filename)
        cv2.imwrite(img_path, img)
        img_path_db = f"uploads/{filename}"

        extracted = parse_fields_from_image(img)
        extracted["Img_path"] = img_path_db
        return jsonify(extracted), 200

    except Exception as e:
        traceback.print_exc()
        return json_error("Server error while scanning camera", 500, details=str(e))

# ✅ Export PDF from CURRENT form data (no DB)
@app.route("/export-pdf", methods=["POST"])
def export_pdf():
    data = request.get_json(silent=True) or {}

    filename = f"guest_{datetime.now().strftime('%Y%m%d%H%M%S')}.pdf"
    pdf_path = os.path.join(PDF_FOLDER, filename)

    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    margin = 50
    image_width = 150
    image_height = 100

    c.setFont("Helvetica-Bold", 16)
    c.drawString(margin, height - margin, "GUEST INFORMATION")
    c.setFont("Helvetica", 10)
    c.drawString(margin, height - margin - 20, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    # image
    img_rel = (data.get("Img_path") or "").replace("\\", "/")
    img_path = os.path.join(app.root_path, "static", img_rel)
    if img_rel and os.path.exists(img_path):
        try:
            img = ImageReader(img_path)
            c.drawImage(img, margin, height - margin - image_height - 40,
                        width=image_width, height=image_height, preserveAspectRatio=True)
        except Exception as e:
            print("Image error:", e)

    # text fields
    text_x = margin + image_width + 20
    text_y = height - margin - 40
    c.setFont("Helvetica", 12)

    c.drawString(text_x, text_y, f"First Name: {data.get('First_name','')}")
    text_y -= 18
    c.drawString(text_x, text_y, f"Middle Name: {data.get('Middle_name','')}")
    text_y -= 18
    c.drawString(text_x, text_y, f"Last Name: {data.get('Last_name','')}")
    text_y -= 18
    c.drawString(text_x, text_y, f"DOB: {data.get('Date_of_birth','') or 'N/A'}")
    text_y -= 18
    c.drawString(text_x, text_y, f"Gender: {data.get('Gender','') or 'N/A'}")
    text_y -= 18
    c.drawString(text_x, text_y, f"Contact: {data.get('Contact','')}")
    text_y -= 18
    c.drawString(text_x, text_y, f"ID Type: {data.get('ID_type','')}")

    addr_y = height - margin - image_height - 60 - 18 * 5
    c.setFont("Helvetica-Bold", 12)
    c.drawString(margin, addr_y, "Address:")
    c.setFont("Helvetica", 10)
    addr_text = c.beginText(margin, addr_y - 18)
    addr_text.setLeading(14)

    address = (data.get("Address") or "").replace("\n", " ")
    max_chars_per_line = 90
    while address:
        addr_text.textLine(address[:max_chars_per_line])
        address = address[max_chars_per_line:]
    c.drawText(addr_text)

    c.showPage()
    c.save()

    return send_file(pdf_path, as_attachment=True)

if __name__ == "__main__":
    app.run(debug=True)

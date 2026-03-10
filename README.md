# AIntelli OCR Result System

AIntelli is a web-based OCR result system designed to help inns identify guest IDs and extract information automatically. The system uses AI classification and Optical Character Recognition (OCR) to detect ID types and extract text from ID images.

---

# Language

- Python
- JavaScript
- HTML
- CSS

---

# Framework

- Flask

---

# Dependencies

The system requires the following Python dependencies:

- flask
- easyocr==1.7.2
- opencv-python-headless
- numpy
- reportlab
- pillow
- pymupdf
- supabase
- python-dotenv
- gunicorn
- torch==2.5.1
- torchvision==0.20.1

Install dependencies using:
pip install -r requirements.txt


---

# Tools Used

- **Google Teachable Machine** – for ID classification
- **EasyOCR** – for text extraction
- **OpenCV** – for image processing
- **ReportLab** – for generating PDF results
- **Railway** – backend deployment
- **Vercel** – frontend deployment
- **GitHub** – version control and repository hosting

---

# Database

The system uses **Supabase** to store guest records and OCR results.

Example environment variables:
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key


Create a `.env` file in the project root and add your Supabase credentials.

---

# How to Run the System

## 1. Clone the repository
git clone https://github.com/your-username/your-repository-name.git
cd your-repository-name


---

## 2. Create a virtual environment
python -m venv .venv


---

## 3. Activate the virtual environment
**Windows**
.venv\Scripts\activate

**Mac / Linux**
source .venv/bin/activate


---

## 4. Install dependencies
pip install -r requirements.txt


---

## 5. Create a `.env` file

Add your Supabase credentials:
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key


---

## 6. Run the application
python app.py


---

## 7. Open the system in your browser

---

# Step-by-Step Guide on How to Use the System

### Step 1 – Open the web application
Open the deployed system or the local server in your browser.

### Step 2 – Upload or capture an ID image
Upload an image file or use the camera to capture an ID.

### Step 3 – ID classification
The system uses **Teachable Machine** to classify the ID into:
- National ID
- School ID
- Invalid ID

### Step 4 – OCR processing
The system processes the image using **EasyOCR** to extract text from the ID.

### Step 5 – Review extracted information
The detected information will appear on the form or results page.

### Step 6 – Edit fields if necessary
If some details are incorrect, the user can manually edit them.

### Step 7 – Save the record
Save the information to the **Supabase database**.

### Step 8 – Generate PDF
The system generates a downloadable **PDF guest record**.

### Step 9 – Retry if needed
If the scan result is inaccurate, upload a clearer image and retry the process.

---

# Notes

- OCR accuracy depends on the **image quality, lighting, and angle** of the ID.
- AI classification may be affected by **limited training data** used in the model.
- Railway is used for backend deployment because some dependencies exceed the size limits of other hosting platforms.

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_file
import shutil
import os
import re
import io
import json
import uuid
import base64
import sqlite3
from datetime import datetime
from functools import wraps
from difflib import get_close_matches
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from PIL import Image, ImageFilter, ImageOps
import pytesseract
import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

app = Flask(__name__)
app.secret_key = "medicycle_ai_demo_secret_key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "medicycle.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
TEMP_FOLDER = os.path.join(BASE_DIR, "static", "temp")
PROOF_FOLDER = os.path.join(BASE_DIR, "static", "proofs")
CERT_FOLDER = os.path.join(BASE_DIR, "static", "certificates")
REPORT_FOLDER = os.path.join(BASE_DIR, "reports")
MED_DB_PATH = os.path.join(BASE_DIR, "medicine_db.json")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg"}

MOCK_CENTERS = [
    {"name": "City Pharmacy Return Point", "lat": 18.5308, "lng": 73.8478, "location": "Shivajinagar, Pune", "type": "Pharmacy"},
    {"name": "Green Med Collection Center", "lat": 18.5074, "lng": 73.8077, "location": "Kothrud, Pune", "type": "NGO Collection"},
    {"name": "Metro Hospital Disposal Desk", "lat": 18.5590, "lng": 73.8070, "location": "Aundh, Pune", "type": "Hospital"},
    {"name": "SafeRx Community Drop Box", "lat": 18.4967, "lng": 73.9272, "location": "Hadapsar, Pune", "type": "Community Center"}
]

for folder in [UPLOAD_FOLDER, TEMP_FOLDER, PROOF_FOLDER, CERT_FOLDER, REPORT_FOLDER]:
    os.makedirs(folder, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            points INTEGER DEFAULT 0,
            rank TEXT DEFAULT 'Starter',
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            filename TEXT,
            detected_medicine TEXT,
            category TEXT,
            risk_level TEXT,
            recommendation TEXT,
            awareness_message TEXT,
            expiry_date TEXT,
            confidence TEXT,
            status_title TEXT,
            raw_text TEXT,
            cleaned_text TEXT,
            points_awarded INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS proofs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            medicine_name TEXT,
            disposal_method TEXT,
            proof_image TEXT,
            verification_id TEXT,
            qr_file TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        )
    """)

    conn.commit()
    conn.close()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def preprocess_image(image_path):
    image = Image.open(image_path)
    image = image.convert("L")
    image = ImageOps.autocontrast(image)
    image = image.filter(ImageFilter.SHARPEN)
    return image


def clean_text(text):
    text = text.lower()
    text = text.replace("\n", " ")
    text = re.sub(r"[^a-z0-9/\- ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def detect_expiry(text):
    patterns = [
        r"\b\d{2}/\d{2}/\d{4}\b",
        r"\b\d{2}-\d{2}-\d{4}\b",
        r"\b\d{2}/\d{4}\b",
        r"\b\d{2}-\d{4}\b"
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group()
    return None


def detect_medicine(cleaned_text, antibiotic_list):
    for med in antibiotic_list:
        if med in cleaned_text:
            return med, "High"
    words = cleaned_text.split()
    for word in words:
        matches = get_close_matches(word, antibiotic_list, n=1, cutoff=0.82)
        if matches:
            return matches[0], "Medium"
    return None, "Low"


def build_analysis_message(detected_medicine, category, expiry_date, confidence_level):
    if detected_medicine and category == "Antibiotic":
        msg = f"{detected_medicine} appears to be an antibiotic medicine."
        if expiry_date:
            msg += f" Expiry marking detected: {expiry_date}."
        msg += " Safe return to a pharmacy or medical waste collection point is recommended."
        return msg
    if confidence_level == "Low":
        return "The medicine label is unclear in the uploaded image. Please upload a sharper image with the medicine name clearly visible."
    return "Medicine information was partially detected. Please verify the label manually before disposal."


def calculate_points(category, risk_level):
    if category == "Antibiotic" and risk_level == "High":
        return 100
    if category == "Antibiotic":
        return 70
    return 40


def rank_from_points(points):
    if points >= 500:
        return "Eco-Champion"
    if points >= 300:
        return "Eco-Warrior"
    if points >= 150:
        return "Safe-Helper"
    return "Starter"


def update_user_points(user_id, add_points):
    conn = get_db()
    user = conn.execute("SELECT points FROM users WHERE id = ?", (user_id,)).fetchone()
    current_points = user["points"] if user else 0
    new_points = current_points + add_points
    new_rank = rank_from_points(new_points)
    conn.execute("UPDATE users SET points = ?, rank = ? WHERE id = ?", (new_points, new_rank, user_id))
    conn.commit()
    conn.close()


def generate_qr(verification_id):
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(f"MediCycle Verification ID: {verification_id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    filename = f"{verification_id}.png"
    path = os.path.join(CERT_FOLDER, filename)
    img.save(path)
    return filename


def generate_pdf_report(scan):
    filename = f"report_scan_{scan['id']}.pdf"
    path = os.path.join(REPORT_FOLDER, filename)

    c = canvas.Canvas(path, pagesize=A4)
    width, height = A4

    y = height - 50
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, y, "MediCycle AI - Medicine Scan Report")
    y -= 40

    c.setFont("Helvetica", 12)
    lines = [
        f"Scan ID: {scan['id']}",
        f"File Name: {scan['filename']}",
        f"Detected Medicine: {scan['detected_medicine']}",
        f"Category: {scan['category']}",
        f"Risk Level: {scan['risk_level']}",
        f"Confidence: {scan['confidence']}",
        f"Expiry Date: {scan['expiry_date']}",
        f"Recommendation: {scan['recommendation']}",
        f"AMR Advisory: {scan['awareness_message']}",
        f"Points Awarded: {scan['points_awarded']}",
        f"Created At: {scan['created_at']}"
    ]

    for line in lines:
        c.drawString(50, y, line)
        y -= 22

    c.setFont("Helvetica-Oblique", 11)
    c.drawString(50, y - 10, "This report is generated by MediCycle AI for safe medicine disposal guidance.")
    c.save()
    return path


def chatbot_reply(message):
    msg = (message or "").lower()

    if "antibiotic" in msg:
        return "Antibiotics should ideally be returned to a pharmacy or a medical waste collection point. Avoid throwing them in open waste."
    if "expired" in msg:
        return "Expired medicines should not be consumed. If possible, submit disposal proof after safely discarding them through an authorized center."
    if "dispose" in msg or "disposal" in msg:
        return "Recommended disposal methods are pharmacy return, hospital drop-off, or collection bin submission."
    if "amr" in msg:
        return "AMR means Antimicrobial Resistance. Unsafe disposal of antibiotics can contribute to AMR."
    if "points" in msg or "rank" in msg:
        return "You earn points for scans and safe disposal actions. Higher points unlock better eco ranks."
    if "center" in msg or "pharmacy" in msg or "map" in msg:
        return "Open the Nearest Disposal Center page to view nearby mock collection locations on the map."
    return "I can help with medicine disposal, antibiotics, expiry guidance, AMR, proof submission, points, and nearby disposal centers."


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user


@app.context_processor
def inject_user():
    return {"current_user": get_current_user()}


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return wrapper


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not username or not email or not password:
            flash("All fields are required.")
            return redirect(url_for("register"))

        conn = get_db()
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        if existing:
            conn.close()
            flash("Email already registered.")
            return redirect(url_for("register"))

        password_hash = generate_password_hash(password)
        conn.execute(
            "INSERT INTO users (username, email, password_hash, points, rank, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (username, email, password_hash, 0, "Starter", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        conn.close()
        flash("Registration successful. Please login.")
        return redirect(url_for("login"))

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            flash("Login successful.")
            return redirect(url_for("home"))

        flash("Invalid email or password.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out successfully.")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def home():
    user = get_current_user()
    return render_template("index.html", points=user["points"], rank=user["rank"], username=user["username"])


@app.route("/upload", methods=["POST"])
@login_required
def upload_image():
    user = get_current_user()
    file = request.files.get("medicine_image")
    camera_data = request.form.get("camera_data", "")

    filename = None
    upload_path = None

    if file and file.filename and allowed_file(file.filename):
        filename = f"{uuid.uuid4().hex}_{secure_filename(file.filename)}"
        upload_path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(upload_path)

    elif camera_data.startswith("data:image"):
        header, encoded = camera_data.split(",", 1)
        ext = "jpg"
        filename = f"{uuid.uuid4().hex}_camera_capture.{ext}"
        upload_path = os.path.join(UPLOAD_FOLDER, filename)
        with open(upload_path, "wb") as f:
            f.write(base64.b64decode(encoded))

    else:
        flash("Please upload an image or capture one from the camera.")
        return redirect(url_for("home"))

    shutil.copy(upload_path, os.path.join(TEMP_FOLDER, filename))

    try:
        processed_image = preprocess_image(upload_path)
        extracted_text = pytesseract.image_to_string(processed_image)
    except Exception as e:
        extracted_text = f"OCR Error: {str(e)}"

    cleaned_ocr_text = clean_text(extracted_text)

    with open(MED_DB_PATH, "r") as f:
        db = json.load(f)

    antibiotic_list = [m.lower() for m in db["antibiotics"]]
    detected_raw, confidence_level = detect_medicine(cleaned_ocr_text, antibiotic_list)

    detected_medicine = None
    category = "Unknown"
    risk_level = "Low"
    recommendation = "Dispose as normal pharmaceutical waste"
    awareness_message = "Follow safe medicine disposal practices."
    status_title = "Label Unclear"
    confidence_label = "Low"

    if detected_raw:
        detected_medicine = detected_raw.title()
        category = "Antibiotic"
        risk_level = "High"
        recommendation = "Return to Pharmacy or Medical Waste Collection Center"
        awareness_message = "Improper antibiotic disposal may contribute to Antimicrobial Resistance (AMR)."
        status_title = "Antibiotic Identified"
        confidence_label = "High" if confidence_level == "High" else "Medium"
    elif len(cleaned_ocr_text) > 8:
        status_title = "Partial Label Recognition"
        confidence_label = "Medium"

    expiry_date = detect_expiry(extracted_text)
    analysis_message = build_analysis_message(detected_medicine, category, expiry_date, confidence_level)
    raw_ocr_visible = len(cleaned_ocr_text) >= 12 and "ocr error" not in cleaned_ocr_text
    points_awarded = calculate_points(category, risk_level)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO scans (
            user_id, filename, detected_medicine, category, risk_level,
            recommendation, awareness_message, expiry_date, confidence,
            status_title, raw_text, cleaned_text, points_awarded, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user["id"], filename, detected_medicine or "Not detected", category, risk_level,
        recommendation, awareness_message, expiry_date or "Not found", confidence_label,
        status_title, extracted_text, cleaned_ocr_text, points_awarded,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    scan_id = cur.lastrowid
    conn.commit()
    conn.close()

    update_user_points(user["id"], points_awarded)

    return redirect(url_for("result_page", scan_id=scan_id))


@app.route("/result/<int:scan_id>")
@login_required
def result_page(scan_id):
    user = get_current_user()
    conn = get_db()
    scan = conn.execute("SELECT * FROM scans WHERE id = ? AND user_id = ?", (scan_id, user["id"])).fetchone()
    conn.close()

    if not scan:
        flash("Scan not found.")
        return redirect(url_for("home"))

    confidence_class = {
        "High": "confidence-high",
        "Medium": "confidence-medium",
        "Low": "confidence-low"
    }.get(scan["confidence"], "confidence-low")

    risk_class = "risk-high" if scan["risk_level"] == "High" else "risk-low"

    return render_template(
        "result.html",
        scan=scan,
        confidence_class=confidence_class,
        risk_class=risk_class,
        raw_ocr_visible=len(scan["cleaned_text"] or "") >= 12
    )


@app.route("/download_report/<int:scan_id>")
@login_required
def download_report(scan_id):
    user = get_current_user()
    conn = get_db()
    scan = conn.execute("SELECT * FROM scans WHERE id = ? AND user_id = ?", (scan_id, user["id"])).fetchone()
    conn.close()

    if not scan:
        return "Report not found"

    pdf_path = generate_pdf_report(scan)
    return send_file(pdf_path, as_attachment=True)


@app.route("/proof")
@login_required
def proof():
    medicine = request.args.get("medicine", "")
    return render_template("proof.html", prefill_medicine=medicine)


@app.route("/submit_proof", methods=["POST"])
@login_required
def submit_proof():
    user = get_current_user()
    medicine_name = request.form.get("medicine_name", "").strip()
    disposal_method = request.form.get("disposal_method", "").strip()
    proof_file = request.files.get("proof_image")

    if not medicine_name or not disposal_method:
        flash("Medicine name and disposal method are required.")
        return redirect(url_for("proof"))

    proof_filename = "No image uploaded"
    if proof_file and proof_file.filename and allowed_file(proof_file.filename):
        proof_filename = f"{uuid.uuid4().hex}_{secure_filename(proof_file.filename)}"
        proof_path = os.path.join(PROOF_FOLDER, proof_filename)
        proof_file.save(proof_path)

    verification_id = f"MC-{uuid.uuid4().hex[:10].upper()}"
    qr_file = generate_qr(verification_id)

    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO proofs (user_id, medicine_name, disposal_method, proof_image, verification_id, qr_file, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        user["id"], medicine_name, disposal_method, proof_filename,
        verification_id, qr_file, datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    proof_id = cur.lastrowid
    conn.commit()
    conn.close()

    return redirect(url_for("proof_success", proof_id=proof_id))


@app.route("/proof_success/<int:proof_id>")
@login_required
def proof_success(proof_id):
    user = get_current_user()
    conn = get_db()
    proof = conn.execute("SELECT * FROM proofs WHERE id = ? AND user_id = ?", (proof_id, user["id"])).fetchone()
    conn.close()

    if not proof:
        flash("Proof not found.")
        return redirect(url_for("dashboard"))

    return render_template("proof_success.html", proof=proof)


@app.route("/dashboard")
@login_required
def dashboard():
    user = get_current_user()
    conn = get_db()

    total_scans = conn.execute("SELECT COUNT(*) AS c FROM scans WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    antibiotics_detected = conn.execute(
        "SELECT COUNT(*) AS c FROM scans WHERE user_id = ? AND category = 'Antibiotic'", (user["id"],)
    ).fetchone()["c"]
    high_risk_count = conn.execute(
        "SELECT COUNT(*) AS c FROM scans WHERE user_id = ? AND risk_level = 'High'", (user["id"],)
    ).fetchone()["c"]
    proof_submissions = conn.execute("SELECT COUNT(*) AS c FROM proofs WHERE user_id = ?", (user["id"],)).fetchone()["c"]

    recent_scans = conn.execute(
        "SELECT * FROM scans WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)
    ).fetchall()
    recent_proofs = conn.execute(
        "SELECT * FROM proofs WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)
    ).fetchall()

    conn.close()

    safe_count = max(total_scans - high_risk_count, 0)
    antibiotic_percent = int((antibiotics_detected / total_scans) * 100) if total_scans else 0
    proof_percent = int((proof_submissions / total_scans) * 100) if total_scans else 0

    return render_template(
        "dashboard.html",
        total_scans=total_scans,
        antibiotics_detected=antibiotics_detected,
        high_risk_count=high_risk_count,
        proof_submissions=proof_submissions,
        recent_scans=recent_scans,
        recent_proofs=recent_proofs,
        safe_count=safe_count,
        antibiotic_percent=antibiotic_percent,
        proof_percent=proof_percent
    )


@app.route("/centers")
@login_required
def centers():
    return render_template("centers.html", centers=MOCK_CENTERS, centers_json=json.dumps(MOCK_CENTERS))


@app.route("/history")
@login_required
def history():
    user = get_current_user()
    conn = get_db()
    scans = conn.execute("SELECT * FROM scans WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    proofs = conn.execute("SELECT * FROM proofs WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()
    return render_template("history.html", scans=scans, proofs=proofs)


@app.route("/chatbot", methods=["POST"])
@login_required
def chatbot():
    data = request.get_json(silent=True) or {}
    message = data.get("message", "")
    return jsonify({"reply": chatbot_reply(message)})


if __name__ == "__main__":
    init_db()
    app.run(debug=True)

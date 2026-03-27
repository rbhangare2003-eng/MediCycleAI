
import os
import re
import io
import json
import uuid
import sqlite3
from datetime import datetime
from difflib import get_close_matches

import streamlit as st
import pandas as pd
from PIL import Image, ImageFilter, ImageOps
import pytesseract
import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# ---------------------------
# App config
# ---------------------------
st.set_page_config(
    page_title="MediCycle AI",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "medicycle_streamlit.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PROOF_DIR = os.path.join(BASE_DIR, "proofs")
QR_DIR = os.path.join(BASE_DIR, "certificates")
REPORT_DIR = os.path.join(BASE_DIR, "reports")
MED_DB_PATH = os.path.join(BASE_DIR, "medicine_db.json")

for folder in [UPLOAD_DIR, PROOF_DIR, QR_DIR, REPORT_DIR]:
    os.makedirs(folder, exist_ok=True)

DEFAULT_MED_DB = {
    "antibiotics": [
        "amoxicillin", "amoxycillin", "azithromycin", "ciprofloxacin",
        "doxycycline", "cefixime", "metronidazole", "levofloxacin",
        "ofloxacin", "clindamycin", "cephalexin", "ceftriaxone",
        "linezolid", "moxifloxacin", "norfloxacin", "clarithromycin",
        "ampicillin", "co-amoxiclav", "amikacin", "meropenem",
        "imipenem", "rifaximin", "rifampicin", "vancomycin"
    ]
}

MOCK_CENTERS = [
    {"name": "City Pharmacy Return Point", "lat": 18.5308, "lon": 73.8478, "location": "Shivajinagar, Pune", "type": "Pharmacy"},
    {"name": "Green Med Collection Center", "lat": 18.5074, "lon": 73.8077, "location": "Kothrud, Pune", "type": "NGO Collection"},
    {"name": "Metro Hospital Disposal Desk", "lat": 18.5590, "lon": 73.8070, "location": "Aundh, Pune", "type": "Hospital"},
    {"name": "SafeRx Community Drop Box", "lat": 18.4967, "lon": 73.9272, "location": "Hadapsar, Pune", "type": "Community Center"},
]

# ---------------------------
# DB helpers
# ---------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
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
            password TEXT NOT NULL,
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
            created_at TEXT NOT NULL
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
            created_at TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()

def get_user_by_email(email: str):
    conn = get_db()
    row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower().strip(),)).fetchone()
    conn.close()
    return row

def create_user(username: str, email: str, password: str):
    conn = get_db()
    conn.execute(
        "INSERT INTO users (username, email, password, points, rank, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (username.strip(), email.lower().strip(), password, 0, "Starter", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()

def update_user_points(user_id: int, points_to_add: int):
    conn = get_db()
    user = conn.execute("SELECT points FROM users WHERE id = ?", (user_id,)).fetchone()
    current = int(user["points"]) if user else 0
    new_points = current + points_to_add
    new_rank = rank_from_points(new_points)
    conn.execute("UPDATE users SET points = ?, rank = ? WHERE id = ?", (new_points, new_rank, user_id))
    conn.commit()
    conn.close()

def get_user(user_id: int):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return user

# ---------------------------
# Core helpers
# ---------------------------
def ensure_med_db():
    if not os.path.exists(MED_DB_PATH):
        with open(MED_DB_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_MED_DB, f, indent=2)

def load_antibiotics():
    ensure_med_db()
    with open(MED_DB_PATH, "r", encoding="utf-8") as f:
        return [m.lower() for m in json.load(f)["antibiotics"]]

def rank_from_points(points: int) -> str:
    if points >= 500:
        return "Eco-Champion"
    if points >= 300:
        return "Eco-Warrior"
    if points >= 150:
        return "Safe-Helper"
    return "Starter"

def preprocess_image(pil_image: Image.Image) -> Image.Image:
    image = pil_image.convert("L")
    image = ImageOps.autocontrast(image)
    image = image.filter(ImageFilter.SHARPEN)
    return image

def clean_text(text: str) -> str:
    text = text.lower()
    text = text.replace("\n", " ")
    text = re.sub(r"[^a-z0-9/\- ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def detect_expiry(text: str):
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
    return "Not found"

def detect_medicine(cleaned_text: str, antibiotic_list: list[str]):
    for med in antibiotic_list:
        if med in cleaned_text:
            return med.title(), "High"
    words = cleaned_text.split()
    for word in words:
        matches = get_close_matches(word, antibiotic_list, n=1, cutoff=0.82)
        if matches:
            return matches[0].title(), "Medium"
    return "Not detected", "Low"

def compute_risk(detected_medicine: str, confidence: str, expiry_date: str):
    if detected_medicine != "Not detected":
        risk_score = 85 if confidence == "High" else 65
        category = "Antibiotic"
        risk_level = "High"
        recommendation = "Return to Pharmacy or Medical Waste Collection Center"
        advisory = "Improper antibiotic disposal may contribute to Antimicrobial Resistance (AMR)."
    else:
        risk_score = 25 if confidence == "Medium" else 10
        category = "Unknown"
        risk_level = "Low"
        recommendation = "Dispose as normal pharmaceutical waste only after verification."
        advisory = "Follow safe medicine disposal practices."

    if expiry_date != "Not found":
        risk_score = min(100, risk_score + 10)

    return {
        "risk_score": risk_score,
        "category": category,
        "risk_level": risk_level,
        "recommendation": recommendation,
        "advisory": advisory,
    }

def points_for_scan(category: str, risk_level: str) -> int:
    if category == "Antibiotic" and risk_level == "High":
        return 100
    if category == "Antibiotic":
        return 70
    return 40

def save_uploaded_file(uploaded_file, folder: str, prefix: str = ""):
    ext = os.path.splitext(uploaded_file.name)[1].lower() or ".jpg"
    filename = f"{prefix}{uuid.uuid4().hex}{ext}"
    path = os.path.join(folder, filename)
    with open(path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return filename, path

def save_pil_image(pil_image: Image.Image, folder: str, prefix: str = ""):
    filename = f"{prefix}{uuid.uuid4().hex}.jpg"
    path = os.path.join(folder, filename)
    pil_image.save(path, format="JPEG")
    return filename, path

def insert_scan(user_id: int, record: dict):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO scans (
            user_id, filename, detected_medicine, category, risk_level,
            recommendation, awareness_message, expiry_date, confidence,
            status_title, raw_text, cleaned_text, points_awarded, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        record["filename"],
        record["detected_medicine"],
        record["category"],
        record["risk_level"],
        record["recommendation"],
        record["awareness_message"],
        record["expiry_date"],
        record["confidence"],
        record["status_title"],
        record["raw_text"],
        record["cleaned_text"],
        record["points_awarded"],
        record["created_at"]
    ))
    scan_id = cur.lastrowid
    conn.commit()
    conn.close()
    return scan_id

def insert_proof(user_id: int, record: dict):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO proofs (user_id, medicine_name, disposal_method, proof_image, verification_id, qr_file, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        record["medicine_name"],
        record["disposal_method"],
        record["proof_image"],
        record["verification_id"],
        record["qr_file"],
        record["created_at"]
    ))
    proof_id = cur.lastrowid
    conn.commit()
    conn.close()
    return proof_id

def get_scan(scan_id: int, user_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM scans WHERE id = ? AND user_id = ?", (scan_id, user_id)).fetchone()
    conn.close()
    return row

def generate_qr(verification_id: str):
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(f"MediCycle Verification ID: {verification_id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    filename = f"{verification_id}.png"
    path = os.path.join(QR_DIR, filename)
    img.save(path)
    return filename, path

def generate_pdf_bytes(scan_row):
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    y = height - 50

    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, y, "MediCycle AI - Medicine Scan Report")
    y -= 40

    c.setFont("Helvetica", 12)
    lines = [
        f"Scan ID: {scan_row['id']}",
        f"File Name: {scan_row['filename']}",
        f"Detected Medicine: {scan_row['detected_medicine']}",
        f"Category: {scan_row['category']}",
        f"Risk Level: {scan_row['risk_level']}",
        f"Confidence: {scan_row['confidence']}",
        f"Expiry Date: {scan_row['expiry_date']}",
        f"Recommendation: {scan_row['recommendation']}",
        f"AMR Advisory: {scan_row['awareness_message']}",
        f"Points Awarded: {scan_row['points_awarded']}",
        f"Created At: {scan_row['created_at']}",
    ]
    for line in lines:
        c.drawString(50, y, line)
        y -= 22

    c.setFont("Helvetica-Oblique", 11)
    c.drawString(50, y - 8, "Generated by MediCycle AI for safe medicine disposal guidance.")
    c.save()
    buffer.seek(0)
    return buffer

def chatbot_reply(message: str) -> str:
    msg = (message or "").lower()
    if "antibiotic" in msg:
        return "Antibiotics should ideally be returned to a pharmacy or an authorized medical waste collection point."
    if "expired" in msg:
        return "Expired medicines should not be consumed. Please dispose of them safely and upload proof if possible."
    if "dispose" in msg or "disposal" in msg:
        return "Recommended disposal methods are pharmacy return, hospital drop-off, or collection bin submission."
    if "amr" in msg:
        return "AMR means Antimicrobial Resistance. Unsafe disposal of antibiotics can contribute to AMR."
    if "points" in msg or "rank" in msg:
        return "You earn points for scans and safe disposal actions. Higher points unlock better eco ranks."
    if "center" in msg or "pharmacy" in msg or "map" in msg:
        return "Open the Disposal Centers page to view nearby mock collection locations on the map."
    return "I can help with medicine disposal, antibiotics, expiry guidance, AMR, proof submission, points, and nearby disposal centers."

def apply_theme():
    st.markdown("""
    <style>
    .stApp {
        background: linear-gradient(135deg, #eef2ff, #f0fdf4, #ecfeff);
    }
    .mc-card {
        background: rgba(255,255,255,0.94);
        border-radius: 18px;
        padding: 1rem 1.2rem;
        box-shadow: 0 10px 24px rgba(15,23,42,0.08);
        margin-bottom: 1rem;
    }
    .mc-badge {
        display:inline-block;
        padding:0.25rem 0.7rem;
        border-radius:999px;
        font-weight:700;
        font-size:0.82rem;
        margin-right:0.4rem;
    }
    .mc-high {background:#fee2e2;color:#991b1b;}
    .mc-med {background:#fef3c7;color:#92400e;}
    .mc-low {background:#dcfce7;color:#166534;}
    .mc-header {
        background: rgba(255,255,255,0.95);
        border-radius: 16px;
        padding: 0.9rem 1rem;
        box-shadow: 0 8px 20px rgba(15,23,42,0.06);
        margin-bottom: 1rem;
    }
    </style>
    """, unsafe_allow_html=True)

def sidebar_user_panel(user):
    st.sidebar.markdown("## 👤 User Panel")
    st.sidebar.success(f"{user['username']} | {user['rank']}")
    st.sidebar.info(f"⭐ Points: {user['points']}")
    page = st.sidebar.radio(
        "Navigation",
        ["Home", "Result", "Proof Submission", "Dashboard", "Centers Map", "History", "Chat Assistant"]
    )
    if st.sidebar.button("Logout"):
        for key in ["user_id", "last_scan_id"]:
            if key in st.session_state:
                del st.session_state[key]
        st.rerun()
    return page

def login_register_ui():
    st.title("MediCycle AI")
    st.caption("Streamlit cloud-ready version for safe medicine disposal")
    tab1, tab2 = st.tabs(["Login", "Register"])

    with tab1:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            login_submit = st.form_submit_button("Login")
        if login_submit:
            user = get_user_by_email(email)
            if user and user["password"] == password:
                st.session_state["user_id"] = user["id"]
                st.success("Login successful")
                st.rerun()
            else:
                st.error("Invalid email or password")

    with tab2:
        with st.form("register_form"):
            username = st.text_input("Username")
            email_r = st.text_input("Email", key="reg_email")
            password_r = st.text_input("Password", type="password", key="reg_pw")
            register_submit = st.form_submit_button("Create Account")
        if register_submit:
            if not username or not email_r or not password_r:
                st.error("All fields are required")
            elif get_user_by_email(email_r):
                st.error("Email already exists")
            else:
                create_user(username, email_r, password_r)
                st.success("Registration successful. Please login.")

def home_page(user):
    st.markdown('<div class="mc-header"><h2 style="margin:0;">💊 MediCycle AI</h2><p style="margin:0.2rem 0 0 0;">Upload or capture a medicine image and get safe disposal guidance.</p></div>', unsafe_allow_html=True)

    uploaded = st.file_uploader("Upload medicine image", type=["png", "jpg", "jpeg"])
    camera = st.camera_input("Or capture from camera")

    col1, col2 = st.columns(2)
    with col1:
        st.page_link("app", label="📊 Go to dashboard", icon="📊")
    with col2:
        st.page_link("app", label="📍 Find disposal centers", icon="📍")

    source_image = None
    source_name = None

    if camera is not None:
        source_image = Image.open(camera)
        source_name = f"camera_{uuid.uuid4().hex[:8]}.jpg"
    elif uploaded is not None:
        source_image = Image.open(uploaded)
        source_name = uploaded.name

    if source_image is not None:
        st.image(source_image, caption="Selected image", width=350)

        if st.button("Analyze Medicine", type="primary", use_container_width=True):
            filename, saved_path = save_pil_image(source_image.convert("RGB"), UPLOAD_DIR, prefix="scan_")

            processed = preprocess_image(source_image)
            extracted_text = pytesseract.image_to_string(processed)
            cleaned = clean_text(extracted_text)

            antibiotics = load_antibiotics()
            detected_medicine, confidence = detect_medicine(cleaned, antibiotics)
            expiry_date = detect_expiry(extracted_text)
            risk_bundle = compute_risk(detected_medicine, confidence, expiry_date)

            points = points_for_scan(risk_bundle["category"], risk_bundle["risk_level"])

            status_title = "Antibiotic Identified" if detected_medicine != "Not detected" else ("Partial Label Recognition" if len(cleaned) > 8 else "Label Unclear")

            record = {
                "filename": filename,
                "detected_medicine": detected_medicine,
                "category": risk_bundle["category"],
                "risk_level": risk_bundle["risk_level"],
                "recommendation": risk_bundle["recommendation"],
                "awareness_message": risk_bundle["advisory"],
                "expiry_date": expiry_date,
                "confidence": confidence,
                "status_title": status_title,
                "raw_text": extracted_text,
                "cleaned_text": cleaned,
                "points_awarded": points,
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }

            scan_id = insert_scan(user["id"], record)
            update_user_points(user["id"], points)
            st.session_state["last_scan_id"] = scan_id
            st.success("Analysis complete.")
            st.rerun()

def result_page(user):
    scan_id = st.session_state.get("last_scan_id")
    if not scan_id:
        st.info("No recent scan found. Please analyze a medicine first.")
        return

    scan = get_scan(scan_id, user["id"])
    if not scan:
        st.warning("Recent scan not available.")
        return

    image_path = os.path.join(UPLOAD_DIR, scan["filename"])

    st.markdown('<div class="mc-header"><h2 style="margin:0;">🧠 Medicine Analysis Result</h2></div>', unsafe_allow_html=True)
    col1, col2 = st.columns([1, 1])

    with col1:
        st.markdown('<div class="mc-card">', unsafe_allow_html=True)
        st.subheader("Uploaded Image")
        if os.path.exists(image_path):
            st.image(image_path, width=320)
        st.write(f"**File:** {scan['filename']}")
        st.write(f"**Status:** {scan['status_title']}")
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="mc-card">', unsafe_allow_html=True)
        st.subheader("Smart Summary")

        conf_class = "mc-high" if scan["confidence"] == "High" else ("mc-med" if scan["confidence"] == "Medium" else "mc-low")
        risk_class = "mc-high" if scan["risk_level"] == "High" else "mc-low"

        st.markdown(f'<span class="mc-badge {conf_class}">Confidence: {scan["confidence"]}</span>', unsafe_allow_html=True)
        st.markdown(f'<span class="mc-badge {risk_class}">Risk: {scan["risk_level"]}</span>', unsafe_allow_html=True)

        st.write(f"**Medicine:** {scan['detected_medicine']}")
        st.write(f"**Category:** {scan['category']}")
        st.write(f"**Expiry:** {scan['expiry_date']}")
        st.write(f"**Recommendation:** {scan['recommendation']}")
        st.write(f"**Points Awarded:** {scan['points_awarded']}")

        st.progress(int(scan["points_awarded"]) / 100.0)
        st.caption("Action reward score")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="mc-card">', unsafe_allow_html=True)
    st.subheader("AMR Advisory")
    st.write(scan["awareness_message"])
    st.markdown('</div>', unsafe_allow_html=True)

    with st.expander("Show OCR details"):
        st.text_area("Extracted OCR Text", value=scan["raw_text"], height=150)
        st.text_area("Processed OCR Text", value=scan["cleaned_text"], height=120)

    pdf_bytes = generate_pdf_bytes(scan)
    st.download_button(
        "⬇️ Download PDF Report",
        data=pdf_bytes,
        file_name=f"medicycle_report_scan_{scan['id']}.pdf",
        mime="application/pdf",
        use_container_width=True
    )

def proof_page(user):
    st.markdown('<div class="mc-header"><h2 style="margin:0;">✅ Proof Submission</h2></div>', unsafe_allow_html=True)
    prefill = ""
    scan_id = st.session_state.get("last_scan_id")
    if scan_id:
        scan = get_scan(scan_id, user["id"])
        if scan:
            prefill = scan["detected_medicine"]

    with st.form("proof_form"):
        med_name = st.text_input("Medicine Name", value=prefill)
        method = st.selectbox("Disposal Method", ["Returned to Pharmacy", "Disposed in Collection Bin", "Hospital Drop-off"])
        proof_image = st.file_uploader("Upload Proof Image", type=["png", "jpg", "jpeg"], key="proof_uploader")
        submit = st.form_submit_button("Submit Proof", use_container_width=True)

    if submit:
        proof_filename = "No image uploaded"
        if proof_image is not None:
            proof_filename, _ = save_uploaded_file(proof_image, PROOF_DIR, prefix="proof_")

        verification_id = f"MC-{uuid.uuid4().hex[:10].upper()}"
        qr_file, qr_path = generate_qr(verification_id)

        record = {
            "medicine_name": med_name,
            "disposal_method": method,
            "proof_image": proof_filename,
            "verification_id": verification_id,
            "qr_file": qr_file,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        proof_id = insert_proof(user["id"], record)
        st.success("Proof submitted successfully.")
        st.markdown('<div class="mc-card">', unsafe_allow_html=True)
        st.write(f"**Verification ID:** {verification_id}")
        st.image(qr_path, width=180)
        st.markdown('</div>', unsafe_allow_html=True)

def dashboard_page(user):
    conn = get_db()
    total_scans = conn.execute("SELECT COUNT(*) c FROM scans WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    antibiotics_detected = conn.execute("SELECT COUNT(*) c FROM scans WHERE user_id = ? AND category = 'Antibiotic'", (user["id"],)).fetchone()["c"]
    high_risk_count = conn.execute("SELECT COUNT(*) c FROM scans WHERE user_id = ? AND risk_level = 'High'", (user["id"],)).fetchone()["c"]
    proof_submissions = conn.execute("SELECT COUNT(*) c FROM proofs WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    recent_scans = conn.execute("SELECT * FROM scans WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    recent_proofs = conn.execute("SELECT * FROM proofs WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    conn.close()

    safe_count = max(total_scans - high_risk_count, 0)
    antibiotic_percent = int((antibiotics_detected / total_scans) * 100) if total_scans else 0
    proof_percent = int((proof_submissions / total_scans) * 100) if total_scans else 0

    st.markdown('<div class="mc-header"><h2 style="margin:0;">📊 Dashboard</h2></div>', unsafe_allow_html=True)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Scans", total_scans)
    c2.metric("Antibiotics Detected", antibiotics_detected)
    c3.metric("High Risk Cases", high_risk_count)
    c4.metric("Proof Submissions", proof_submissions)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown('<div class="mc-card">', unsafe_allow_html=True)
        st.subheader("Detection Progress")
        st.write(f"Antibiotic Detection Rate: {antibiotic_percent}%")
        st.progress(antibiotic_percent / 100 if antibiotic_percent else 0)
        st.write(f"Proof Submission Rate: {proof_percent}%")
        st.progress(proof_percent / 100 if proof_percent else 0)
        st.markdown('</div>', unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="mc-card">', unsafe_allow_html=True)
        st.subheader("Risk Distribution")
        chart_df = pd.DataFrame({
            "Category": ["High Risk", "Low Risk / Other"],
            "Count": [high_risk_count, safe_count]
        })
        st.bar_chart(chart_df.set_index("Category"))
        st.markdown('</div>', unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        st.markdown('<div class="mc-card">', unsafe_allow_html=True)
        st.subheader("Recent Scan Records")
        if recent_scans:
            for scan in recent_scans:
                st.write(f"**{scan['filename']}** | {scan['detected_medicine']} | {scan['risk_level']} | {scan['created_at']}")
        else:
            st.info("No scan records yet.")
        st.markdown('</div>', unsafe_allow_html=True)

    with right:
        st.markdown('<div class="mc-card">', unsafe_allow_html=True)
        st.subheader("Recent Proof Records")
        if recent_proofs:
            for proof in recent_proofs:
                st.write(f"**{proof['medicine_name']}** | {proof['disposal_method']} | {proof['verification_id']}")
        else:
            st.info("No proof records yet.")
        st.markdown('</div>', unsafe_allow_html=True)

def centers_page():
    st.markdown('<div class="mc-header"><h2 style="margin:0;">📍 Disposal Centers Map</h2></div>', unsafe_allow_html=True)
    df = pd.DataFrame(MOCK_CENTERS)
    st.map(df.rename(columns={"lat": "lat", "lon": "lon"})[["lat", "lon"]], size=140)
    st.dataframe(df[["name", "location", "type"]], use_container_width=True)

def history_page(user):
    conn = get_db()
    scans = pd.read_sql_query("SELECT * FROM scans WHERE user_id = ? ORDER BY id DESC", conn, params=(user["id"],))
    proofs = pd.read_sql_query("SELECT * FROM proofs WHERE user_id = ? ORDER BY id DESC", conn, params=(user["id"],))
    conn.close()

    st.markdown('<div class="mc-header"><h2 style="margin:0;">🕘 Full History</h2></div>', unsafe_allow_html=True)
    tab1, tab2 = st.tabs(["Scan History", "Proof History"])

    with tab1:
        if not scans.empty:
            st.dataframe(scans[["id", "filename", "detected_medicine", "category", "risk_level", "confidence", "expiry_date", "points_awarded", "created_at"]], use_container_width=True)
        else:
            st.info("No scan history yet.")

    with tab2:
        if not proofs.empty:
            st.dataframe(proofs[["id", "medicine_name", "disposal_method", "verification_id", "created_at"]], use_container_width=True)
        else:
            st.info("No proof history yet.")

def chat_page():
    st.markdown('<div class="mc-header"><h2 style="margin:0;">💬 Chat Assistant</h2></div>', unsafe_allow_html=True)
    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    for role, text in st.session_state.chat_history:
        if role == "user":
            st.chat_message("user").write(text)
        else:
            st.chat_message("assistant").write(text)

    prompt = st.chat_input("Ask about disposal, antibiotics, AMR, points, or centers...")
    if prompt:
        st.session_state.chat_history.append(("user", prompt))
        reply = chatbot_reply(prompt)
        st.session_state.chat_history.append(("assistant", reply))
        st.rerun()

# ---------------------------
# main
# ---------------------------
init_db()
ensure_med_db()
apply_theme()

if "user_id" not in st.session_state:
    login_register_ui()
else:
    user = get_user(st.session_state["user_id"])
    if not user:
        del st.session_state["user_id"]
        st.rerun()
    page = sidebar_user_panel(user)

    if page == "Home":
        home_page(user)
    elif page == "Result":
        result_page(user)
    elif page == "Proof Submission":
        proof_page(user)
    elif page == "Dashboard":
        dashboard_page(user)
    elif page == "Centers Map":
        centers_page()
    elif page == "History":
        history_page(user)
    elif page == "Chat Assistant":
        chat_page()

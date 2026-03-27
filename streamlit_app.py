import streamlit as st
import sqlite3
import os
import io
import re
import json
import uuid
from datetime import datetime
from difflib import get_close_matches

from PIL import Image, ImageFilter, ImageOps
import pytesseract
import qrcode
from werkzeug.security import generate_password_hash, check_password_hash
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# -------------------- CONFIG --------------------
st.set_page_config(
    page_title="MediCycle AI",
    page_icon="💊",
    layout="wide"
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "medicycle_streamlit.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PROOF_DIR = os.path.join(BASE_DIR, "proofs")
QR_DIR = os.path.join(BASE_DIR, "qr_codes")
REPORT_DIR = os.path.join(BASE_DIR, "reports")
MEDICINE_DB_PATH = os.path.join(BASE_DIR, "medicine_db.json")

for folder in [UPLOAD_DIR, PROOF_DIR, QR_DIR, REPORT_DIR]:
    os.makedirs(folder, exist_ok=True)

# -------------------- DEFAULT MEDICINE DB --------------------
DEFAULT_MED_DB = {
    "antibiotics": [
        "amoxicillin",
        "amoxycillin",
        "azithromycin",
        "ciprofloxacin",
        "doxycycline",
        "cefixime",
        "metronidazole",
        "levofloxacin",
        "ofloxacin",
        "clindamycin",
        "cephalexin",
        "ceftriaxone",
        "linezolid",
        "moxifloxacin",
        "norfloxacin",
        "clarithromycin",
        "ampicillin",
        "co-amoxiclav",
        "amikacin",
        "meropenem",
        "imipenem",
        "rifaximin",
        "rifampicin",
        "vancomycin"
    ]
}

if not os.path.exists(MEDICINE_DB_PATH):
    with open(MEDICINE_DB_PATH, "w") as f:
        json.dump(DEFAULT_MED_DB, f, indent=4)

# -------------------- CSS --------------------
st.markdown("""
<style>
.main-title {
    font-size: 2.8rem;
    font-weight: 700;
    text-align: center;
    margin-bottom: 0.2rem;
}
.sub-title {
    text-align: center;
    color: #666;
    margin-bottom: 1.5rem;
}
.metric-card {
    padding: 1rem;
    border-radius: 16px;
    color: white;
    text-align: center;
    font-weight: 700;
}
.bg1 {background: linear-gradient(135deg, #4f46e5, #7c3aed);}
.bg2 {background: linear-gradient(135deg, #0ea5e9, #2563eb);}
.bg3 {background: linear-gradient(135deg, #f97316, #ef4444);}
.bg4 {background: linear-gradient(135deg, #10b981, #059669);}
.small-note {
    font-size: 0.9rem;
    color: #666;
}
.rank-pill {
    display:inline-block;
    padding: 6px 12px;
    border-radius: 999px;
    background:#06b6d4;
    color:white;
    font-weight:700;
}
.block {
    padding: 1rem;
    border: 1px solid #e5e7eb;
    border-radius: 16px;
    margin-bottom: 1rem;
}
</style>
""", unsafe_allow_html=True)

# -------------------- DB --------------------
def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
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

    conn.execute("""
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

    conn.execute("""
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

    # FIXED: duplicate insert problem solved
    conn.execute(
        "INSERT OR IGNORE INTO users (username,email,password_hash,points,rank,created_at) VALUES (?,?,?,?,?,?)",
        (
            "Demo User",
            "demo@medicycle.ai",
            generate_password_hash("demo123"),
            200,
            "Safe-Helper",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )

    conn.commit()
    conn.close()


init_db()

# -------------------- HELPERS --------------------
def load_medicine_db():
    with open(MEDICINE_DB_PATH, "r") as f:
        return json.load(f)


def preprocess_image(pil_image):
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
    expiry_patterns = [
        r"\b\d{2}/\d{2}/\d{4}\b",
        r"\b\d{2}-\d{2}-\d{4}\b",
        r"\b\d{2}/\d{4}\b",
        r"\b\d{2}-\d{4}\b"
    ]
    for pattern in expiry_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group()
    return None


def detect_medicine(cleaned_text: str, antibiotic_list):
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
    path = os.path.join(QR_DIR, filename)
    img.save(path)
    return path, filename


def generate_pdf_report(scan):
    filename = f"report_scan_{scan['id']}.pdf"
    path = os.path.join(REPORT_DIR, filename)

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
    c.drawString(50, y - 10, "Generated by MediCycle AI.")
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
        return "Check the Disposal Centers section to view nearby mock collection locations."
    return "I can help with medicine disposal, antibiotics, expiry guidance, AMR, proof submission, points, and disposal centers."


MOCK_CENTERS = [
    {"name": "City Pharmacy Return Point", "location": "Shivajinagar, Pune", "type": "Pharmacy", "lat": 18.5308, "lng": 73.8478},
    {"name": "Green Med Collection Center", "location": "Kothrud, Pune", "type": "NGO Collection", "lat": 18.5074, "lng": 73.8077},
    {"name": "Metro Hospital Disposal Desk", "location": "Aundh, Pune", "type": "Hospital", "lat": 18.5590, "lng": 73.8070},
    {"name": "SafeRx Community Drop Box", "location": "Hadapsar, Pune", "type": "Community Center", "lat": 18.4967, "lng": 73.9272}
]

# -------------------- AUTH --------------------
def login_user(email, password):
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    conn.close()

    if user and check_password_hash(user["password_hash"], password):
        st.session_state.user_id = user["id"]
        st.session_state.user_email = user["email"]
        st.session_state.username = user["username"]
        st.session_state.logged_in = True
        return True
    return False


def register_user(username, email, password):
    conn = get_db()
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if existing:
        conn.close()
        return False, "Email already registered."

    conn.execute(
        "INSERT INTO users (username,email,password_hash,points,rank,created_at) VALUES (?,?,?,?,?,?)",
        (
            username.strip(),
            email.strip().lower(),
            generate_password_hash(password),
            0,
            "Starter",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
    )
    conn.commit()
    conn.close()
    return True, "Registration successful."


def reset_password(email, new_password):
    conn = get_db()
    user = conn.execute("SELECT id FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    if not user:
        conn.close()
        return False, "Email not found."

    conn.execute(
        "UPDATE users SET password_hash = ? WHERE email = ?",
        (generate_password_hash(new_password), email.strip().lower())
    )
    conn.commit()
    conn.close()
    return True, "Password reset successful."


def current_user():
    if not st.session_state.get("logged_in"):
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (st.session_state.user_id,)).fetchone()
    conn.close()
    return user


# -------------------- SESSION INIT --------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "last_scan_id" not in st.session_state:
    st.session_state.last_scan_id = None

# -------------------- LOGIN PAGE --------------------
if not st.session_state.logged_in:
    st.markdown("<div class='main-title'>MediCycle AI</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>Streamlit Cloud-ready version for safe medicine disposal</div>", unsafe_allow_html=True)

    action = st.radio("Choose Action", ["Login", "Register", "Reset Password"], horizontal=True)

    if action == "Login":
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")

            if submitted:
                if login_user(email, password):
                    st.success("Login successful.")
                    st.rerun()
                else:
                    st.error("Invalid email or password.")

        st.info("Demo account → Email: demo@medicycle.ai | Password: demo123")

    elif action == "Register":
        with st.form("register_form"):
            username = st.text_input("Username")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Register")

            if submitted:
                ok, msg = register_user(username, email, password)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    elif action == "Reset Password":
        with st.form("reset_form"):
            email = st.text_input("Registered Email")
            new_password = st.text_input("New Password", type="password")
            submitted = st.form_submit_button("Reset Password")

            if submitted:
                ok, msg = reset_password(email, new_password)
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)

    st.stop()

# -------------------- USER HEADER --------------------
user = current_user()
if user is None:
    st.session_state.logged_in = False
    st.rerun()

col1, col2, col3 = st.columns([3, 2, 1])
with col1:
    st.markdown(f"### 👤 {user['username']}")
with col2:
    st.markdown(f"<span class='rank-pill'>{user['rank']}</span>", unsafe_allow_html=True)
with col3:
    if st.button("Logout"):
        st.session_state.clear()
        st.rerun()

st.markdown(f"**⭐ {user['points']} Points**")

# -------------------- SIDEBAR --------------------
page = st.sidebar.radio(
    "Navigation",
    ["Home", "Dashboard", "Proof Submission", "Disposal Centers", "History", "Chatbot"]
)

# -------------------- HOME --------------------
if page == "Home":
    st.markdown("<div class='main-title'>MediCycle AI</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>Upload or capture a medicine image and get safe disposal guidance.</div>", unsafe_allow_html=True)

    input_mode = st.radio("Choose input mode", ["Upload Image", "Use Camera"], horizontal=True)

    uploaded_image = None
    filename = None

    if input_mode == "Upload Image":
        uploaded_file = st.file_uploader("Upload medicine image", type=["png", "jpg", "jpeg"])
        if uploaded_file:
            uploaded_image = Image.open(uploaded_file)
            filename = uploaded_file.name
    else:
        cam_file = st.camera_input("Capture medicine image")
        if cam_file:
            uploaded_image = Image.open(cam_file)
            filename = f"camera_capture_{uuid.uuid4().hex[:8]}.jpg"

    if uploaded_image:
        st.image(uploaded_image, caption="Selected Image", width=320)

        if st.button("Analyze Medicine"):
            save_name = f"{uuid.uuid4().hex}_{filename}"
            upload_path = os.path.join(UPLOAD_DIR, save_name)
            uploaded_image.save(upload_path)

            processed_image = preprocess_image(uploaded_image)
            extracted_text = pytesseract.image_to_string(processed_image)
            cleaned_ocr_text = clean_text(extracted_text)

            med_db = load_medicine_db()
            antibiotic_list = [m.lower() for m in med_db["antibiotics"]]
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

            expiry_date = detect_expiry(extracted_text) or "Not found"
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
                user["id"],
                save_name,
                detected_medicine or "Not detected",
                category,
                risk_level,
                recommendation,
                awareness_message,
                expiry_date,
                confidence_label,
                status_title,
                extracted_text,
                cleaned_ocr_text,
                points_awarded,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            scan_id = cur.lastrowid
            conn.commit()
            conn.close()

            update_user_points(user["id"], points_awarded)
            st.session_state.last_scan_id = scan_id
            st.success("Analysis completed successfully.")
            st.rerun()

    if st.session_state.last_scan_id:
        conn = get_db()
        scan = conn.execute("SELECT * FROM scans WHERE id = ? AND user_id = ?", (st.session_state.last_scan_id, user["id"])).fetchone()
        conn.close()

        if scan:
            st.markdown("---")
            st.subheader("Latest Scan Result")

            c1, c2 = st.columns(2)
            with c1:
                img_path = os.path.join(UPLOAD_DIR, scan["filename"])
                if os.path.exists(img_path):
                    st.image(img_path, caption=scan["filename"], width=320)

            with c2:
                st.write(f"**Medicine:** {scan['detected_medicine']}")
                st.write(f"**Category:** {scan['category']}")
                st.write(f"**Risk Level:** {scan['risk_level']}")
                st.write(f"**Confidence:** {scan['confidence']}")
                st.write(f"**Expiry:** {scan['expiry_date']}")
                st.write(f"**Recommendation:** {scan['recommendation']}")
                st.info(scan["awareness_message"])
                st.success(f"Points Awarded: {scan['points_awarded']}")

            with st.expander("Show Technical OCR Details"):
                st.text_area("Extracted OCR Text", scan["raw_text"], height=140)
                st.text_area("Processed OCR Text", scan["cleaned_text"], height=140)

            pdf_path = generate_pdf_report(scan)
            with open(pdf_path, "rb") as f:
                st.download_button(
                    "Download PDF Report",
                    f,
                    file_name=os.path.basename(pdf_path),
                    mime="application/pdf"
                )

# -------------------- DASHBOARD --------------------
elif page == "Dashboard":
    conn = get_db()
    total_scans = conn.execute("SELECT COUNT(*) AS c FROM scans WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    antibiotics_detected = conn.execute("SELECT COUNT(*) AS c FROM scans WHERE user_id = ? AND category = 'Antibiotic'", (user["id"],)).fetchone()["c"]
    high_risk_count = conn.execute("SELECT COUNT(*) AS c FROM scans WHERE user_id = ? AND risk_level = 'High'", (user["id"],)).fetchone()["c"]
    proof_submissions = conn.execute("SELECT COUNT(*) AS c FROM proofs WHERE user_id = ?", (user["id"],)).fetchone()["c"]
    recent_scans = conn.execute("SELECT * FROM scans WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    recent_proofs = conn.execute("SELECT * FROM proofs WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    conn.close()

    safe_count = max(total_scans - high_risk_count, 0)
    antibiotic_percent = int((antibiotics_detected / total_scans) * 100) if total_scans else 0
    proof_percent = int((proof_submissions / total_scans) * 100) if total_scans else 0

    st.markdown("<div class='main-title'>Admin Dashboard</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>Real-time stats, recent records, and disposal analytics.</div>", unsafe_allow_html=True)

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown(f"<div class='metric-card bg1'>📷<br>Total Scans<br><h2>{total_scans}</h2></div>", unsafe_allow_html=True)
    c2.markdown(f"<div class='metric-card bg2'>💊<br>Antibiotics Detected<br><h2>{antibiotics_detected}</h2></div>", unsafe_allow_html=True)
    c3.markdown(f"<div class='metric-card bg3'>⚠️<br>High Risk Cases<br><h2>{high_risk_count}</h2></div>", unsafe_allow_html=True)
    c4.markdown(f"<div class='metric-card bg4'>✅<br>Proof Submissions<br><h2>{proof_submissions}</h2></div>", unsafe_allow_html=True)

    st.markdown("### Detection Progress")
    st.progress(antibiotic_percent / 100 if antibiotic_percent else 0, text=f"Antibiotic Detection Rate: {antibiotic_percent}%")
    st.progress(proof_percent / 100 if proof_percent else 0, text=f"Proof Submission Rate: {proof_percent}%")

    st.markdown("### Risk Distribution")
    st.write(f"High Risk: {high_risk_count} | Low Risk / Other: {safe_count}")

    colA, colB = st.columns(2)

    with colA:
        st.subheader("Recent Scan Records")
        if recent_scans:
            for scan in recent_scans:
                with st.container(border=True):
                    st.write(f"**File:** {scan['filename']}")
                    st.write(f"**Medicine:** {scan['detected_medicine']}")
                    st.write(f"**Category:** {scan['category']}")
                    st.write(f"**Risk:** {scan['risk_level']}")
                    st.write(f"**Confidence:** {scan['confidence']}")
                    st.write(f"**Points:** {scan['points_awarded']}")
                    st.write(f"**Time:** {scan['created_at']}")
        else:
            st.info("No scan records yet.")

    with colB:
        st.subheader("Recent Proof Records")
        if recent_proofs:
            for proof in recent_proofs:
                with st.container(border=True):
                    st.write(f"**Medicine:** {proof['medicine_name']}")
                    st.write(f"**Method:** {proof['disposal_method']}")
                    st.write(f"**Verification ID:** {proof['verification_id']}")
                    st.write(f"**Time:** {proof['created_at']}")
        else:
            st.info("No proof records yet.")

# -------------------- PROOF --------------------
elif page == "Proof Submission":
    st.markdown("<div class='main-title'>Proof Submission</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>Upload proof after safely disposing the medicine.</div>", unsafe_allow_html=True)

    with st.form("proof_form"):
        medicine_name = st.text_input("Medicine Name")
        disposal_method = st.selectbox("Disposal Method", [
            "Returned to Pharmacy",
            "Disposed in Collection Bin",
            "Hospital Drop-off"
        ])
        proof_image = st.file_uploader("Upload Proof Image", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("Submit Proof")

    if submitted:
        if not medicine_name:
            st.error("Medicine name is required.")
        else:
            proof_filename = "No image uploaded"
            if proof_image:
                proof_filename = f"{uuid.uuid4().hex}_{proof_image.name}"
                proof_path = os.path.join(PROOF_DIR, proof_filename)
                with open(proof_path, "wb") as f:
                    f.write(proof_image.read())

            verification_id = f"MC-{uuid.uuid4().hex[:10].upper()}"
            qr_path, qr_file = generate_qr(verification_id)

            conn = get_db()
            conn.execute("""
                INSERT INTO proofs (user_id, medicine_name, disposal_method, proof_image, verification_id, qr_file, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                user["id"],
                medicine_name,
                disposal_method,
                proof_filename,
                verification_id,
                qr_file,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            conn.commit()
            conn.close()

            st.success("Proof submitted successfully.")
            st.write(f"**Verification ID:** {verification_id}")
            st.image(qr_path, caption="QR Verification Certificate", width=200)

# -------------------- CENTERS --------------------
elif page == "Disposal Centers":
    st.markdown("<div class='main-title'>Nearest Disposal Centers</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>Nearby pharmacy, NGO, and hospital drop-off points.</div>", unsafe_allow_html=True)

    st.map([
        {"lat": c["lat"], "lon": c["lng"]} for c in MOCK_CENTERS
    ])

    for c in MOCK_CENTERS:
        with st.container(border=True):
            st.write(f"**Name:** {c['name']}")
            st.write(f"**Location:** {c['location']}")
            st.write(f"**Type:** {c['type']}")

# -------------------- HISTORY --------------------
elif page == "History":
    conn = get_db()
    scans = conn.execute("SELECT * FROM scans WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    proofs = conn.execute("SELECT * FROM proofs WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()

    st.markdown("<div class='main-title'>Scan and Proof History</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>All recorded activity for your account.</div>", unsafe_allow_html=True)

    tab1, tab2 = st.tabs(["Scan History", "Proof History"])

    with tab1:
        if scans:
            for scan in scans:
                with st.container(border=True):
                    st.write(f"**File:** {scan['filename']}")
                    st.write(f"**Medicine:** {scan['detected_medicine']}")
                    st.write(f"**Category:** {scan['category']}")
                    st.write(f"**Risk:** {scan['risk_level']}")
                    st.write(f"**Confidence:** {scan['confidence']}")
                    st.write(f"**Expiry:** {scan['expiry_date']}")
                    st.write(f"**Points:** {scan['points_awarded']}")
                    st.write(f"**Created At:** {scan['created_at']}")

                    pdf_path = generate_pdf_report(scan)
                    with open(pdf_path, "rb") as f:
                        st.download_button(
                            label=f"Download PDF Report #{scan['id']}",
                            data=f,
                            file_name=os.path.basename(pdf_path),
                            mime="application/pdf",
                            key=f"pdf_{scan['id']}"
                        )
        else:
            st.info("No scan history available.")

    with tab2:
        if proofs:
            for proof in proofs:
                with st.container(border=True):
                    st.write(f"**Medicine:** {proof['medicine_name']}")
                    st.write(f"**Method:** {proof['disposal_method']}")
                    st.write(f"**Verification ID:** {proof['verification_id']}")
                    st.write(f"**Created At:** {proof['created_at']}")
                    qr_path = os.path.join(QR_DIR, proof["qr_file"])
                    if os.path.exists(qr_path):
                        st.image(qr_path, width=150)
        else:
            st.info("No proof history available.")

# -------------------- CHATBOT --------------------
elif page == "Chatbot":
    st.markdown("<div class='main-title'>MediCycle AI Assistant</div>", unsafe_allow_html=True)
    st.markdown("<div class='sub-title'>Ask questions about disposal, antibiotics, AMR, centers, and points.</div>", unsafe_allow_html=True)

    if "chat_history" not in st.session_state:
        st.session_state.chat_history = []

    user_message = st.text_input("Ask your question")

    col1, col2 = st.columns([1, 1])
    with col1:
        ask = st.button("Send")
    with col2:
        voice = st.button("Voice not enabled on Streamlit Cloud demo")

    if ask and user_message:
        reply = chatbot_reply(user_message)
        st.session_state.chat_history.append(("You", user_message))
        st.session_state.chat_history.append(("Bot", reply))

    for speaker, msg in st.session_state.chat_history:
        if speaker == "You":
            st.markdown(f"**🧑 You:** {msg}")
        else:
            st.markdown(f"**🤖 Bot:** {msg}")

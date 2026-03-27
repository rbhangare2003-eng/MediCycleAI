
import os
import re
import io
import json
import uuid
import sqlite3
from datetime import datetime
from difflib import get_close_matches

import pandas as pd
import streamlit as st
from PIL import Image, ImageFilter, ImageOps
import pytesseract
import qrcode
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "medicycle_streamlit.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
PROOF_DIR = os.path.join(BASE_DIR, "proofs")
REPORT_DIR = os.path.join(BASE_DIR, "reports")
MED_DB_PATH = os.path.join(BASE_DIR, "medicine_db.json")

for folder in [UPLOAD_DIR, PROOF_DIR, REPORT_DIR]:
    os.makedirs(folder, exist_ok=True)

st.set_page_config(page_title="MediCycle AI", page_icon="💊", layout="wide")

MOCK_CENTERS = [
    {"name": "City Pharmacy Return Point", "lat": 18.5308, "lon": 73.8478, "location": "Shivajinagar, Pune", "type": "Pharmacy"},
    {"name": "Green Med Collection Center", "lat": 18.5074, "lon": 73.8077, "location": "Kothrud, Pune", "type": "NGO Collection"},
    {"name": "Metro Hospital Disposal Desk", "lat": 18.5590, "lon": 73.8070, "location": "Aundh, Pune", "type": "Hospital"},
    {"name": "SafeRx Community Drop Box", "lat": 18.4967, "lon": 73.9272, "location": "Hadapsar, Pune", "type": "Community Center"}
]

st.markdown("""
<style>
.block-container {padding-top: 1.2rem; padding-bottom: 2rem;}
.hero-card {padding: 1rem 1.25rem; border-radius: 18px; background: linear-gradient(135deg, #eef2ff, #ecfeff); border: 1px solid #dbeafe;}
.metric-card {padding: 1rem; border-radius: 16px; color: white; text-align: center; font-weight: 700;}
.metric-purple {background: linear-gradient(135deg,#4f46e5,#7c3aed);}
.metric-blue {background: linear-gradient(135deg,#0ea5e9,#2563eb);}
.metric-orange {background: linear-gradient(135deg,#f97316,#ef4444);}
.metric-green {background: linear-gradient(135deg,#10b981,#059669);}
.section-card {border: 1px solid #e5e7eb; border-radius: 16px; padding: 1rem; background: white;}
.small-muted {color: #64748b; font-size: 0.95rem;}
.status-pill {display:inline-block; padding:6px 12px; border-radius:999px; background: #e0e7ff; color:#3730a3; font-weight:700; margin-right:8px;}
.risk-high {display:inline-block; padding:6px 12px; border-radius:999px; background:#dc2626; color:white; font-weight:700;}
.risk-low {display:inline-block; padding:6px 12px; border-radius:999px; background:#16a34a; color:white; font-weight:700;}
.conf-high {display:inline-block; padding:6px 12px; border-radius:999px; background:#dcfce7; color:#166534; font-weight:700;}
.conf-med {display:inline-block; padding:6px 12px; border-radius:999px; background:#fef3c7; color:#92400e; font-weight:700;}
.conf-low {display:inline-block; padding:6px 12px; border-radius:999px; background:#fee2e2; color:#991b1b; font-weight:700;}
</style>
""", unsafe_allow_html=True)

def db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
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
            qr_path TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    demo = conn.execute("SELECT id FROM users WHERE email = ?", ("demo@medicycle.ai",)).fetchone()
    if not demo:
        conn.execute(
            "INSERT INTO users (username,email,password_hash,points,rank,created_at) VALUES (?,?,?,?,?,?)",
            ("Demo User", "demo@medicycle.ai", generate_password_hash("demo123"), 200, "Safe-Helper", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
    conn.close()

def load_antibiotics():
    if os.path.exists(MED_DB_PATH):
        with open(MED_DB_PATH, "r") as f:
            data = json.load(f)
            return [x.lower() for x in data.get("antibiotics", [])]
    return ["amoxicillin", "azithromycin", "ciprofloxacin", "ofloxacin", "doxycycline", "metronidazole"]

def rank_from_points(points):
    if points >= 500:
        return "Eco-Champion"
    if points >= 300:
        return "Eco-Warrior"
    if points >= 150:
        return "Safe-Helper"
    return "Starter"

def update_user_points(user_id, add_points):
    conn = db()
    row = conn.execute("SELECT points FROM users WHERE id = ?", (user_id,)).fetchone()
    new_points = (row["points"] if row else 0) + add_points
    new_rank = rank_from_points(new_points)
    conn.execute("UPDATE users SET points = ?, rank = ? WHERE id = ?", (new_points, new_rank, user_id))
    conn.commit()
    conn.close()

def preprocess_pil_image(pil_img):
    img = pil_img.convert("L")
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    return img

def clean_text(text):
    text = (text or "").lower().replace("\n", " ")
    text = re.sub(r"[^a-z0-9/\- ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def detect_expiry(text):
    patterns = [r"\b\d{2}/\d{2}/\d{4}\b", r"\b\d{2}-\d{2}-\d{4}\b", r"\b\d{2}/\d{4}\b", r"\b\d{2}-\d{4}\b"]
    for p in patterns:
        m = re.search(p, text or "")
        if m:
            return m.group()
    return "Not found"

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

def calculate_points(category, risk_level):
    if category == "Antibiotic" and risk_level == "High":
        return 100
    if category == "Antibiotic":
        return 70
    return 40

def confidence_class(conf):
    return {"High": "conf-high", "Medium": "conf-med"}.get(conf, "conf-low")

def risk_class(risk):
    return "risk-high" if risk == "High" else "risk-low"

def get_user():
    uid = st.session_state.get("user_id")
    if not uid:
        return None
    conn = db()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    conn.close()
    return user

def save_uploaded_bytes(content_bytes, filename, folder):
    ext = filename.split(".")[-1].lower() if "." in filename else "jpg"
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    path = os.path.join(folder, safe_name)
    with open(path, "wb") as f:
        f.write(content_bytes)
    return safe_name, path

def generate_qr(verification_id):
    qr = qrcode.QRCode(box_size=8, border=2)
    qr.add_data(f"MediCycle Verification ID: {verification_id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    filename = f"{verification_id}.png"
    path = os.path.join(REPORT_DIR, filename)
    img.save(path)
    return path

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
        f"Created At: {scan_row['created_at']}"
    ]
    for line in lines:
        c.drawString(50, y, line)
        y -= 22
    c.setFont("Helvetica-Oblique", 10)
    c.drawString(50, y - 10, "Generated by MediCycle AI for safe medicine disposal guidance.")
    c.save()
    buffer.seek(0)
    return buffer.getvalue()

def chatbot_reply(message):
    msg = (message or "").lower()
    if "antibiotic" in msg:
        return "Antibiotics should be returned to a pharmacy or medical waste point. Avoid open waste disposal."
    if "expired" in msg:
        return "Expired medicines should not be consumed. Dispose them using an authorized return or collection point."
    if "dispose" in msg or "disposal" in msg:
        return "Recommended disposal methods are pharmacy return, hospital drop-off, or collection bin submission."
    if "amr" in msg:
        return "AMR means Antimicrobial Resistance. Unsafe disposal of antibiotics can contribute to AMR."
    if "points" in msg or "rank" in msg:
        return "You earn points after scans and safe disposal actions. More points improve your eco rank."
    if "center" in msg or "pharmacy" in msg or "map" in msg:
        return "Open the Centers page to see nearby disposal points."
    return "I can help with medicine disposal, antibiotics, expiry, AMR, centers, proof submission, and points."

init_db()

if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "last_scan_id" not in st.session_state:
    st.session_state.last_scan_id = None
if "last_proof_id" not in st.session_state:
    st.session_state.last_proof_id = None
if "prefill_medicine" not in st.session_state:
    st.session_state.prefill_medicine = ""

def render_auth():
    st.markdown("<div class='hero-card'><b>Streamlit cloud-ready version for safe medicine disposal</b></div>", unsafe_allow_html=True)
    st.title("MediCycle AI")
    st.caption("Demo account: demo@medicycle.ai / demo123")
    mode = st.radio("Choose Action", ["Login", "Register", "Reset Password"], horizontal=True)

    if mode == "Login":
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Login")
        if submitted:
            conn = db()
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
            conn.close()
            if user and check_password_hash(user["password_hash"], password):
                st.session_state.user_id = user["id"]
                st.success("Login successful.")
                st.rerun()
            else:
                st.error("Invalid email or password.")

    elif mode == "Register":
        with st.form("register_form"):
            username = st.text_input("Username")
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Register")
        if submitted:
            if not username or not email or not password:
                st.warning("All fields are required.")
            else:
                conn = db()
                existing = conn.execute("SELECT id FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
                if existing:
                    st.error("Email already registered.")
                else:
                    conn.execute(
                        "INSERT INTO users (username,email,password_hash,points,rank,created_at) VALUES (?,?,?,?,?,?)",
                        (username.strip(), email.strip().lower(), generate_password_hash(password), 0, "Starter", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    )
                    conn.commit()
                    st.success("Registration successful. Please login.")
                conn.close()

    else:
        with st.form("reset_form"):
            email = st.text_input("Registered Email")
            new_password = st.text_input("New Password", type="password")
            submitted = st.form_submit_button("Reset Password")
        if submitted:
            conn = db()
            user = conn.execute("SELECT id FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
            if not user:
                st.error("Email not found.")
            else:
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new_password), user["id"]))
                conn.commit()
                st.success("Password reset successful. Please login.")
            conn.close()

def render_home(user):
    st.markdown("<div class='hero-card'><h2 style='margin-bottom:0'>MediCycle AI</h2><div class='small-muted'>AI-powered safe medicine disposal ecosystem</div></div>", unsafe_allow_html=True)
    st.write("")
    col1, col2 = st.columns([2, 1])
    with col1:
        source = st.radio("Choose image source", ["Upload Image", "Use Camera"], horizontal=True)
        uploaded = None
        camera = None
        if source == "Upload Image":
            uploaded = st.file_uploader("Upload medicine image", type=["png", "jpg", "jpeg"])
        else:
            camera = st.camera_input("Capture medicine image")

        if st.button("Analyze Medicine", type="primary"):
            image_bytes = None
            original_name = None
            if uploaded is not None:
                image_bytes = uploaded.getvalue()
                original_name = uploaded.name
            elif camera is not None:
                image_bytes = camera.getvalue()
                original_name = "camera_capture.jpg"

            if not image_bytes:
                st.warning("Please upload or capture an image first.")
                return

            filename, file_path = save_uploaded_bytes(image_bytes, original_name, UPLOAD_DIR)
            pil_img = Image.open(io.BytesIO(image_bytes))
            processed = preprocess_pil_image(pil_img)

            try:
                raw_text = pytesseract.image_to_string(processed)
            except Exception as e:
                raw_text = f"OCR Error: {e}"

            cleaned = clean_text(raw_text)
            antibiotics = load_antibiotics()
            detected_raw, conf = detect_medicine(cleaned, antibiotics)

            detected_medicine = "Not detected"
            category = "Unknown"
            risk = "Low"
            recommendation = "Dispose as normal pharmaceutical waste"
            advisory = "Follow safe medicine disposal practices."
            status = "Label Unclear"

            if detected_raw:
                detected_medicine = detected_raw.title()
                category = "Antibiotic"
                risk = "High"
                recommendation = "Return to Pharmacy or Medical Waste Collection Center"
                advisory = "Improper antibiotic disposal may contribute to Antimicrobial Resistance (AMR)."
                status = "Antibiotic Identified"
            elif len(cleaned) > 8:
                status = "Partial Label Recognition"
                conf = "Medium"

            expiry = detect_expiry(raw_text)
            points_awarded = calculate_points(category, risk)

            conn = db()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO scans (
                    user_id, filename, detected_medicine, category, risk_level,
                    recommendation, awareness_message, expiry_date, confidence,
                    status_title, raw_text, cleaned_text, points_awarded, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user["id"], filename, detected_medicine, category, risk, recommendation,
                advisory, expiry, conf, status, raw_text, cleaned, points_awarded,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            ))
            scan_id = cur.lastrowid
            conn.commit()
            conn.close()

            update_user_points(user["id"], points_awarded)
            st.session_state.last_scan_id = scan_id
            st.success("Analysis completed.")
            st.rerun()

    with col2:
        st.markdown(f"""
        <div class='section-card'>
            <h4>User Profile</h4>
            <p><b>Name:</b> {user["username"]}</p>
            <p><b>Rank:</b> {user["rank"]}</p>
            <p><b>Points:</b> ⭐ {user["points"]}</p>
            <p class='small-muted'>Use the demo account if needed.</p>
        </div>
        """, unsafe_allow_html=True)

    if st.session_state.last_scan_id:
        conn = db()
        scan = conn.execute("SELECT * FROM scans WHERE id = ?", (st.session_state.last_scan_id,)).fetchone()
        conn.close()
        if scan:
            st.write("")
            c1, c2 = st.columns([1, 1])
            with c1:
                st.image(os.path.join(UPLOAD_DIR, scan["filename"]), caption="Uploaded Image", use_container_width=True)
            with c2:
                st.markdown(f"<span class='status-pill'>{scan['status_title']}</span><span class='{confidence_class(scan['confidence'])}'>{scan['confidence']} Confidence</span>", unsafe_allow_html=True)
                st.subheader("Smart Summary")
                st.write(f"**Medicine:** {scan['detected_medicine']}")
                st.write(f"**Category:** {scan['category']}")
                st.markdown(f"**Risk:** <span class='{risk_class(scan['risk_level'])}'>{scan['risk_level']}</span>", unsafe_allow_html=True)
                st.write(f"**Expiry:** {scan['expiry_date']}")
                st.write(f"**Recommendation:** {scan['recommendation']}")
                st.info(scan["awareness_message"])
                pdf_bytes = generate_pdf_bytes(scan)
                st.download_button("Download PDF Report", data=pdf_bytes, file_name=f"medicycle_report_{scan['id']}.pdf", mime="application/pdf")
                if st.button("Go to Proof Submission"):
                    st.session_state.prefill_medicine = scan["detected_medicine"]
                    st.session_state.page_jump = "Proof"
                    st.rerun()
                with st.expander("Show Technical OCR Details"):
                    st.text_area("Raw OCR Text", scan["raw_text"], height=140)
                    st.text_area("Processed OCR Text", scan["cleaned_text"], height=120)

def render_dashboard(user):
    conn = db()
    total_scans = conn.execute("SELECT COUNT(*) AS c FROM scans WHERE user_id=?", (user["id"],)).fetchone()["c"]
    antibiotics_detected = conn.execute("SELECT COUNT(*) AS c FROM scans WHERE user_id=? AND category='Antibiotic'", (user["id"],)).fetchone()["c"]
    high_risk_count = conn.execute("SELECT COUNT(*) AS c FROM scans WHERE user_id=? AND risk_level='High'", (user["id"],)).fetchone()["c"]
    proof_submissions = conn.execute("SELECT COUNT(*) AS c FROM proofs WHERE user_id=?", (user["id"],)).fetchone()["c"]
    recent_scans = conn.execute("SELECT * FROM scans WHERE user_id=? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    recent_proofs = conn.execute("SELECT * FROM proofs WHERE user_id=? ORDER BY id DESC LIMIT 5", (user["id"],)).fetchall()
    conn.close()
    safe_count = max(total_scans - high_risk_count, 0)
    antibiotic_percent = int((antibiotics_detected / total_scans) * 100) if total_scans else 0
    proof_percent = int((proof_submissions / total_scans) * 100) if total_scans else 0

    st.title("📊 Dashboard")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"<div class='metric-card metric-purple'>Total Scans<br><span style='font-size:2rem'>{total_scans}</span></div>", unsafe_allow_html=True)
    with c2:
        st.markdown(f"<div class='metric-card metric-blue'>Antibiotics Detected<br><span style='font-size:2rem'>{antibiotics_detected}</span></div>", unsafe_allow_html=True)
    with c3:
        st.markdown(f"<div class='metric-card metric-orange'>High Risk Cases<br><span style='font-size:2rem'>{high_risk_count}</span></div>", unsafe_allow_html=True)
    with c4:
        st.markdown(f"<div class='metric-card metric-green'>Proof Submissions<br><span style='font-size:2rem'>{proof_submissions}</span></div>", unsafe_allow_html=True)

    st.write("")
    p1, p2 = st.columns(2)
    with p1:
        st.subheader("Detection Progress")
        st.progress(antibiotic_percent / 100 if antibiotic_percent else 0, text=f"Antibiotic Detection Rate: {antibiotic_percent}%")
        st.progress(proof_percent / 100 if proof_percent else 0, text=f"Proof Submission Rate: {proof_percent}%")
    with p2:
        st.subheader("Risk Distribution")
        chart_df = pd.DataFrame({"Risk": ["High Risk", "Low Risk / Other"], "Count": [high_risk_count, safe_count]})
        st.bar_chart(chart_df.set_index("Risk"))

    a, b = st.columns(2)
    with a:
        st.subheader("Recent Scan Records")
        if recent_scans:
            for s in recent_scans:
                with st.container(border=True):
                    st.write(f"**File:** {s['filename']}")
                    st.write(f"**Medicine:** {s['detected_medicine']}")
                    st.write(f"**Category:** {s['category']}")
                    st.write(f"**Risk:** {s['risk_level']}")
                    st.write(f"**Confidence:** {s['confidence']}")
                    st.write(f"**Points:** {s['points_awarded']}")
                    st.caption(s["created_at"])
        else:
            st.info("No scan records yet.")
    with b:
        st.subheader("Recent Proof Records")
        if recent_proofs:
            for p in recent_proofs:
                with st.container(border=True):
                    st.write(f"**Medicine:** {p['medicine_name']}")
                    st.write(f"**Method:** {p['disposal_method']}")
                    st.write(f"**Verification ID:** {p['verification_id']}")
                    st.caption(p["created_at"])
        else:
            st.info("No proof records yet.")

def render_proof(user):
    st.title("✅ Submit Disposal Proof")
    with st.form("proof_form"):
        medicine_name = st.text_input("Medicine Name", value=st.session_state.get("prefill_medicine", ""))
        disposal_method = st.selectbox("Disposal Method", ["Returned to Pharmacy", "Disposed in Collection Bin", "Hospital Drop-off"])
        proof_image = st.file_uploader("Upload Proof Image", type=["png", "jpg", "jpeg"])
        submitted = st.form_submit_button("Submit Proof")
    if submitted:
        if not medicine_name:
            st.warning("Medicine name is required.")
            return
        proof_filename = "No image uploaded"
        if proof_image is not None:
            proof_filename, _ = save_uploaded_bytes(proof_image.getvalue(), proof_image.name, PROOF_DIR)
        verification_id = f"MC-{uuid.uuid4().hex[:10].upper()}"
        qr_path = generate_qr(verification_id)
        conn = db()
        cur = conn.cursor()
        cur.execute("INSERT INTO proofs (user_id, medicine_name, disposal_method, proof_image, verification_id, qr_path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user["id"], medicine_name, disposal_method, proof_filename, verification_id, qr_path, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        st.session_state.last_proof_id = cur.lastrowid
        conn.close()
        st.success("Proof submitted successfully.")
    if st.session_state.last_proof_id:
        conn = db()
        proof = conn.execute("SELECT * FROM proofs WHERE id=? AND user_id=?", (st.session_state.last_proof_id, user["id"])).fetchone()
        conn.close()
        if proof:
            st.subheader("Verification Certificate")
            st.write(f"**Medicine:** {proof['medicine_name']}")
            st.write(f"**Method:** {proof['disposal_method']}")
            st.write(f"**Verification ID:** {proof['verification_id']}")
            st.image(proof["qr_path"], caption="QR Verification")

def render_history(user):
    conn = db()
    scans = conn.execute("SELECT * FROM scans WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    proofs = conn.execute("SELECT * FROM proofs WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    conn.close()
    st.title("🕘 History")
    tab1, tab2 = st.tabs(["Scan History", "Proof History"])
    with tab1:
        if scans:
            for s in scans:
                with st.container(border=True):
                    st.write(f"**Scan ID:** {s['id']}")
                    st.write(f"**File:** {s['filename']}")
                    st.write(f"**Medicine:** {s['detected_medicine']}")
                    st.write(f"**Category:** {s['category']}")
                    st.write(f"**Risk:** {s['risk_level']}")
                    st.write(f"**Confidence:** {s['confidence']}")
                    st.caption(s["created_at"])
                    pdf_bytes = generate_pdf_bytes(s)
                    st.download_button(f"Download Report #{s['id']}", data=pdf_bytes, file_name=f"medicycle_report_{s['id']}.pdf", mime="application/pdf", key=f"pdf_{s['id']}")
        else:
            st.info("No scan history yet.")
    with tab2:
        if proofs:
            for p in proofs:
                with st.container(border=True):
                    st.write(f"**Medicine:** {p['medicine_name']}")
                    st.write(f"**Method:** {p['disposal_method']}")
                    st.write(f"**Verification ID:** {p['verification_id']}")
                    st.image(p["qr_path"], width=140)
                    st.caption(p["created_at"])
        else:
            st.info("No proof history yet.")

def render_centers():
    st.title("📍 Nearest Disposal Centers")
    st.caption("Interactive map with mock nearby pharmacy, NGO, and hospital drop-off points.")
    df = pd.DataFrame(MOCK_CENTERS)
    st.map(df[["lat", "lon"]].rename(columns={"lon": "lon"}))
    st.dataframe(df[["name", "location", "type"]], use_container_width=True, hide_index=True)

def render_chatbot():
    st.title("💬 AI Disposal Assistant")
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = [{"role": "assistant", "text": "Hello! Ask me about disposal, antibiotics, AMR, points, or centers."}]
    for msg in st.session_state.chat_messages:
        with st.chat_message("assistant" if msg["role"] == "assistant" else "user"):
            st.write(msg["text"])
    prompt = st.chat_input("Ask about medicine disposal...")
    if prompt:
        st.session_state.chat_messages.append({"role": "user", "text": prompt})
        st.session_state.chat_messages.append({"role": "assistant", "text": chatbot_reply(prompt)})
        st.rerun()

user = get_user()
if not user:
    render_auth()
else:
    st.sidebar.title("MediCycle AI")
    st.sidebar.success(f"{user['username']} | {user['rank']} | ⭐ {user['points']}")
    page = st.sidebar.radio("Navigate", ["Home", "Dashboard", "Proof", "History", "Centers", "Chatbot"])
    if st.sidebar.button("Logout"):
        st.session_state.user_id = None
        st.session_state.last_scan_id = None
        st.session_state.last_proof_id = None
        st.rerun()

    page_jump = st.session_state.pop("page_jump", None)
    if page_jump:
        page = page_jump

    if page == "Home":
        render_home(user)
    elif page == "Dashboard":
        render_dashboard(user)
    elif page == "Proof":
        render_proof(user)
    elif page == "History":
        render_history(user)
    elif page == "Centers":
        render_centers()
    else:
        render_chatbot()

MediCycle AI - Streamlit Deployment Files

Files:
- streamlit_app.py
- requirements.txt
- packages.txt

Local run:
1. python3 -m venv venv
2. source venv/bin/activate
3. pip install -r requirements.txt
4. streamlit run streamlit_app.py

For Streamlit Community Cloud:
- Push these files and medicine_db.json to your GitHub repo root.
- In Streamlit Cloud, choose streamlit_app.py as the entrypoint.
- Keep requirements.txt and packages.txt in the same repo root.

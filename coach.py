import streamlit as st
import anthropic
import re, time, json, os, base64, hashlib, hmac, secrets, socket
from datetime import datetime
from pathlib import Path

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import google.generativeai as genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

try:
    from pypdf import PdfReader
    PYPDF_OK = True
except ImportError:
    try:
        from PyPDF2 import PdfReader
        PYPDF_OK = True
    except ImportError:
        PYPDF_OK = False

try:
    import io as _io
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors as _rc
    from reportlab.pdfgen import canvas as _rl_canvas
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    REPORTLAB_OK = True
except Exception:
    REPORTLAB_OK = False

# ══════════════════════════════════════════════════════════════════════════════
#  PERSISTENT STORAGE  (accounts + sessions + leaderboard)
# ══════════════════════════════════════════════════════════════════════════════
DATA_DIR = Path("interview_data")
DATA_DIR.mkdir(exist_ok=True)
USERS_FILE       = DATA_DIR / "users.json"          # login credentials
SESSIONS_FILE    = DATA_DIR / "sessions.json"       # interview results
LEADERBOARD_FILE = DATA_DIR / "leaderboard.json"    # top scores
CUSTOM_FILE      = DATA_DIR / "custom_interviews.json"  # recruiter-designed interviews

def load_json(p, d):
    try:
        if p.exists(): return json.loads(p.read_text())
    except: pass
    return d

def save_json(p, data):
    try: p.write_text(json.dumps(data, indent=2, default=str))
    except: pass

# ── Password hashing (salted SHA-256) ──────────────────────────────────────────
def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"

def verify_password(password, stored):
    try:
        salt, _ = stored.split("$", 1)
        return hmac.compare_digest(stored, hash_password(password, salt))
    except: return False

# ── Account management ─────────────────────────────────────────────────────────
def get_users():
    return load_json(USERS_FILE, {})

def create_user(email, password, name, role, company="", emp_id=""):
    users = get_users()
    email = email.lower().strip()
    if email in users:
        return False, "An account with this email already exists."
    users[email] = {
        "name": name.strip(),
        "email": email,
        "password": hash_password(password),
        "role": role,            # "candidate" or "recruiter"
        "company": company.strip(),
        "emp_id": emp_id.strip(),
        "created": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    save_json(USERS_FILE, users)
    return True, "Account created successfully."

def authenticate(email, password):
    users = get_users()
    email = email.lower().strip()
    if email not in users:
        return False, None, "No account found with this email."
    if not verify_password(password, users[email]["password"]):
        return False, None, "Incorrect password."
    return True, users[email], "Login successful."

# ── Interview session storage ──────────────────────────────────────────────────
def save_session(data):
    s = load_json(SESSIONS_FILE, [])
    s.append(data)
    save_json(SESSIONS_FILE, s)
    _update_lb(data)

def _update_lb(data):
    lb   = load_json(LEADERBOARD_FILE, {})
    role = data.get("role","?")
    lb.setdefault(role, [])
    lb[role].append({"name":data.get("candidate_name","?"),"score":data.get("avg_score",0),
                     "date":data.get("date",""),"violations":data.get("violations",0)})
    lb[role] = sorted(lb[role], key=lambda x: x["score"], reverse=True)[:20]
    save_json(LEADERBOARD_FILE, lb)

# ── Custom (recruiter-designed) interviews ─────────────────────────────────────
def _new_interview_code(company):
    base="".join(ch for ch in (company or "ORG").upper() if ch.isalnum())[:4] or "ORG"
    return f"{base}-{secrets.token_hex(3).upper()}"

def save_custom_interview(cfg):
    data=load_json(CUSTOM_FILE, {})
    data[cfg["code"]]=cfg
    save_json(CUSTOM_FILE, data)

def get_custom_interview(code):
    if not code: return None
    data=load_json(CUSTOM_FILE, {})
    return data.get(code.strip().upper())

def list_custom_interviews(recruiter_email):
    data=load_json(CUSTOM_FILE, {})
    out=[c for c in data.values() if c.get("recruiter","")==recruiter_email]
    return sorted(out, key=lambda c: c.get("created",""), reverse=True)

def delete_custom_interview(code):
    data=load_json(CUSTOM_FILE, {})
    if code in data:
        del data[code]; save_json(CUSTOM_FILE, data)

# Advanced lockdown / proctoring technologies a recruiter can switch on per interview.
# key -> (label, help, default_on)
PROCTOR_OPTIONS = {
    "camera":      ("📷 Webcam proctoring + recording", "Live camera self-view with a recording indicator throughout the exam.", True),
    "face_required":("🤳 Require face detection before start", "The Start button unlocks only after the candidate's face is detected and a photo is auto-captured.", True),
    "gaze":        ("👁️ Gaze / head-pose monitoring", "After 2 head turns the candidate gets one red warning; a 4th head turn ends the exam as failed.", True),
    "voice":       ("🗣️ Human-voice detection", "Detects talking (human speech 300–3400 Hz, fluctuating) and warns. Steady machine noise (fan/AC) is ignored.", True),
    "fullscreen":  ("⛶ Full-screen enforcement", "Auto full-screen on start; leaving full-screen is logged as a violation.", True),
    "copy_paste":  ("📋 Block copy / paste / cut", "Disables copy, paste and cut to prevent answer smuggling.", True),
    "right_click": ("🖱️ Block right-click menu", "Disables the context menu.", True),
    "devtools":    ("🛠️ Block DevTools", "Blocks F12 and Ctrl/Cmd+Shift+I/J/C and warns if developer tools open.", True),
    "shortcuts":   ("⌨️ Block save/print/find/source shortcuts", "Blocks Ctrl/Cmd+S, P, U, F.", True),
    "tab_switch":  ("🔁 Tab-switch & focus-loss logging", "Logs every time the candidate switches tabs or the window loses focus.", True),
    "screenshot":  ("📸 Discourage screenshots", "Intercepts the PrintScreen key and logs the attempt.", True),
    "selection":   ("✋ Disable text selection & drag", "Stops selecting/highlighting and dragging page text.", True),
    "paste_block": ("🚫 Block pasting into the answer box", "Even the answer field rejects pasted text (forces typing).", False),
}
def default_proctoring():
    return {k:dflt for k,(lbl,hlp,dflt) in PROCTOR_OPTIONS.items()}

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title="AI Interview Coach", page_icon="🔒", layout="wide")

US_COMPANIES = {
    "🔵 Big Tech (FAANG+)": {
        "Google / Alphabet": {
            "logo":"G","color":"#4285F4","hq":"Mountain View, CA",
            "style":"Algorithmic coding + system design + Googleyness (behavioral). Heavy LeetCode Hard. Known for: 'Design YouTube', 'Merge k sorted lists'.",
            "roles":["Software Engineer","ML Engineer","Data Scientist","Product Manager","Site Reliability Engineer","Cloud Architect","Research Scientist","DevOps Engineer"]
        },
        "Meta / Facebook": {
            "logo":"M","color":"#1877F2","hq":"Menlo Park, CA",
            "style":"Coding + system design + behavioral. Emphasizes scale (billions of users). Known for: 'Design Instagram Feed', 'Find all paths in graph'.",
            "roles":["Software Engineer","ML Engineer","Data Engineer","Product Manager","Research Scientist","AR/VR Engineer","Backend Engineer","Frontend Engineer"]
        },
        "Amazon": {
            "logo":"A","color":"#FF9900","hq":"Seattle, WA",
            "style":"14 Leadership Principles drive ALL behavioral questions. Coding is standard LC. System design at senior+. Known for: STAR method for every answer.",
            "roles":["Software Development Engineer","ML Engineer","Data Scientist","Solutions Architect","Product Manager","Cloud Support Engineer","DevOps Engineer","Business Analyst"]
        },
        "Apple": {
            "logo":"🍎","color":"#555555","hq":"Cupertino, CA",
            "style":"Deep technical + problem solving. iOS/macOS specifics for mobile roles. Known for design thinking. Less algorithmic, more practical engineering.",
            "roles":["Software Engineer","iOS Developer","macOS Engineer","ML Engineer","Hardware Engineer","Product Designer","Data Scientist","Security Engineer"]
        },
        "Microsoft": {
            "logo":"⊞","color":"#00A4EF","hq":"Redmond, WA",
            "style":"Coding + system design + behavioral. Azure cloud for infra roles. Culture fit around Growth Mindset. Known for collaborative interview loops.",
            "roles":["Software Engineer","Cloud Engineer","Data Scientist","Product Manager","Azure Architect","ML Engineer","Security Engineer","DevOps Engineer"]
        },
        "Netflix": {
            "logo":"N","color":"#E50914","hq":"Los Gatos, CA",
            "style":"Senior-heavy interviews. Freedom & Responsibility culture. Deep system design for streaming at scale. No standard LeetCode grind — more whiteboard design.",
            "roles":["Software Engineer","Data Engineer","ML Engineer","Platform Engineer","Security Engineer","Product Manager","Site Reliability Engineer"]
        },
    },
    "🤖 AI & Machine Learning Companies": {
        "OpenAI": {
            "logo":"O","color":"#10A37F","hq":"San Francisco, CA",
            "style":"Deep ML theory + research coding. Transformer architecture, RLHF, safety alignment. Expect to discuss recent papers. Very research-oriented.",
            "roles":["ML Research Engineer","Software Engineer","Data Scientist","AI Safety Researcher","Product Manager","Infrastructure Engineer","Applied Scientist"]
        },
        "Anthropic": {
            "logo":"A","color":"#CC785C","hq":"San Francisco, CA",
            "style":"Constitutional AI, interpretability, safety research. Strong ML theory + Python. Mission-driven culture. Research + engineering combined.",
            "roles":["ML Research Engineer","Software Engineer","Safety Researcher","Policy Researcher","Data Scientist","Infrastructure Engineer"]
        },
        "NVIDIA": {
            "logo":"N","color":"#76B900","hq":"Santa Clara, CA",
            "style":"GPU architecture, CUDA, parallel computing. Deep hardware/software co-design. Strong C++/Python. HPC and AI accelerator knowledge.",
            "roles":["CUDA Engineer","ML Engineer","Software Engineer","Hardware Engineer","Solutions Architect","Data Scientist","DevOps Engineer"]
        },
        "Hugging Face": {
            "logo":"🤗","color":"#FFD21E","hq":"New York, NY",
            "style":"Open-source ML, transformers, datasets. Strong Python + PyTorch/TensorFlow. Community-oriented culture. Practical ML deployment.",
            "roles":["ML Engineer","Software Engineer","Research Engineer","Developer Advocate","Data Scientist"]
        },
        "Scale AI": {
            "logo":"S","color":"#7B46F6","hq":"San Francisco, CA",
            "style":"Data quality, ML pipelines, annotation systems. Product + engineering. Rapid growth startup culture.",
            "roles":["Software Engineer","ML Engineer","Data Scientist","Product Manager","Solutions Engineer"]
        },
        "Cohere": {
            "logo":"C","color":"#39C4AA","hq":"Toronto / San Francisco",
            "style":"NLP, LLMs, enterprise AI. Strong ML + API design. Research + product balance.",
            "roles":["ML Engineer","Software Engineer","Research Scientist","Solutions Engineer","Data Scientist"]
        },
    },
    "☁️ Cloud & Infrastructure": {
        "Amazon Web Services (AWS)": {
            "logo":"AWS","color":"#FF9900","hq":"Seattle, WA",
            "style":"Amazon Leadership Principles + cloud architecture. Deep AWS services knowledge. Solutions design at scale.",
            "roles":["Cloud Engineer","Solutions Architect","SRE","DevOps Engineer","Software Engineer","Data Engineer","Security Engineer"]
        },
        "Google Cloud (GCP)": {
            "logo":"GCP","color":"#4285F4","hq":"Sunnyvale, CA",
            "style":"Cloud architecture + Kubernetes + Terraform. Strong networking. Googleyness culture.",
            "roles":["Cloud Engineer","DevOps Engineer","Data Engineer","ML Engineer","Solutions Architect","SRE"]
        },
        "Microsoft Azure": {
            "logo":"AZ","color":"#0089D6","hq":"Redmond, WA",
            "style":"Azure services + .NET ecosystem. Enterprise focus. Growth mindset culture.",
            "roles":["Cloud Architect","DevOps Engineer","Software Engineer","Data Engineer","Solutions Engineer","Security Engineer"]
        },
        "Cloudflare": {
            "logo":"CF","color":"#F48120","hq":"San Francisco, CA",
            "style":"Networking, security, CDN, edge computing. Strong systems knowledge. Rust/Go preferred.",
            "roles":["Software Engineer","Network Engineer","Security Engineer","DevOps Engineer","Solutions Engineer"]
        },
        "Snowflake": {
            "logo":"❄","color":"#29B5E8","hq":"Bozeman, MT / SF",
            "style":"Data warehousing, SQL, cloud data platforms. Customer-obsessed culture. Strong data engineering.",
            "roles":["Data Engineer","Software Engineer","Solutions Architect","Data Scientist","Product Manager","Sales Engineer"]
        },
        "Databricks": {
            "logo":"DB","color":"#FF3621","hq":"San Francisco, CA",
            "style":"Apache Spark, Delta Lake, ML pipelines. Heavy data engineering. Open-source culture.",
            "roles":["Data Engineer","ML Engineer","Software Engineer","Solutions Architect","Data Scientist","Sales Engineer"]
        },
    },
    "💳 Fintech & Finance": {
        "Stripe": {
            "logo":"S","color":"#635BFF","hq":"San Francisco, CA",
            "style":"API design, payments infrastructure, reliability. Strong coding + system design. 'Move fast carefully' culture. Write clearly.",
            "roles":["Software Engineer","Backend Engineer","Data Scientist","Product Manager","ML Engineer","Security Engineer","Infrastructure Engineer"]
        },
        "Square / Block": {
            "logo":"■","color":"#006AFF","hq":"San Francisco, CA",
            "style":"Payments, fintech, mobile. Strong mobile + backend. Inclusive culture.",
            "roles":["Software Engineer","iOS/Android Engineer","Data Scientist","ML Engineer","Product Manager","Security Engineer"]
        },
        "PayPal": {
            "logo":"P","color":"#003087","hq":"San Jose, CA",
            "style":"Payments, fraud detection, scalability. Java/Python heavy. Enterprise + startup mix.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","Security Engineer","Product Manager"]
        },
        "Goldman Sachs": {
            "logo":"GS","color":"#7399C6","hq":"New York, NY",
            "style":"Finance + tech combined. Quant skills valued. Java/Python. Trading systems, risk. Formal culture.",
            "roles":["Software Engineer","Quantitative Analyst","Data Engineer","ML Engineer","Cybersecurity Analyst","Product Manager"]
        },
        "JPMorgan Chase": {
            "logo":"JP","color":"#003594","hq":"New York, NY",
            "style":"Banking + tech. Java heavy. Risk and compliance awareness. Large enterprise culture.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Cybersecurity Analyst","Cloud Engineer","Business Analyst"]
        },
        "Robinhood": {
            "logo":"R","color":"#00C805","hq":"Menlo Park, CA",
            "style":"Fintech startup culture. Python/Go. Trading systems, real-time data. Fast-paced.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","Security Engineer","Product Manager"]
        },
        "Coinbase": {
            "logo":"₿","color":"#0052FF","hq":"Remote-first",
            "style":"Crypto, blockchain, Web3. Python/Go/TypeScript. Regulatory awareness. Remote-first culture.",
            "roles":["Software Engineer","Blockchain Engineer","Data Scientist","Security Engineer","Product Manager","ML Engineer"]
        },
    },
    "🚗 Mobility & Transportation": {
        "Uber": {
            "logo":"U","color":"#000000","hq":"San Francisco, CA",
            "style":"Scale systems (millions of rides). Real-time data, maps, payments. Go/Python/Java. Strong system design.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","Product Manager","Site Reliability Engineer"]
        },
        "Lyft": {
            "logo":"L","color":"#FF00BF","hq":"San Francisco, CA",
            "style":"Similar to Uber. Python/Swift. Real-time matching algorithms. Culture emphasizes collaboration.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","iOS/Android Engineer","Product Manager","Data Engineer"]
        },
        "Tesla": {
            "logo":"T","color":"#CC0000","hq":"Austin, TX",
            "style":"Embedded systems, C++, Python, hardware integration. Autonomous driving, robotics. Fast-paced, high-pressure.",
            "roles":["Software Engineer","Embedded Engineer","ML Engineer","Data Scientist","Autopilot Engineer","Controls Engineer","Infrastructure Engineer"]
        },
        "Waymo": {
            "logo":"W","color":"#009AC7","hq":"Mountain View, CA",
            "style":"Autonomous vehicles, robotics, ML. C++/Python. Sensor fusion, computer vision.",
            "roles":["Software Engineer","ML Engineer","Robotics Engineer","Computer Vision Engineer","Data Scientist","Systems Engineer"]
        },
    },
    "🛍️ E-Commerce & Marketplace": {
        "Shopify": {
            "logo":"S","color":"#96BF48","hq":"Ottawa, Canada / Remote",
            "style":"Ruby on Rails, React, distributed systems. Merchant obsession. Strong product thinking.",
            "roles":["Software Engineer","Frontend Engineer","Data Scientist","ML Engineer","Product Manager","Solutions Engineer"]
        },
        "DoorDash": {
            "logo":"D","color":"#FF3008","hq":"San Francisco, CA",
            "style":"Logistics, real-time systems, ML for ETA. Python/Go. Fast startup culture.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","Product Manager","Site Reliability Engineer"]
        },
        "Airbnb": {
            "logo":"Å","color":"#FF5A5F","hq":"San Francisco, CA",
            "style":"React, Java, Kotlin. Search, payments, trust & safety. Design-driven culture.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Frontend Engineer","Product Manager","Data Engineer"]
        },
        "eBay": {
            "logo":"e","color":"#E53238","hq":"San Jose, CA",
            "style":"Large-scale Java systems. Search, recommendations, fraud. Enterprise culture.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","Product Manager","Security Engineer"]
        },
    },
    "🏥 Health Tech": {
        "Epic Systems": {
            "logo":"E","color":"#D9534F","hq":"Verona, WI",
            "style":"Healthcare software, EHR systems. Strong C#/Java. Customer-focused. Challenging aptitude tests.",
            "roles":["Software Engineer","Implementation Consultant","Data Analyst","QA Engineer","Technical Writer","Project Manager"]
        },
        "Hims & Hers": {
            "logo":"H","color":"#4CAF50","hq":"San Francisco, CA",
            "style":"Telemedicine, e-commerce. React/Python. Fast startup growth.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Product Manager","Backend Engineer"]
        },
        "Veeva Systems": {
            "logo":"V","color":"#F26724","hq":"Pleasanton, CA",
            "style":"Life sciences SaaS. CRM, clinical data. Salesforce ecosystem knowledge.",
            "roles":["Software Engineer","Solutions Architect","Data Engineer","Product Manager","QA Engineer"]
        },
    },
    "🎮 Gaming & Entertainment": {
        "Roblox": {
            "logo":"R","color":"#E2231A","hq":"San Mateo, CA",
            "style":"Game engine, Lua, distributed systems at scale. UGC platform. C++/Python.",
            "roles":["Software Engineer","ML Engineer","Data Scientist","Game Engineer","Backend Engineer","Product Manager"]
        },
        "Electronic Arts (EA)": {
            "logo":"EA","color":"#00A651","hq":"Redwood City, CA",
            "style":"C++, game engines, graphics. Live service platforms. Large enterprise gaming.",
            "roles":["Software Engineer","Game Engineer","ML Engineer","Data Analyst","Product Manager","QA Engineer"]
        },
        "Spotify": {
            "logo":"S","color":"#1DB954","hq":"New York, NY",
            "style":"Agile squads culture. Java/Python/Swift. Recommendations, audio streaming. Data-driven.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","iOS/Android Engineer","Product Manager"]
        },
    },
    "💼 Recruiting & Staffing (USA)": {
        "LinkedIn": {
            "logo":"in","color":"#0077B5","hq":"Sunnyvale, CA",
            "style":"Java/Scala, Kafka, distributed systems. Professional networking at scale. Data + ML for recommendations.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Product Manager","Backend Engineer","Data Engineer"]
        },
        "Indeed": {
            "logo":"I","color":"#2557A7","hq":"Austin, TX",
            "style":"Java, Python. Search, job matching ML. Large-scale data pipelines.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Product Manager","Software Development Engineer"]
        },
        "Workday": {
            "logo":"W","color":"#0875E1","hq":"Pleasanton, CA",
            "style":"HR/Finance SaaS. Java + Workday proprietary. Enterprise culture. Strong functional knowledge.",
            "roles":["Software Engineer","Data Analyst","Solutions Architect","Product Manager","QA Engineer","Business Analyst"]
        },
        "Robert Half Technology": {
            "logo":"RH","color":"#C8102E","hq":"Menlo Park, CA",
            "style":"Staffing/recruiting. Client placement interviews. Technical screening + cultural fit.",
            "roles":["Software Engineer","Data Analyst","Network Engineer","IT Support Specialist","Project Manager","Business Analyst"]
        },
        "Kforce": {
            "logo":"K","color":"#E31837","hq":"Tampa, FL",
            "style":"IT staffing. Technical skills + client fit. Contract and full-time placement.",
            "roles":["Software Engineer","Data Analyst","Network Engineer","Cloud Engineer","QA Engineer","Project Manager"]
        },
        "TEKsystems": {
            "logo":"T","color":"#003087","hq":"Hanover, MD",
            "style":"IT staffing and services. Technical + soft skills. Large enterprise clients.",
            "roles":["Software Engineer","Network Engineer","Data Analyst","Cloud Engineer","Cybersecurity Analyst","Project Manager"]
        },
        "Insight Global": {
            "logo":"IG","color":"#FF6B35","hq":"Atlanta, GA",
            "style":"IT staffing. Technical screening + culture fit. Diverse client placements.",
            "roles":["Software Engineer","IT Support","Data Analyst","Network Engineer","Project Manager","Business Analyst"]
        },
        "Dice": {
            "logo":"D","color":"#EB1C26","hq":"Remote / Des Moines, IA",
            "style":"Tech job platform. Screens for specific tech stack match. Contract + perm.",
            "roles":["Software Engineer","Data Engineer","Cloud Engineer","DevOps Engineer","ML Engineer","QA Engineer"]
        },
    },
    "🏢 Big Consulting & Enterprise": {
        "Accenture": {
            "logo":"Ac","color":"#A100FF","hq":"New York, NY",
            "style":"Case + technical interviews. Client-facing skills valued. Java/.NET/SAP knowledge. Large enterprise projects.",
            "roles":["Software Engineer","Data Analyst","Cloud Engineer","Business Analyst","Cybersecurity Analyst","ML Engineer","Project Manager"]
        },
        "Deloitte Technology": {
            "logo":"D","color":"#86BC25","hq":"New York, NY",
            "style":"Case studies + tech. Consulting mindset. SAP, Oracle, cloud platforms. Client service.",
            "roles":["Software Engineer","Data Analyst","Cloud Engineer","Cybersecurity Analyst","Business Analyst","Solutions Architect","ML Engineer"]
        },
        "IBM": {
            "logo":"IBM","color":"#006699","hq":"Armonk, NY",
            "style":"Java, cloud (IBM Cloud), AI (Watson). Enterprise consulting. Strong technical breadth.",
            "roles":["Software Engineer","Data Scientist","Cloud Engineer","ML Engineer","Cybersecurity Analyst","Solutions Architect","DevOps Engineer"]
        },
        "Salesforce": {
            "logo":"SF","color":"#00A1E0","hq":"San Francisco, CA",
            "style":"CRM, Apex/SOQL, Salesforce platform. Customer success mindset. Strong admin + dev tracks.",
            "roles":["Software Engineer","Salesforce Developer","Data Engineer","Solutions Architect","Product Manager","ML Engineer","QA Engineer"]
        },
        "ServiceNow": {
            "logo":"SN","color":"#62D84E","hq":"Santa Clara, CA",
            "style":"ITSM platform, JavaScript, ServiceNow scripting. Enterprise workflow automation.",
            "roles":["Software Engineer","Solutions Architect","Data Analyst","Product Manager","QA Engineer","Business Analyst"]
        },
        "Oracle": {
            "logo":"O","color":"#F80000","hq":"Austin, TX",
            "style":"Java, Oracle DB, cloud. Large-scale enterprise. Database and ERP knowledge valued.",
            "roles":["Software Engineer","Database Administrator","Cloud Engineer","Data Analyst","Solutions Architect","DevOps Engineer"]
        },
    },
    "🔐 Cybersecurity": {
        "CrowdStrike": {
            "logo":"CS","color":"#E2251C","hq":"Austin, TX",
            "style":"Endpoint security, threat intelligence. C++/Python. Security mindset. Incident response knowledge.",
            "roles":["Software Engineer","Security Engineer","Threat Intelligence Analyst","Data Scientist","DevOps Engineer","SOC Analyst"]
        },
        "Palo Alto Networks": {
            "logo":"PA","color":"#F04E22","hq":"Santa Clara, CA",
            "style":"Network security, firewall, SASE. Security architecture. Strong technical + sales roles.",
            "roles":["Software Engineer","Security Engineer","Network Engineer","Cloud Security Engineer","Solutions Engineer","Data Scientist"]
        },
        "Okta": {
            "logo":"Ok","color":"#007DC1","hq":"San Francisco, CA",
            "style":"Identity management, OAuth, SAML. API design. Zero-trust security model.",
            "roles":["Software Engineer","Security Engineer","Solutions Engineer","Data Engineer","Product Manager","IT Support"]
        },
    },
    "📦 Other Major Tech": {
        "Twitter / X": {
            "logo":"X","color":"#000000","hq":"San Francisco, CA",
            "style":"Distributed systems, real-time feeds, Scala/Java. Large-scale messaging.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","Site Reliability Engineer"]
        },
        "Adobe": {
            "logo":"Ad","color":"#FF0000","hq":"San Jose, CA",
            "style":"Creative cloud, APIs, Java/C++. Strong product design culture.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Product Manager","Frontend Engineer","Security Engineer"]
        },
        "Slack / Salesforce": {
            "logo":"Sl","color":"#4A154B","hq":"San Francisco, CA",
            "style":"Collaboration tools, real-time messaging, Electron/React. Strong product + engineering.",
            "roles":["Software Engineer","Data Scientist","Backend Engineer","Product Manager","ML Engineer"]
        },
        "Zoom": {
            "logo":"Z","color":"#2D8CFF","hq":"San Jose, CA",
            "style":"Video infrastructure, WebRTC, C++/Go. Real-time communication at scale.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Backend Engineer","Security Engineer","Product Manager"]
        },
        "Palantir": {
            "logo":"P","color":"#101113","hq":"Denver, CO",
            "style":"Data analytics platforms, Ontology. Python/Java. Government + enterprise. Strong problem decomposition.",
            "roles":["Software Engineer","Forward Deployed Engineer","Data Engineer","ML Engineer","Product Manager","Solutions Engineer"]
        },
        "Twilio": {
            "logo":"T","color":"#F22F46","hq":"San Francisco, CA",
            "style":"Communications APIs, CPaaS. REST APIs, WebRTC. Developer-first culture.",
            "roles":["Software Engineer","Solutions Engineer","Data Scientist","Product Manager","Developer Advocate","Backend Engineer"]
        },
        "Reddit": {
            "logo":"Rd","color":"#FF4500","hq":"San Francisco, CA",
            "style":"Social platform at scale, Python/Go, recommendation systems, content ranking. Community-driven product.",
            "roles":["Software Engineer","ML Engineer","Data Scientist","Backend Engineer","Product Manager","Trust & Safety Engineer"]
        },
        "Pinterest": {
            "logo":"Pi","color":"#E60023","hq":"San Francisco, CA",
            "style":"Visual discovery, recommendation ML, large-scale image systems. Python/Java/Kotlin.",
            "roles":["Software Engineer","ML Engineer","Data Scientist","iOS/Android Engineer","Product Manager"]
        },
        "Dropbox": {
            "logo":"Db","color":"#0061FF","hq":"San Francisco, CA",
            "style":"Distributed storage, sync engines, Python/Go/Rust. Strong systems design.",
            "roles":["Software Engineer","Backend Engineer","Data Engineer","ML Engineer","Security Engineer","Product Manager"]
        },
    },
    "🧩 Enterprise SaaS & Productivity": {
        "Atlassian": {
            "logo":"At","color":"#0052CC","hq":"Sydney / San Francisco",
            "style":"Jira/Confluence, Java/React, distributed systems. Team-collaboration products. Strong values culture.",
            "roles":["Software Engineer","Frontend Engineer","Data Scientist","ML Engineer","Product Manager","Site Reliability Engineer"]
        },
        "Notion": {
            "logo":"No","color":"#000000","hq":"San Francisco, CA",
            "style":"Productivity software, React/TypeScript, offline-first sync. Design-led, fast iteration.",
            "roles":["Software Engineer","Frontend Engineer","ML Engineer","Product Manager","Data Engineer"]
        },
        "Asana": {
            "logo":"As","color":"#F06A6A","hq":"San Francisco, CA",
            "style":"Work management SaaS, TypeScript/React/Python. Strong product engineering and mindfulness culture.",
            "roles":["Software Engineer","Frontend Engineer","Data Scientist","Product Manager","ML Engineer"]
        },
        "ServiceTitan": {
            "logo":"ST","color":"#4B2AAD","hq":"Glendale, CA",
            "style":"Vertical SaaS for trades, .NET/React, large data. Customer-obsessed scaling.",
            "roles":["Software Engineer","Data Engineer","ML Engineer","Product Manager","QA Engineer"]
        },
        "HubSpot": {
            "logo":"HS","color":"#FF7A59","hq":"Cambridge, MA",
            "style":"CRM/marketing SaaS, Java/React, microservices. Inbound culture, strong autonomy.",
            "roles":["Software Engineer","Data Scientist","ML Engineer","Product Manager","Solutions Engineer"]
        },
    },
    "🔬 Semiconductors & Hardware": {
        "Intel": {
            "logo":"In","color":"#0071C5","hq":"Santa Clara, CA",
            "style":"CPU/chip architecture, C/C++/Verilog, low-level systems. Deep hardware-software co-design.",
            "roles":["Hardware Engineer","Software Engineer","Firmware Engineer","Verification Engineer","ML Engineer","Design Engineer"]
        },
        "AMD": {
            "logo":"AMD","color":"#ED1C24","hq":"Santa Clara, CA",
            "style":"GPU/CPU design, parallel computing, C++/Verilog. HPC and graphics.",
            "roles":["Hardware Engineer","Software Engineer","GPU Architect","Verification Engineer","ML Engineer","Firmware Engineer"]
        },
        "Qualcomm": {
            "logo":"Qc","color":"#3253DC","hq":"San Diego, CA",
            "style":"Mobile SoC, wireless, embedded C/C++, DSP. Modems and edge AI.",
            "roles":["Embedded Engineer","Software Engineer","Hardware Engineer","ML Engineer","Modem Engineer","DSP Engineer"]
        },
        "Broadcom": {
            "logo":"Bc","color":"#CC092F","hq":"Palo Alto, CA",
            "style":"Networking silicon, ASIC, firmware, C/C++. Infrastructure hardware at scale.",
            "roles":["Hardware Engineer","Firmware Engineer","Software Engineer","Verification Engineer","ASIC Engineer"]
        },
    },
    "🛡️ Defense & Aerospace": {
        "SpaceX": {
            "logo":"SX","color":"#005288","hq":"Hawthorne, CA",
            "style":"Aerospace, embedded C++, real-time systems, flight software. High-pressure, mission-critical.",
            "roles":["Software Engineer","Embedded Engineer","Avionics Engineer","ML Engineer","Systems Engineer","Propulsion Engineer"]
        },
        "Anduril": {
            "logo":"An","color":"#1A1A1A","hq":"Costa Mesa, CA",
            "style":"Defense tech, autonomy, C++/Rust/Python, edge AI. Fast-moving, mission-driven.",
            "roles":["Software Engineer","ML Engineer","Embedded Engineer","Robotics Engineer","Data Scientist","Systems Engineer"]
        },
        "Lockheed Martin": {
            "logo":"LM","color":"#003B71","hq":"Bethesda, MD",
            "style":"Aerospace & defense systems, C++/Java, safety-critical. Clearance often required.",
            "roles":["Software Engineer","Systems Engineer","Embedded Engineer","Cybersecurity Analyst","Data Engineer"]
        },
    }
}
# ── Constants ──────────────────────────────────────────────────────────────────
ROLE_CATEGORIES = {
    "🤖 AI / ML":["ML Engineer","AI Research Intern","NLP Engineer","Computer Vision Engineer","Data Scientist","MLOps Engineer","AI Product Manager"],
    "💻 Software Engineering":["Frontend Developer","Backend Developer","Full Stack Developer","Mobile Developer","Software Engineer","Junior Developer","API Engineer"],
    "☁️ Cloud & DevOps":["DevOps Engineer","Cloud Engineer","Site Reliability Engineer","Infrastructure Engineer","Platform Engineer","CI/CD Engineer","Kubernetes Engineer","Cloud Security Engineer"],
    "🔐 Cybersecurity":["Cybersecurity Analyst","Penetration Tester","SOC Analyst","Cloud Security Engineer","Application Security Engineer","Security Architect","Incident Response Analyst","GRC Analyst"],
    "🗄️ Data":["Data Analyst","Data Engineer","Database Administrator","Business Intelligence Developer","Analytics Engineer","Big Data Engineer","Data Architect"],
    "🧪 QA & Testing":["QA Engineer","Automation Test Engineer","Performance Test Engineer","SDET","Manual QA Tester","Test Lead"],
    "🖧 IT":["IT Support Specialist","Network Engineer","Systems Administrator","Help Desk Technician","IT Project Manager","Cloud Administrator"],
    "📦 Product & Design":["Product Manager","Technical Project Manager","Scrum Master","Engineering Manager","Solutions Architect","UX Designer","UI Designer","Product Designer"],
    "🤖 Generative AI & LLM":["LLM Engineer","Prompt Engineer","RAG Engineer","AI Agent Developer","Applied AI Engineer","ML Platform Engineer","AI Safety Engineer"],
    "🧠 Data Science & Analytics":["Data Scientist","Applied Scientist","Research Scientist","Quantitative Analyst","Decision Scientist","Statistician"],
    "📱 Mobile & Embedded":["iOS Developer","Android Developer","React Native Developer","Flutter Developer","Embedded Systems Engineer","Firmware Engineer"],
    "🕸️ Web & Frontend":["React Developer","Angular Developer","Vue Developer","Web Developer","UI Engineer","WebGL/Three.js Developer"],
    "⛓️ Blockchain & Web3":["Blockchain Engineer","Smart Contract Developer","Solidity Engineer","Web3 Developer","DeFi Engineer"],
    "🎮 Game Development":["Game Engineer","Gameplay Programmer","Graphics Engineer","Game Designer","Unity Developer","Unreal Engine Developer"],
}
DIFFICULTIES = {"🎓 Intern":"intern","🟢 Entry":"entry","🔵 Junior":"junior","🟡 Mid-Level":"mid","🟠 Senior":"senior","🔴 Staff/Lead":"staff","⚫ Principal":"principal"}
LANGUAGES    = {"🇺🇸 English":"English","🇪🇸 Spanish":"Spanish","🇮🇳 Hindi":"Hindi","🇫🇷 French":"French","🇩🇪 German":"German","🇧🇷 Portuguese":"Portuguese","🇨🇳 Chinese":"Chinese","🇯🇵 Japanese":"Japanese"}
PROVIDERS    = {"🟣 Claude (Anthropic)":"claude","🟢 ChatGPT (OpenAI)":"openai","🔵 Gemini (Google)":"gemini"}

# Current model options per provider (June 2026). "Custom…" lets you type any future model string.
MODEL_OPTIONS = {
    "claude": ["claude-opus-4-8","claude-sonnet-4-6","claude-haiku-4-5-20251001","claude-sonnet-4-20250514","Custom…"],
    "openai": ["gpt-5.5","gpt-5.4","gpt-5-mini","gpt-4o","Custom…"],
    "gemini": ["gemini-3.5-flash","gemini-3.1-pro","gemini-3-flash","gemini-2.5-flash","Auto-detect","Custom…"],
}
DEFAULT_MODEL = {"claude":"claude-opus-4-8","openai":"gpt-5.5","gemini":"gemini-3.5-flash"}
DEFAULT_TIME = {"intern":180,"entry":180,"junior":240,"mid":300,"senior":360,"staff":420,"principal":480}
GENERAL = "General / No specific company"

# Languages offered in the in-interview code editor (broad coverage)
CODE_LANGUAGES = [
    "Python","Java","C","C++","C#","JavaScript","TypeScript","Go","Rust","Ruby",
    "PHP","Swift","Kotlin","Scala","R","SQL","MATLAB","Perl","Objective-C","Dart",
    "Haskell","Lua","Bash / Shell","Julia","Groovy","Visual Basic .NET","F#","Elixir",
    "Clojure","Assembly","COBOL","Fortran","Pascal","Racket","Erlang","OCaml","Solidity","Zig",
]

# ══════════════════════════════════════════════════════════════════════════════
#  OFFLINE DEMO MODE — runs with NO API key / no network (judge-day insurance)
# ══════════════════════════════════════════════════════════════════════════════
DEMO_QUESTIONS = [
    ("CODING",     "Write a function that returns the two indices of the numbers in an array that add up to a target value."),
    ("CONCEPT",    "Explain the difference between a process and a thread, and when you'd choose one over the other."),
    ("CODING",     "Given a string, write a function that returns true if it is a palindrome, ignoring spaces and case."),
    ("DESIGN",     "Design a URL shortener like bit.ly. Cover the API, data model, and how you'd scale reads."),
    ("BEHAVIORAL", "Tell me about a time you disagreed with a teammate. How did you handle it? (Use the STAR format.)"),
    ("CONCEPT",    "What is the time and space complexity of binary search, and what must be true about the input?"),
    ("CODING",     "Write a function that counts how many times each word appears in a sentence and returns the result."),
    ("BEHAVIORAL", "Describe a project you're proud of. What was your specific contribution and what did you learn?"),
    ("DESIGN",     "How would you design a rate limiter for an API? Discuss algorithms and trade-offs."),
    ("CONCEPT",    "Explain what a hash table is, how collisions are handled, and its average-case lookup cost."),
    ("CODING",     "Implement a function that reverses a singly linked list and returns the new head."),
    ("BEHAVIORAL", "Tell me about a time you received critical feedback. What did you change afterwards?"),
    ("CONCEPT",    "What is the difference between SQL and NoSQL databases? Give a use case for each."),
    ("DESIGN",     "Design a notification system that can send email, SMS, and push to millions of users."),
    ("CODING",     "Write a function that finds the maximum sum of any contiguous subarray (Kadane's algorithm)."),
    ("CONCEPT",    "Explain the concept of overfitting in machine learning and three ways to reduce it."),
    ("BEHAVIORAL", "Describe a situation where you had to learn a new technology quickly. How did you approach it?"),
    ("CODING",     "Given two sorted arrays, write a function that merges them into one sorted array."),
    ("CONCEPT",    "What happens, step by step, when you type a URL into a browser and press enter?"),
    ("DESIGN",     "Design the backend for a ride-sharing app's driver-rider matching feature."),
]

def demo_questions(n, q_type):
    """Return (questions, tags) from the offline bank, honoring the question focus."""
    bank=DEMO_QUESTIONS[:]
    if q_type=="behavioral":
        bank=[x for x in bank if x[0]=="BEHAVIORAL"] or DEMO_QUESTIONS[:]
    elif q_type=="technical_coding":
        bank=[x for x in bank if x[0]!="BEHAVIORAL"] or DEMO_QUESTIONS[:]
    bank=bank[:n] if len(bank)>=n else (bank*((n//len(bank))+1))[:n]
    tags=[t for t,_ in bank]; qs=[q for _,q in bank]
    return qs, tags

def demo_feedback(answer):
    """Deterministic, offline feedback so the app works with no API key."""
    a=(answer or "").strip()
    wc=len(a.split())
    score = 8 if wc>=40 else (6 if wc>=15 else (4 if wc>=4 else 2))
    return {"SCORE":score,
            "STRENGTH":"Clear, structured response with relevant detail." if wc>=15 else "You attempted the question.",
            "IMPROVEMENT":"Add a concrete example and state trade-offs explicitly." if wc>=15 else "Expand with more detail, examples, and structure.",
            "IDEAL":"(Offline demo) A strong answer states the approach, walks through an example, notes edge cases, and gives complexity or trade-offs.",
            "COMPLEXITY":"N/A","TIP":"In offline demo mode, scoring is based on structure and depth. Add an API key for full AI evaluation."}

def demo_code_eval(code):
    """Offline heuristic code check (no execution)."""
    c=(code or "").strip()
    has_logic = any(k in c for k in ["return","def ","function","for","while","if","=>","class","print","cout","System.out"])
    correct = len(c)>=30 and has_logic
    return {"VERDICT":"CORRECT" if correct else "INCORRECT",
            "OUTPUT":"(Offline demo) Looks like a complete solution." if correct else "(Offline demo) Code looks incomplete or missing core logic.",
            "ISSUES":"none — looks structurally complete" if correct else "Add the core logic and a return/print of the result.",
            "COMPLEXITY":"N/A",
            "SCORE":8 if correct else 0,
            "TIP":"Offline demo grades structure only. Add an API key for a real dry-run and correctness check."}

# ══════════════════════════════════════════════════════════════════════════════
#  LOCKDOWN JS  — configurable anti-cheat (recruiter chooses which tech is active)
# ══════════════════════════════════════════════════════════════════════════════
def lockdown_js(opts=None):
    """Build the lockdown <script> from a proctoring-options dict. Missing keys default ON."""
    o = default_proctoring()
    if opts:
        for k, v in opts.items():
            if k in o: o[k] = bool(v)
    def J(b): return "true" if b else "false"
    flags = ("var O={right_click:%s,copy_paste:%s,devtools:%s,shortcuts:%s,screenshot:%s,"
             "tab_switch:%s,selection:%s};" % (
        J(o["right_click"]), J(o["copy_paste"]), J(o["devtools"]), J(o["shortcuts"]),
        J(o["screenshot"]), J(o["tab_switch"]), J(o["selection"])))
    return """<script>
(function(){'use strict';
  """ + flags + """
  if(O.right_click) document.addEventListener('contextmenu',function(e){e.preventDefault();showW('Right-click disabled.');logV('Right-click');return false;});
  document.addEventListener('keydown',function(e){
    var b=false,r='';
    if(e.ctrlKey||e.metaKey){
      if(O.copy_paste){var cm={c:'Copy',v:'Paste',x:'Cut'}; if(cm[e.key.toLowerCase()]){r=cm[e.key.toLowerCase()]+' disabled.';b=true;}}
      if(O.shortcuts){var sm={u:'Source',s:'Save',p:'Print',f:'Find'}; if(sm[e.key.toLowerCase()]){r=sm[e.key.toLowerCase()]+' disabled.';b=true;}}
      if(O.devtools && e.shiftKey && ['I','J','C'].includes(e.key)){r='DevTools disabled.';b=true;}
    }
    if(O.devtools && e.key==='F12'){r='DevTools disabled.';b=true;}
    if(O.screenshot && e.key==='PrintScreen'){r='Screenshots discouraged.';b=true;}
    if(b){e.preventDefault();e.stopPropagation();if(r)showW(r);logV(r);return false;}
  });
  if(O.copy_paste){
    document.addEventListener('copy',function(e){e.preventDefault();showW('Copy disabled.');logV('Copy');});
    document.addEventListener('cut',function(e){e.preventDefault();showW('Cut disabled.');logV('Cut');});
    document.addEventListener('paste',function(e){
      if(document.activeElement&&document.activeElement.tagName==='TEXTAREA')return;
      e.preventDefault();showW('Paste disabled.');logV('Paste');
    });
  }
  if(O.selection){
    document.addEventListener('selectstart',function(e){ if(e.target.tagName!=='TEXTAREA'&&e.target.tagName!=='INPUT')e.preventDefault(); });
    document.addEventListener('dragstart',function(e){e.preventDefault();});
  }
  if(O.tab_switch){
    var ts=0;
    document.addEventListener('visibilitychange',function(){
      if(document.hidden){ts++;sessionStorage.setItem('ts',ts);logV('Tab switch #'+ts);
        if(ts>=3)showW('WARNING: '+ts+' tab switches recorded.');}
    });
    var bc=0;window.addEventListener('blur',function(){bc++;if(bc>2)logV('Blur #'+bc);});
  }
  if(O.devtools){
    setInterval(function(){
      if(window.outerWidth-window.innerWidth>160||window.outerHeight-window.innerHeight>160){
        showW('DevTools detected.');logV('DevTools');}},2000);
  }
  function showW(msg){var ex=document.getElementById('lkw');if(ex)ex.remove();
    var d=document.createElement('div');d.id='lkw';
    d.style.cssText='position:fixed;top:16px;left:50%;transform:translateX(-50%);background:#e53e3e;color:#fff;padding:12px 24px;border-radius:8px;font-size:14px;font-weight:600;z-index:999999;pointer-events:none';
    d.textContent='🔒 '+msg;document.body.appendChild(d);setTimeout(function(){if(d.parentNode)d.remove();},3000);}
  function logV(a){if(!a)return;var l=JSON.parse(sessionStorage.getItem('violations')||'[]');
    l.push({time:new Date().toISOString(),action:a});sessionStorage.setItem('violations',JSON.stringify(l));}
  console.log('%c🔒 SECURE PROCTORED MODE','color:red;font-size:18px;font-weight:bold;');
})();</script>"""

# All-on default used by the standard (non-custom) candidate flow.
LOCKDOWN_JS = lockdown_js()

# ── Proctor panel: camera (any laptop cam) + live noise meter + fullscreen ─────
def proctor_panel(compact=False, show_fullscreen=True, gaze=False, precheck=False, autofs=False,
                  hide_voice_ui=False, start_gate=False, start_label="Start Interview", voice=True):
    cam_w = 200 if compact else 320
    cam_h = 150 if compact else 240
    voice_flag = "1" if voice else "0"
    fs_btn = "" if not show_fullscreen else """
      <button id="fsbtn" style="margin-top:8px;width:100%;background:linear-gradient(135deg,#5b46d6,#7c3aed);color:#fff;border:none;border-radius:10px;padding:9px 0;font-size:13px;font-weight:700;cursor:pointer">⛶ Enter full-screen exam window</button>
    """
    cam_sel = "" if compact else """
      <select id="camsel" style="margin-top:6px;width:100%;background:#15151f;color:#e7e7f0;border:1px solid #2a2a40;border-radius:8px;padding:6px"></select>
    """
    # Pre-check UI: auto face-detect status + auto-captured photo thumbnail
    precheck_ui = """
      <div id="facestat" style="font-size:13px;font-weight:700;color:#cfcfe0;margin-top:8px;text-align:center">🔍 Detecting your face…</div>
      <canvas id="snapshot" style="display:none;width:120px;border-radius:10px;border:2px solid #16a34a;margin:8px auto 0"></canvas>
    """ if precheck else ""
    # In-component START button (only revealed after camera + face detection succeed)
    start_ui = """
      <button id="ppstart" style="display:none;margin-top:12px;width:100%;background:linear-gradient(135deg,#16a34a,#22c55e);color:#fff;border:none;border-radius:12px;padding:14px 0;font-size:16px;font-weight:800;cursor:pointer;box-shadow:0 8px 20px -8px rgba(34,197,94,.7)">🚀 START INTERVIEW</button>
      <div id="ppwait" style="margin-top:10px;text-align:center;font-size:13px;font-weight:700;color:#cfcfe0">⏳ Waiting for camera &amp; face detection… the Start button appears when ready.</div>
    """ if start_gate else ""
    # Voice meter visuals: shown normally, or hidden (detection still runs) when hide_voice_ui
    voice_wrap_style = "display:none" if hide_voice_ui else "margin-top:6px"
    gaze_ui = """
      <div id="gazestat" style="font-size:12.5px;font-weight:700;color:#cfcfe0;margin-top:6px;text-align:center">👁️ Gaze: loading detector…</div>
      <div id="gazebanner" style="display:none;margin-top:6px;border-radius:10px;padding:8px 10px;font-size:12px;font-weight:700;text-align:center"></div>
    """ if gaze else ""
    gaze_script = GAZE_SCRIPT if gaze else ""
    autofs_flag = "1" if autofs else "0"
    precheck_flag = "1" if precheck else "0"
    startgate_flag = "1" if start_gate else "0"
    return """
<div style="display:flex;flex-direction:column;align-items:stretch;gap:6px;font-family:Inter,sans-serif">
  <video id="pcam" autoplay playsinline muted
     style="width:__CAMW__px;height:__CAMH__px;border-radius:12px;border:3px solid #7c5cff;background:#000;object-fit:cover;transform:scaleX(-1);align-self:center"></video>
  <div id="camstat" style="font-size:12.5px;font-weight:700;color:#e53e3e;text-align:center">● Requesting camera…</div>
  __CAMSEL__
  __PRECHECKUI__
  <div style="__VOICEWRAP__">
    <div style="font-size:11px;color:#cfcfe0;margin-bottom:3px">🗣️ Talking / human-voice check <span style="color:#9a9ab0">(fan / AC ignored)</span></div>
    <div style="background:#23233a;border-radius:99px;height:10px;overflow:hidden">
      <div id="noisebar" style="height:10px;width:0%;background:#16a34a;transition:width .1s"></div>
    </div>
    <div id="noisestat" style="font-size:12px;font-weight:700;color:#16a34a;margin-top:4px;text-align:center">Checking…</div>
  </div>
  <div id="voicewarn" style="display:none;margin-top:6px;border-radius:10px;padding:8px 10px;font-size:12px;font-weight:700;text-align:center;background:#d97706;color:#fff"></div>
  __GAZEUI__
  __STARTUI__
  __FSBTN__
</div>
<script>
(function(){
  var AUTOFS=__AUTOFS__, PRECHECK=__PRECHECK__, STARTGATE=__STARTGATE__, VOICE=__VOICE__;
  var STARTLABEL="__STARTLABEL__";
  var v=document.getElementById('pcam'), cs=document.getElementById('camstat');
  var nb=document.getElementById('noisebar'), ns=document.getElementById('noisestat');
  var sel=document.getElementById('camsel'), fsb=document.getElementById('fsbtn');
  var vw=document.getElementById('voicewarn');
  function logV(a){try{var l=JSON.parse(sessionStorage.getItem('violations')||'[]');l.push({time:new Date().toISOString(),action:a});sessionStorage.setItem('violations',JSON.stringify(l));}catch(e){}}
  window.__logV=logV;
  function pdoc(){ try{ return (window.parent&&window.parent.document)?window.parent.document:document; }catch(e){ return document; } }
  function pbody(){ try{ return pdoc().body||document.body; }catch(e){ return document.body; } }
  // blink CSS injected into BOTH this frame and the parent (for full-screen overlays)
  function injectBlink(d){ try{ if(d.getElementById('pp-blink-css'))return; var bs=d.createElement('style'); bs.id='pp-blink-css';
    bs.textContent='@keyframes ppblink{0%,100%{opacity:1}50%{opacity:.18}} .pp-blink{animation:ppblink .55s linear infinite}'; (d.head||d.body).appendChild(bs);}catch(e){} }
  injectBlink(document); injectBlink(pdoc());
  window.__pp_pdoc=pdoc; window.__pp_pbody=pbody; window.__pp_injectBlink=injectBlink;
  // ---- Full-screen helper (parent) ----
  function goFS(){
    try{ var d=pdoc(); var el=d.documentElement; (el.requestFullscreen||el.webkitRequestFullscreen||el.msRequestFullscreen).call(el);
    }catch(e){ try{var el2=document.documentElement;(el2.requestFullscreen||el2.webkitRequestFullscreen).call(el2);}catch(e2){} }
  }
  window.__pp_goFS=goFS;
  // ---- Auto full-screen on first user gesture (browsers require a gesture) ----
  if(AUTOFS){
    try{ goFS(); }catch(e){}
    try{
      var pd=pdoc();
      pd.addEventListener('click', function f(){ goFS(); pd.removeEventListener('click',f); }, {once:true});
      pd.addEventListener('keydown', function f2(){ goFS(); pd.removeEventListener('keydown',f2); }, {once:true});
    }catch(e){}
  }
  // ---- human-voice warning (first time -> full-width warning bar over the screen) ----
  var voiceWarnedUntil=0;
  window.__voiceWarn=function(){
    var now=Date.now(); if(now<voiceWarnedUntil) return; voiceWarnedUntil=now+8000;
    logV('Human voice WARNING shown');
    try{
      var d=pdoc(); injectBlink(d);
      var bar=d.getElementById('pp-voicebar');
      if(!bar){ bar=d.createElement('div'); bar.id='pp-voicebar';
        bar.style.cssText='position:fixed;top:0;left:0;right:0;z-index:2147483646;background:#d97706;color:#fff;'+
          'font-family:Inter,sans-serif;font-weight:800;font-size:16px;text-align:center;padding:14px 12px;box-shadow:0 4px 16px rgba(0,0,0,.4)';
        (d.body||document.body).appendChild(bar); }
      bar.className='pp-blink'; bar.style.display='block';
      bar.textContent='🗣️ WARNING — Please stay SILENT. Talking is detected and recorded during the interview.';
      setTimeout(function(){ if(bar){ bar.className=''; bar.style.display='none'; } }, 6000);
    }catch(e){
      if(vw){ vw.style.display='block'; vw.className='pp-blink'; vw.textContent='🗣️ WARNING — please stay silent.'; setTimeout(function(){ if(vw){vw.className='';vw.style.display='none';} },6000); }
    }
  };
  // ---- Start gate: hide the real Streamlit Start button until checks pass ----
  function findStartBtn(){ try{ var d=pdoc(); var bs=d.querySelectorAll('button'); for(var i=0;i<bs.length;i++){ var t=bs[i].innerText||bs[i].textContent||''; if(t.indexOf(STARTLABEL)>=0) return bs[i]; } }catch(e){} return null; }
  function setStartVisible(show){ var b=findStartBtn(); if(b){ var w=b.closest('[data-testid="stButton"]')||b.parentElement; if(w) w.style.display= show?'block':'none'; } return b; }
  if(STARTGATE){
    var ppstart=document.getElementById('ppstart'), ppwait=document.getElementById('ppwait');
    setStartVisible(false);
    var hk=setInterval(function(){ if(!(window.__pcamReady&&window.__faceOK)) setStartVisible(false); },350);
    var rk=setInterval(function(){
      if(window.__pcamReady && window.__faceOK){
        clearInterval(hk); clearInterval(rk);
        if(ppstart) ppstart.style.display='block';
        if(ppwait){ ppwait.textContent='✅ Camera and face detected — press START to begin.'; ppwait.style.color='#16a34a'; }
        setStartVisible(true);
      }
    },350);
    if(ppstart){ ppstart.addEventListener('click',function(){ goFS(); var b=setStartVisible(true); if(b) b.click(); }); }
  }
  var curStream=null;
  function start(deviceId){
    if(curStream){curStream.getTracks().forEach(function(t){t.stop();});}
    var c={video: deviceId?{deviceId:{exact:deviceId}}:true, audio:true};
    navigator.mediaDevices.getUserMedia(c).then(function(stream){
      curStream=stream; v.srcObject=stream; window.__pcamReady=true;
      cs.textContent='🔴 REC — recording in progress'; cs.style.color='#16a34a';
      if(sel){navigator.mediaDevices.enumerateDevices().then(function(ds){
        sel.innerHTML='';
        ds.filter(function(d){return d.kind==='videoinput';}).forEach(function(d,i){
          var o=document.createElement('option'); o.value=d.deviceId;
          o.text=d.label||('Camera '+(i+1)); sel.appendChild(o);
        });
      });}
      try{
        // Human-VOICE detector: human speech sits mostly in 300-3400 Hz AND fluctuates
        // (syllables). Steady machine noise (fan/AC) is low-modulation and/or low-band,
        // so it is ignored. We flag only talking.
        if(!VOICE) throw new Error('voice off');
        var AC=window.AudioContext||window.webkitAudioContext; var ac=new AC();
        var src=ac.createMediaStreamSource(stream); var an=ac.createAnalyser();
        an.fftSize=2048; an.smoothingTimeConstant=0.55; src.connect(an);
        var freq=new Uint8Array(an.frequencyBinCount);
        var sr=ac.sampleRate||44100, binHz=sr/an.fftSize;
        var loBin=Math.max(1,Math.floor(300/binHz)), hiBin=Math.ceil(3400/binHz);
        var hist=[], hi=0;
        setInterval(function(){
          an.getByteFrequencyData(freq);
          var voice=0, total=0, n=0;
          for(var i=0;i<freq.length;i++){ total+=freq[i]; if(i>=loBin && i<=hiBin){ voice+=freq[i]; n++; } }
          var voiceAvg = voice/Math.max(1,n);
          var ratio = total>0 ? (voice/total) : 0;            // energy concentrated in voice band?
          hist.push(voiceAvg); if(hist.length>14) hist.shift();
          var mean=hist.reduce(function(a,b){return a+b;},0)/hist.length;
          var varc=hist.reduce(function(a,b){return a+(b-mean)*(b-mean);},0)/hist.length;
          var modulation=Math.sqrt(varc);                     // speech fluctuates; fan/AC is steady
          // Human voice = strong mid-band energy + concentrated in speech band + fluctuating
          var isVoice = (voiceAvg>26 && ratio>0.42 && modulation>5.5);
          var pct=Math.min(100,Math.round(voiceAvg*1.7));
          nb.style.width=pct+'%';
          if(isVoice){
            nb.style.background='#e53e3e';
            ns.textContent='🗣️ Human voice detected — please stay silent';
            ns.style.color='#e53e3e'; hi++;
            if(hi===4){ logV('Human voice / talking detected'); if(window.__voiceWarn) window.__voiceWarn(); }
            if(hi>=10){ hi=0; }
          } else {
            nb.style.background='#16a34a';
            ns.textContent='✅ No talking (machine noise is OK)';
            ns.style.color='#16a34a'; hi=0;
          }
        },150);
      }catch(e){ns.textContent='Mic unavailable';ns.style.color='#a9a9bd';}
    }).catch(function(err){
      cs.textContent='⚠ Camera/mic blocked — click the 🎥 icon in the address bar to allow'; cs.style.color='#e53e3e';
      ns.textContent='—';
    });
  }
  if(navigator.mediaDevices&&navigator.mediaDevices.getUserMedia){ start(null); }
  else{ cs.textContent='⚠ Camera not supported in this browser'; }
  if(sel){ sel.addEventListener('change',function(){ start(sel.value); }); }
  if(fsb){
    fsb.addEventListener('click',function(){ goFS(); });
    try{ pdoc().addEventListener('fullscreenchange',function(){ if(!pdoc().fullscreenElement){logV('Exited full-screen');} }); }catch(e){}
  }
})();
</script>
__GAZESCRIPT__
""".replace("__CAMW__", str(cam_w)).replace("__CAMH__", str(cam_h)) \
   .replace("__CAMSEL__", cam_sel).replace("__GAZEUI__", gaze_ui) \
   .replace("__PRECHECKUI__", precheck_ui).replace("__AUTOFS__", autofs_flag) \
   .replace("__PRECHECK__", precheck_flag).replace("__STARTGATE__", startgate_flag) \
   .replace("__STARTLABEL__", start_label).replace("__STARTUI__", start_ui) \
   .replace("__VOICEWRAP__", voice_wrap_style).replace("__VOICE__", voice_flag) \
   .replace("__FSBTN__", fs_btn).replace("__GAZESCRIPT__", gaze_script)

# MediaPipe FaceMesh gaze/head-direction detector (client-side, best effort).
# First sustained look-away → warning; second → terminate overlay + flag.
GAZE_SCRIPT = """
<script type="module">
import {FaceLandmarker, FilesetResolver} from "https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.12/vision_bundle.mjs";
const stat=document.getElementById('gazestat');
const banner=document.getElementById('gazebanner');
function logV(a){ if(window.__logV) window.__logV(a); }
let lm=null, away=0, events=0, warned=false, terminated=false, lastT=-1, cooldownUntil=0;
function injectBlink(){
  if(document.getElementById('gz-blink-css')) return;
  var s=document.createElement('style'); s.id='gz-blink-css';
  s.textContent='@keyframes gzblink{0%,100%{opacity:1;}50%{opacity:.15;}} .gz-blink{animation:gzblink .6s linear infinite;}';
  document.head.appendChild(s);
  try{ if(window.__pp_injectBlink) window.__pp_injectBlink(window.__pp_pdoc()); }catch(e){}
}
function ovHost(){ try{ return (window.__pp_pbody)?window.__pp_pbody():document.body; }catch(e){ return document.body; } }
function showWarning(){
  injectBlink();
  // single inline red banner inside the camera panel
  if(banner){
    banner.style.display='block';
    banner.style.background='#e11d1d'; banner.style.color='#fff';
    banner.style.fontSize='12.5px'; banner.style.lineHeight='1.45';
    banner.className='gz-blink';
    banner.textContent='⚠ WARNING — Face the screen. Turning away again will END the exam.';
  }
  // BIG, unmissable RED full-screen warning (shown ONCE after the 2nd head turn)
  var host=ovHost();
  var ov=host.querySelector('#gz-warn-ov');
  if(!ov){
    ov=document.createElement('div'); ov.id='gz-warn-ov';
    ov.style.cssText='position:fixed;inset:0;z-index:2147483646;display:flex;flex-direction:column;'+
      'align-items:center;justify-content:center;text-align:center;padding:18px;'+
      'background:rgba(225,29,29,.93);color:#fff;font-family:Inter,sans-serif';
    host.appendChild(ov);
  }
  ov.className='gz-blink'; ov.style.display='flex';
  ov.innerHTML='<div style="font-size:64px">⚠️</div>'+
    '<div style="font-size:30px;font-weight:800;margin:8px 0">FACE THE SCREEN</div>'+
    '<div style="font-size:16px;font-weight:600;max-width:540px;line-height:1.6">You have turned your head away twice. '+
    'This is your only warning — if you look away again, the exam will be <b>TERMINATED as FAILED</b>.</div>';
  // auto-dismiss the overlay after 6s (termination is governed purely by the head-turn count)
  setTimeout(function(){
    if(banner){ banner.className=''; banner.style.display='none'; }
    if(ov){ ov.className=''; ov.style.display='none'; }
  }, 6000);
}
function endInterviewToReport(){
  // Remove any full-screen proctor overlays so they don't linger on the next page.
  try{
    var d=(window.__pp_pdoc?window.__pp_pdoc():document);
    ['gz-term-ov','gz-warn-ov','pp-voicebar'].forEach(function(id){ var e=d.getElementById(id); if(e&&e.parentNode) e.parentNode.removeChild(e); });
  }catch(e){}
  // Click the real Streamlit "End & see FAILED report" button in the parent app.
  try{
    var d2=(window.__pp_pdoc?window.__pp_pdoc():document);
    var bs=d2.querySelectorAll('button');
    for(var i=0;i<bs.length;i++){ var t=bs[i].innerText||bs[i].textContent||''; if(t.indexOf('FAILED report')>=0){ bs[i].click(); return true; } }
  }catch(e2){}
  try{ // fallback: search this frame too
    var bs2=document.querySelectorAll('button');
    for(var j=0;j<bs2.length;j++){ var t2=bs2[j].innerText||bs2[j].textContent||''; if(t2.indexOf('FAILED report')>=0){ bs2[j].click(); return true; } }
  }catch(e3){}
  return false;
}
function terminate(){
  terminated=true;
  try{ sessionStorage.setItem('proctor_terminated','1'); }catch(e){}
  logV('Proctor TERMINATED: head turned away 4 times');
  var host=ovHost();
  var ov=document.createElement('div'); ov.id='gz-term-ov';
  ov.style.cssText='position:fixed;inset:0;background:rgba(8,8,16,.97);z-index:2147483647;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;font-family:Inter,sans-serif;text-align:center;padding:24px';
  ov.innerHTML='<div style=\"font-size:74px\">⛔</div><div style=\"font-size:30px;font-weight:800;margin:8px 0\">EXAM TERMINATED</div>'+
    '<div style=\"font-size:16px;color:#ffb4b4;max-width:560px;line-height:1.6\">Your head was turned away from the screen 4 times after a clear warning. '+
    'Per the proctoring rules the interview has ended and your result is recorded as <b>FAILED</b>.</div>'+
    '<button id=\"gz-endbtn\" style=\"margin-top:22px;background:#e11d1d;color:#fff;border:none;border-radius:12px;padding:15px 30px;font-size:17px;font-weight:800;cursor:pointer;box-shadow:0 8px 22px -8px rgba(225,29,29,.8)\">⛔ See my FAILED report &amp; certificate</button>'+
    '<div id=\"gz-auto\" style=\"font-size:13px;color:#cdd6f4;margin-top:14px\">Taking you to your report automatically…</div>';
  host.appendChild(ov);
  var eb=ov.querySelector('#gz-endbtn');
  if(eb){ eb.addEventListener('click', function(){ endInterviewToReport(); }); }
  setTimeout(function(){ endInterviewToReport(); }, 2200);
}
function handle(isAway){
  if(terminated) return;
  var now=Date.now();
  if(isAway) away++; else away=Math.max(0,away-2);
  if(away < 18) return;               // need a sustained (~1.2s) head-turn to count as ONE event
  away=0;
  if(now < cooldownUntil) return;     // one continuous turn = one event (debounce)
  cooldownUntil = now + 2500;
  events++;
  logV('Head turn #'+events);
  if(events===2 && !warned){ warned=true; showWarning(); }   // 2nd turn -> single RED warning
  if(events>=4){ terminate(); }                              // 4th turn -> terminate
}
async function init(){
  try{
    const fileset=await FilesetResolver.forVisionTasks("https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.12/wasm");
    lm=await FaceLandmarker.createFromOptions(fileset,{
      baseOptions:{modelAssetPath:"https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"},
      runningMode:"VIDEO", numFaces:1, outputFacialTransformationMatrixes:false
    });
    if(stat){ stat.textContent='👁️ Gaze monitoring active'; stat.style.color='#16a34a'; }
    loop();
  }catch(e){ if(stat){ stat.textContent='👁️ Gaze detector unavailable here'; } }
}
function loop(){
  const v=document.getElementById('pcam');
  if(lm && v && v.readyState>=2 && window.__pcamReady){
    const t=v.currentTime;
    if(t!==lastT){
      lastT=t;
      let isAway=true;
      try{
        const res=lm.detectForVideo(v, performance.now());
        if(res && res.faceLandmarks && res.faceLandmarks.length>0){
          const p=res.faceLandmarks[0];
          const nose=p[1], left=p[234], right=p[454], top=p[10], chin=p[152];
          const cx=(left.x+right.x)/2, w=Math.abs(right.x-left.x)||0.001;
          const yaw=(nose.x-cx)/w;                 // + right / - left
          const cy=(top.y+chin.y)/2, h=Math.abs(chin.y-top.y)||0.001;
          const pitch=(nose.y-cy)/h;               // + down / - up
          // iris (refined landmarks): left iris 468, right iris 473
          let eyeOff=false;
          if(p.length>=478){
            const liris=p[468], le1=p[33], le2=p[133];
            const lr=(liris.x-Math.min(le1.x,le2.x))/(Math.abs(le2.x-le1.x)||0.001);
            const riris=p[473], re1=p[362], re2=p[263];
            const rr=(riris.x-Math.min(re1.x,re2.x))/(Math.abs(re2.x-re1.x)||0.001);
            eyeOff=(lr<0.30||lr>0.70||rr<0.30||rr>0.70);
          }
          isAway=(Math.abs(yaw)>0.13 || pitch>0.32 || pitch<-0.28 || eyeOff);
          window.__faceOK=true;   // a face is present in frame -> camera+detection working
          if(stat && !terminated){
            if(events>0){ stat.textContent='👁️ Head turns: '+events+' / 4'; stat.style.color = events>=2 ? '#e11d1d' : '#cfcfe0'; }
            else { stat.textContent='👁️ Gaze monitoring active'; stat.style.color='#cfcfe0'; }
          }
          // ---- Pre-check: auto-capture a photo once a clear, centered face is seen ----
          if(!window.__faceShot && !isAway){
            var snap=document.getElementById('snapshot'), fst=document.getElementById('facestat');
            if(snap){
              try{
                snap.width=v.videoWidth||320; snap.height=v.videoHeight||240;
                var ctx=snap.getContext('2d');
                ctx.save(); ctx.scale(-1,1); ctx.drawImage(v,-snap.width,0,snap.width,snap.height); ctx.restore();
                snap.style.display='block'; window.__faceShot=true; window.__faceOK=true;
                if(fst){ fst.textContent='✅ Face detected — photo captured automatically'; fst.style.color='#16a34a'; }
                logV('Face detected + auto photo captured');
              }catch(e){ window.__faceOK=true; if(fst){ fst.textContent='✅ Face detected'; fst.style.color='#16a34a'; } }
            }
          }
        } else { isAway=true; }  // no face = looking away / absent
      }catch(e){ isAway=false; }
      handle(isAway);
    }
  }
  if(!terminated) requestAnimationFrame(loop);
}
init();
</script>
"""

# Backward-compatible name
CAMERA_JS = proctor_panel(compact=True, show_fullscreen=False)

# ── Live countdown timer (ticks client-side every second; auto-submits at 0) ───
def live_timer(remaining, submit_label="Submit Answer", compact=False):
    size = "16px" if compact else "22px"
    pad  = "8px 12px" if compact else "12px 18px"
    return ("""
<div id="lt-wrap" style="display:flex;align-items:center;justify-content:center;gap:8px;
     background:linear-gradient(135deg,#1a1a2e,#2a2350);color:#fff;border-radius:12px;
     padding:__PAD__;font-family:'Inter',monospace;font-weight:800;font-size:__SIZE__;
     box-shadow:0 8px 22px -10px rgba(124,92,255,.6);width:100%">
  <span style="font-size:.8em">⏱</span><span id="lt">--:--</span>
</div>
<style>@keyframes ltpulse{0%,100%{opacity:1}50%{opacity:.5}}</style>
<script>
(function(){
  var total=__REMAIN__;
  var el=document.getElementById('lt'), wrap=document.getElementById('lt-wrap');
  var deadline=Date.now()+total*1000, fired=false;
  function pdoc(){try{return (window.parent&&window.parent.document)?window.parent.document:document;}catch(e){return document;}}
  function fmt(s){var m=Math.floor(s/60),x=s%60;return (m<10?'0':'')+m+':'+(x<10?'0':'')+x;}
  function clickSubmit(){ try{ var d=pdoc(), bs=d.querySelectorAll('button');
    for(var i=0;i<bs.length;i++){ var t=bs[i].innerText||bs[i].textContent||''; if(t.indexOf('__LABEL__')>=0){ bs[i].click(); return; } } }catch(e){} }
  function tick(){
    var rem=Math.max(0,Math.round((deadline-Date.now())/1000));
    el.textContent=fmt(rem);
    if(rem<=60){ wrap.style.background='linear-gradient(135deg,#c53030,#8b1a1a)'; wrap.style.animation='ltpulse 1s infinite'; }
    if(rem<=0 && !fired){ fired=true; el.textContent='00:00'; clearInterval(iv); clickSubmit(); }
  }
  var iv=setInterval(tick,1000); tick();
})();
</script>
""".replace("__REMAIN__",str(int(remaining))).replace("__LABEL__",submit_label)
   .replace("__SIZE__",size).replace("__PAD__",pad))

# ── CSS  (professional theme: gradients, texture, depth) ───────────────────────
st.markdown("""<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');

  /* App background — deep black with subtle dark texture */
  .stApp{
    background:
      radial-gradient(900px 520px at 12% -8%, #1c1740 0%, rgba(28,23,64,0) 55%),
      radial-gradient(820px 460px at 100% 0%, #10243f 0%, rgba(16,36,63,0) 50%),
      linear-gradient(180deg, #07070d 0%, #0b0b14 100%);
    color:#e7e7f0;
  }
  html, body, [class*="css"]{ font-family:'Inter',-apple-system,Segoe UI,sans-serif; color:#e7e7f0; }

  /* Make Streamlit's default text readable on black */
  .stApp, .stMarkdown, .stMarkdown p, label, .stCaption, [data-testid="stCaptionContainer"],
  h1,h2,h3,h4,h5,h6, .stRadio label, .stSelectbox label, .stSlider label, .stTextInput label{
    color:#e7e7f0 !important;
  }
  [data-testid="stCaptionContainer"], .stCaption, small{ color:#a9a9bd !important; }
  a{ color:#b7a6ff; }

  /* Sidebar dark */
  section[data-testid="stSidebar"]{ background:linear-gradient(180deg,#0c0c16,#0a0a12); border-right:1px solid #1d1d2c; }
  section[data-testid="stSidebar"] *{ color:#e7e7f0; }

  /* Expanders / alerts on dark */
  details, .streamlit-expanderHeader, [data-testid="stExpander"]{ background:#13131f !important; border:1px solid #23233a !important; border-radius:12px !important; }
  [data-testid="stExpander"] *{ color:#e7e7f0; }

  /* Hero banner */
  .hero{
    position:relative; overflow:hidden; border-radius:20px; padding:30px 34px; margin-bottom:22px;
    background:linear-gradient(135deg,#4b2aad 0%,#6d3bd1 45%,#8b5cf6 100%);
    box-shadow:0 18px 40px -18px rgba(75,42,173,.55);
  }
  .hero:before{
    content:""; position:absolute; inset:0; opacity:.35;
    background-image:radial-gradient(rgba(255,255,255,.18) 1px, transparent 1px);
    background-size:18px 18px;
  }
  .hero:after{
    content:""; position:absolute; right:-60px; top:-60px; width:240px; height:240px; border-radius:50%;
    background:radial-gradient(circle at 30% 30%, rgba(255,255,255,.30), rgba(255,255,255,0) 70%);
  }
  .hero h1{ color:#fff; font-size:28px; font-weight:800; margin:0 0 6px; position:relative; letter-spacing:-.5px; }
  .hero p{ color:rgba(255,255,255,.92); font-size:14px; margin:0; position:relative; }
  .hero .pillrow{ margin-top:14px; position:relative; display:flex; gap:8px; flex-wrap:wrap; }
  .hero .hpill{ background:rgba(255,255,255,.18); color:#fff; padding:5px 14px; border-radius:99px; font-size:12px; font-weight:600; backdrop-filter:blur(4px); border:1px solid rgba(255,255,255,.25); }

  /* Surface cards */
  .surface{ background:#13131f; border:1px solid #23233a; border-radius:16px; padding:22px 26px;
            box-shadow:0 10px 30px -20px rgba(0,0,0,.6); margin-bottom:16px; color:#e7e7f0; }
  .surface p{ color:#cfcfe0 !important; }

  /* Streamlit primary buttons — gradient + lift */
  .stButton>button[kind="primary"], .stButton>button[data-testid="baseButton-primary"]{
    background:linear-gradient(135deg,#5b46d6,#7c3aed)!important; border:none!important;
    color:#fff!important; font-weight:700!important; border-radius:12px!important;
    box-shadow:0 10px 22px -10px rgba(124,58,237,.7)!important; transition:transform .12s ease, box-shadow .12s ease;
  }
  .stButton>button[kind="primary"]:hover{ transform:translateY(-1px); box-shadow:0 14px 26px -10px rgba(124,58,237,.8)!important; }
  .stButton>button[kind="secondary"]{ border-radius:12px!important; border:1.5px solid #e0ddf2!important; font-weight:600!important; }

  /* Metric cards in recruiter dashboard */
  div[data-testid="stMetric"]{
    background:linear-gradient(135deg,#15152400,#1a1730); border:1px solid #2a2440; border-radius:14px;
    padding:14px 18px; box-shadow:0 8px 22px -16px rgba(0,0,0,.7);
  }
  div[data-testid="stMetricValue"]{ color:#c4b5fd; font-weight:800; }
  div[data-testid="stMetricLabel"]{ color:#a9a9bd !important; }

  /* Inputs — dark fields */
  .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"]>div{
    border-radius:10px!important; background:#15151f!important; color:#e7e7f0!important; border-color:#2a2a40!important;
  }
  .stTextInput input::placeholder, .stTextArea textarea::placeholder{ color:#6f6f86!important; }

  /* Tabs */
  .stTabs [data-baseweb="tab-list"]{ gap:6px; }
  .stTabs [data-baseweb="tab"]{ border-radius:10px 10px 0 0; font-weight:600; }

  .question-card{background:linear-gradient(135deg,#1a1730,#161a30);border-left:4px solid #7c5cff;border-radius:0 14px 14px 0;padding:18px 22px;margin-bottom:16px;font-size:15px;font-weight:500;color:#e7e7f0;user-select:none;-webkit-user-select:none;box-shadow:0 8px 22px -18px rgba(0,0,0,.7);}
  .code-question{background:linear-gradient(135deg,#0e0e18,#141021);border-left:4px solid #f59e0b;border-radius:0 14px 14px 0;padding:18px 22px;margin-bottom:16px;font-size:14px;color:#cdd6f4;font-family:'Courier New',monospace;user-select:none;-webkit-user-select:none;box-shadow:0 8px 22px -18px rgba(0,0,0,.7);}
  .feedback-box{background:#13131f;border:1px solid #23233a;border-radius:14px;padding:18px 22px;margin-top:12px;box-shadow:0 10px 28px -20px rgba(0,0,0,.7);color:#e7e7f0;}
  .feedback-box pre{background:#0e0e18!important;color:#cdd6f4!important;border:1px solid #23233a;}
  .score-pill{display:inline-block;padding:4px 14px;border-radius:99px;font-size:13px;font-weight:600;}
  .score-high{background:#e1f5ee;color:#085041;}.score-mid{background:#faeeda;color:#633806;}.score-low{background:#fcebeb;color:#791F1F;}
  .stTextArea textarea{font-size:14px!important;font-family:'Courier New',monospace!important;border-radius:12px!important;}
  .progress-bar-wrap{background:#e8e6f3;border-radius:99px;height:10px;margin:8px 0 20px;overflow:hidden;}
  .progress-bar-fill{height:10px;border-radius:99px;background:linear-gradient(90deg,#534AB7,#7c3aed,#a855f7);}
  .timer-box{background:linear-gradient(135deg,#1a1a2e,#2a2350);color:#fff;border-radius:10px;padding:6px 14px;font-size:15px;font-weight:700;display:inline-block;font-family:monospace;box-shadow:0 6px 16px -8px rgba(26,26,46,.7);}
  .timer-warning{background:linear-gradient(135deg,#c53030,#e53e3e)!important;animation:pulse 1s infinite;}
  @keyframes pulse{0%,100%{opacity:1}50%{opacity:.6}}
  .badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:11px;font-weight:600;}
  .badge-claude{background:#EEEDFE;color:#3C3489;}.badge-openai{background:#E7F5E7;color:#1A6B1A;}
  .badge-coding{background:#fff3cd;color:#856404;}.badge-tech{background:#d1ecf1;color:#0c5460;}
  .secure-banner{background:linear-gradient(135deg,#1a1a2e,#2a2350,#16213e);color:#fff;border-radius:12px;padding:11px 20px;font-size:13px;font-weight:500;margin-bottom:16px;box-shadow:0 8px 20px -12px rgba(26,26,46,.6);}
  .time-config{background:linear-gradient(135deg,#f0f7ff,#eef4ff);border:1px solid #cce0ff;border-radius:12px;padding:12px 16px;margin-top:8px;}
  .resume-match-bar{height:8px;border-radius:99px;background:#e8e6f3;margin:4px 0 8px;overflow:hidden;}
  .adaptive-badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:11px;font-weight:600;}
  .diff-up{background:#e1f5ee;color:#085041;}.diff-down{background:#faeeda;color:#633806;}.diff-same{background:#EEEDFE;color:#3C3489;}
  .lang-badge{display:inline-block;padding:3px 10px;border-radius:99px;font-size:11px;font-weight:600;background:#f0f7ff;color:#0C447C;margin-left:8px;}
  .auth-card{background:#13131f;border:1px solid #23233a;border-radius:18px;padding:28px 32px;max-width:460px;margin:0 auto;box-shadow:0 16px 40px -24px rgba(0,0,0,.8);}
  .verify-ok{background:linear-gradient(135deg,#e1f5ee,#d6f3e6);border:1px solid #9ae6c4;border-radius:12px;padding:12px 16px;color:#085041;font-weight:600;}
  .verify-no{background:linear-gradient(135deg,#fcebeb,#fbe0e0);border:1px solid #f5b5b5;border-radius:12px;padding:12px 16px;color:#791F1F;font-weight:600;}
</style>""", unsafe_allow_html=True)

# ── Extra graphics layer (animated background orbs + sheen + micro-interactions) ─
st.markdown("""<style>
  /* Floating gradient orbs drifting behind the app */
  .stApp:before{
    content:""; position:fixed; inset:0; z-index:0; pointer-events:none;
    background:
      radial-gradient(420px 420px at 8% 18%, rgba(124,92,255,.16), transparent 60%),
      radial-gradient(380px 380px at 92% 12%, rgba(41,181,232,.14), transparent 60%),
      radial-gradient(460px 460px at 78% 92%, rgba(204,120,92,.12), transparent 60%);
    animation:orbDrift 16s ease-in-out infinite alternate;
  }
  @keyframes orbDrift{ 0%{transform:translate3d(0,0,0)} 100%{transform:translate3d(0,-18px,0) scale(1.04)} }
  .block-container{ position:relative; z-index:1; }

  /* Animated sheen sweeping across the hero */
  .hero{ background-size:160% 160% !important; animation:heroFlow 12s ease infinite; }
  @keyframes heroFlow{ 0%{background-position:0% 50%} 50%{background-position:100% 50%} 100%{background-position:0% 50%} }
  .hero h1{ text-shadow:0 2px 18px rgba(0,0,0,.25); }
  .hero:before{ animation:twinkle 6s ease-in-out infinite alternate; }
  @keyframes twinkle{ 0%{opacity:.25} 100%{opacity:.45} }

  /* Hover lift for cards & expanders */
  .surface, div[data-testid="stExpander"]{ transition:transform .15s ease, box-shadow .15s ease; }
  .surface:hover{ transform:translateY(-2px); box-shadow:0 18px 40px -22px rgba(124,92,255,.55); }
  div[data-testid="stExpander"]:hover{ box-shadow:0 14px 34px -22px rgba(0,0,0,.6); }

  /* Buttons: subtle shimmer on hover */
  .stButton>button{ position:relative; overflow:hidden; }
  .stButton>button:after{ content:""; position:absolute; top:0; left:-120%; width:60%; height:100%;
    background:linear-gradient(120deg,transparent,rgba(255,255,255,.35),transparent); transform:skewX(-20deg); }
  .stButton>button:hover:after{ animation:btnShine .8s ease; }
  @keyframes btnShine{ to{ left:140%; } }

  /* Animated progress fill (candy stripes) */
  .progress-bar-fill{ background-image:linear-gradient(90deg,#7c5cff,#29b5e8) , 
    repeating-linear-gradient(45deg,rgba(255,255,255,.12) 0 10px,transparent 10px 20px);
    background-blend-mode:overlay; transition:width .4s ease; }

  /* Metric tiles pop */
  div[data-testid="stMetric"]{ background:#13131f; border:1px solid #23233a; border-radius:14px;
    padding:12px 16px; box-shadow:0 10px 28px -20px rgba(0,0,0,.7); }
  div[data-testid="stMetricValue"]{ color:#fff !important; }

  /* Inputs glow on focus */
  .stTextInput input:focus, .stSelectbox div[data-baseweb="select"]:focus-within{
    box-shadow:0 0 0 3px rgba(124,92,255,.35) !important; border-color:#7c5cff !important; }

  /* Decorative animated top accent bar */
  .accent-bar{ height:5px;border-radius:99px;margin:0 0 14px;
    background:linear-gradient(90deg,#7c5cff,#29b5e8,#cc785c,#7c5cff);
    background-size:300% 100%; animation:heroFlow 8s linear infinite; }
</style>""", unsafe_allow_html=True)

def hero(title, subtitle, pills=None):
    pill_html = "".join(f'<span class="hpill">{p}</span>' for p in (pills or []))
    pr = f'<div class="pillrow">{pill_html}</div>' if pill_html else ""
    st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p>{pr}</div>', unsafe_allow_html=True)

def pipeline_stepper(active):
    """Show the 3-stage AI agent pipeline. active = 'generate' | 'evaluate' | 'coach'."""
    steps=[("generate","🧩","Question Generator"),("evaluate","🧪","Answer Evaluator"),("coach","🎯","Coaching Agent")]
    order=[s[0] for s in steps]; ai=order.index(active) if active in order else 0
    cells=""
    for i,(key,icon,label) in enumerate(steps):
        if i<ai:   bg,bd,op,tag="#13131f","#2a7d52",".7","done ✓"
        elif i==ai:bg,bd,op,tag="linear-gradient(135deg,#1a1730,#241a40)","#7c5cff","1","running…"
        else:      bg,bd,op,tag="#0f0f18","#23233a",".5","queued"
        cells+=(f'<div style="flex:1;min-width:120px;background:{bg};border:1px solid {bd};border-radius:12px;'
                f'padding:10px 14px;opacity:{op}"><div style="font-size:18px">{icon}</div>'
                f'<div style="font-size:13px;font-weight:600;color:#e7e7f0">{label}</div>'
                f'<div style="font-size:11px;color:#a9a9bd">{tag}</div></div>')
        if i<len(steps)-1: cells+='<div style="align-self:center;color:#6f6f86;font-size:18px">→</div>'
    st.markdown(f'<div style="display:flex;gap:8px;align-items:stretch;flex-wrap:wrap;margin:6px 0 14px">{cells}</div>',unsafe_allow_html=True)


# ── Session state defaults ─────────────────────────────────────────────────────
defaults = {
    "auth_stage":"login",          # login | signup
    "logged_in":False,
    "user_role":"",                # candidate | recruiter
    "user_email":"",
    "candidate_name":"",
    "stage":"company_select",      # candidate flow stage
    "questions":[],"q_types_list":[],"current_q":0,
    "answers":{},"feedbacks":{},"scores":{},"violations":0,
    "q_start_time":None,"time_limit":240,
    "candidate_email":"","resume_text":"",
    "language":"English","session_saved":False,"adaptive_difficulty":0,
    "target_company":"","target_role":"","company_sector":"",
    "resume_summary":"","resume_match_score":0,
    "custom_code":"","custom_cfg":None,"proctoring_opts":None,"is_custom":False,
}
for k,v in defaults.items():
    if k not in st.session_state: st.session_state[k] = v

# ── AI helpers ─────────────────────────────────────────────────────────────────
def _model_for(provider):
    """Resolve the model the user picked in the sidebar, with a sensible default."""
    m=st.session_state.get(f"model_{provider}")
    return m or DEFAULT_MODEL.get(provider)

def call_claude(key,prompt,max_tokens=2000):
    c=anthropic.Anthropic(api_key=key)
    m=c.messages.create(model=_model_for("claude"),max_tokens=max_tokens,messages=[{"role":"user","content":prompt}])
    return m.content[0].text.strip()

def call_openai(key,prompt,max_tokens=2000):
    if not OPENAI_AVAILABLE: st.error("pip install openai"); st.stop()
    c=OpenAI(api_key=key)
    r=c.chat.completions.create(model=_model_for("openai"),max_tokens=max_tokens,messages=[{"role":"user","content":prompt}])
    return r.choices[0].message.content.strip()

def call_gemini(key,prompt,max_tokens=2000):
    if not GEMINI_AVAILABLE: st.error("pip install google-generativeai"); st.stop()
    genai.configure(api_key=key)
    picked=_model_for("gemini")
    # If the user picked a concrete model (not Auto-detect), use it directly.
    if picked and picked!="Auto-detect":
        try:
            model=genai.GenerativeModel(picked, generation_config=genai.GenerationConfig(max_output_tokens=max_tokens))
            return model.generate_content(prompt).text.strip()
        except Exception:
            pass  # fall through to auto-detect if that exact name isn't available
    preferred=["gemini-3.5-flash","gemini-3.1-pro","gemini-3-flash","gemini-2.5-flash","gemini-2.5-pro","gemini-flash-latest","gemini-pro-latest"]
    chosen=st.session_state.get("_gemini_model")
    if not chosen:
        available=[]
        try:
            for m in genai.list_models():
                if "generateContent" in getattr(m,"supported_generation_methods",[]):
                    available.append(m.name.replace("models/",""))
        except Exception: available=[]
        for name in preferred:
            if name in available: chosen=name; break
        if not chosen and available: chosen=available[0]
        if not chosen:
            st.error("No Gemini model available for this key. Get one at aistudio.google.com/app/apikey"); st.stop()
        st.session_state["_gemini_model"]=chosen
    model=genai.GenerativeModel(chosen, generation_config=genai.GenerationConfig(max_output_tokens=max_tokens))
    return model.generate_content(prompt).text.strip()

def call_ai(key,prompt,max_tokens=2000):
    provider=st.session_state.get("provider","claude")
    if not key:
        st.error("⚠️ No API key found. Please paste your API key in the sidebar under **AI Setup**.")
        st.stop()
    try:
        if provider=="openai": return call_openai(key,prompt,max_tokens)
        if provider=="gemini": return call_gemini(key,prompt,max_tokens)
        return call_claude(key,prompt,max_tokens)
    except Exception as e:
        msg=str(e).lower()
        if "auth" in msg or "api key" in msg or "401" in msg or "invalid" in msg or "permission" in msg:
            st.error("🔑 That API key was rejected. Double-check you pasted the right key for the selected provider in the sidebar.")
        elif "rate" in msg or "429" in msg or "quota" in msg or "insufficient" in msg:
            st.error("⏳ Rate limit or quota reached for this key. Wait a moment and try again, or switch providers in the sidebar.")
        elif "connection" in msg or "timeout" in msg or "network" in msg:
            st.error("🌐 Network issue reaching the AI provider. Check your internet connection and retry.")
        else:
            st.error(f"😕 The AI request failed: {e}")
        st.stop()

def transcribe_audio(key, audio_bytes):
    """Transcribe spoken answers with OpenAI Whisper. Returns text or ''."""
    if not OPENAI_AVAILABLE:
        st.warning("Voice answers need the OpenAI library: pip install openai"); return ""
    if not key:
        st.warning("Voice answers use OpenAI Whisper — paste an OpenAI API key in the sidebar."); return ""
    try:
        import io as _io
        client=OpenAI(api_key=key)
        buf=_io.BytesIO(audio_bytes); buf.name="answer.wav"
        tr=client.audio.transcriptions.create(model="whisper-1", file=buf)
        return tr.text.strip()
    except Exception as e:
        st.error(f"🎙️ Transcription failed: {e}"); return ""

def shared_key_for(provider):
    """Return a host-configured API key so EVERY interviewing user has access without
    pasting their own. Looked up from Streamlit secrets, then environment variables.
    The app owner sets these once; leave unset to require per-user keys."""
    secret_names={"claude":["ANTHROPIC_API_KEY","anthropic","claude"],
                  "openai":["OPENAI_API_KEY","openai"],
                  "gemini":["GEMINI_API_KEY","GOOGLE_API_KEY","gemini","google"]}
    names=secret_names.get(provider,[])
    # 1) st.secrets — supports flat keys or an [api_keys] table
    try:
        sec=st.secrets
        for n in names:
            if n in sec: return str(sec[n])
        if "api_keys" in sec:
            for n in names:
                if n in sec["api_keys"]: return str(sec["api_keys"][n])
    except Exception:
        pass
    # 2) environment variables
    for n in names:
        v=os.environ.get(n)
        if v: return v
    return ""

def check_internet(timeout=2.0):
    """Return True if the machine can reach the internet (so live AI is usable)."""
    for host, port in [("api.anthropic.com",443),("8.8.8.8",53),("1.1.1.1",53)]:
        try:
            s=socket.create_connection((host,port),timeout=timeout); s.close()
            return True
        except Exception:
            continue
    return False

def extract_pdf_text(file):
    if not PYPDF_OK: return ""
    try:
        reader=PdfReader(file)
        return " ".join(p.extract_text() or "" for p in reader.pages).strip()
    except: return ""

# ── Recruiter identity verification (AI-gated "company portal" check) ──────────
def verify_recruiter(key, company, emp_id, name, provider):
    """Ask the chosen AI provider to verify a recruiter's company + employee ID.
    Returns (verified: bool, reason: str). Falls back to a format check if no key."""
    # Basic format guard first
    if not company.strip() or not emp_id.strip():
        return False, "Company name and Employee ID are both required."
    if len(emp_id.strip()) < 4:
        return False, "Employee ID looks too short to be a valid corporate ID."
    if not key:
        # No API key — deterministic fallback check
        ok = any(c.isdigit() for c in emp_id) and any(c.isalpha() for c in emp_id)
        return (ok, "Verified by format check (add an API key for full AI verification)." if ok
                else "Employee ID should contain both letters and numbers (e.g. GOOG-48213).")
    prompt = f"""You are a corporate recruiter-credential verification service connected to company HR portals.
Assess whether the following looks like a PLAUSIBLE legitimate recruiter credential.
Name: {name}
Company: {company}
Employee ID: {emp_id}

Rules:
- The company should be a real, known employer.
- The employee ID should look like a corporate ID format (letters+numbers, reasonable length).
- You are NOT confirming the person truly works there — only that the credential is plausible and well-formed.
Respond in EXACT format:
VERDICT: <VERIFIED or REJECTED>
REASON: <one short sentence>"""
    try:
        old=st.session_state.get("provider")
        st.session_state["provider"]=provider
        raw=call_ai(key, prompt, max_tokens=120)
        st.session_state["provider"]=old
        verdict = "VERIFIED" in raw.upper().split("REASON")[0]
        m=re.search(r'REASON:\s*(.*)', raw, re.DOTALL)
        reason=m.group(1).strip() if m else ("Credential looks plausible." if verdict else "Credential could not be verified.")
        return verdict, reason
    except Exception as e:
        return False, f"Verification service error: {e}"

# ── Share links (email + WhatsApp — opens user's own app, no SMTP needed) ──────
def share_links_html(role, score, company):
    co=f" at {company}" if company and company!=GENERAL else ""
    msg=f"I scored {score}/10 in my AI mock interview for {role}{co}! Practice with AI Interview Coach."
    enc=msg.replace(" ","%20").replace("!","%21")
    wa=f"https://wa.me/?text={enc}"
    mail=f"mailto:?subject=My%20AI%20Interview%20Result&body={enc}"
    tw=f"https://twitter.com/intent/tweet?text={enc}"
    li=f"https://www.linkedin.com/sharing/share-offsite/?url=https%3A%2F%2Fgithub.com&summary={enc}"
    return f"""<div style="background:#13131f;border:1px solid #23233a;border-radius:14px;padding:16px 20px;margin:16px 0;">
  <p style="font-size:14px;font-weight:600;margin:0 0 12px;color:#e7e7f0;">📤 Share your result</p>
  <div style="display:flex;gap:10px;flex-wrap:wrap;">
    <a href="{wa}" target="_blank" style="text-decoration:none"><div style="background:#25D366;color:#fff;padding:9px 18px;border-radius:8px;font-size:13px;font-weight:600">💬 WhatsApp</div></a>
    <a href="{mail}" style="text-decoration:none"><div style="background:#534AB7;color:#fff;padding:9px 18px;border-radius:8px;font-size:13px;font-weight:600">✉️ Email</div></a>
    <a href="{li}" target="_blank" style="text-decoration:none"><div style="background:#0077B5;color:#fff;padding:9px 18px;border-radius:8px;font-size:13px;font-weight:600">in LinkedIn</div></a>
    <a href="{tw}" target="_blank" style="text-decoration:none"><div style="background:#1DA1F2;color:#fff;padding:9px 18px;border-radius:8px;font-size:13px;font-weight:600">𝕏 Twitter</div></a>
  </div>
  <p style="font-size:11px;color:#8a8a9e;margin:10px 0 0">WhatsApp and Email open in your own app — no setup required.</p>
</div>"""

# ── Prompt builders ────────────────────────────────────────────────────────────
def get_company_style(company_name):
    for sector, companies in US_COMPANIES.items():
        if company_name in companies:
            return companies[company_name].get("style","")
    return ""

# ── Daily-updated "hiring today" companies & roles ─────────────────────────────
import random as _random
def _today_human():
    return datetime.now().strftime("%A, %B %d, %Y")
def _daily_seed():
    return int(datetime.now().strftime("%Y%m%d"))
def daily_featured(k=9):
    """A date-seeded rotating set of companies 'hiring today' with a few fresh roles each.
    Deterministic within a day, automatically changes the next day — so each time a
    candidate opens the app they see an updated board without any manual action."""
    rng=_random.Random(_daily_seed())
    flat=[(sec,name,info) for sec,comps in US_COMPANIES.items() for name,info in comps.items()]
    rng.shuffle(flat)
    out=[]
    for sec,name,info in flat[:k]:
        roles=info.get("roles",[])[:]
        _random.Random(_daily_seed()+abs(hash(name))%99991).shuffle(roles)
        out.append((sec,name,info,roles[:3]))
    return out
def fetch_live_openings(api_key):
    """Optional: ask the AI for companies actively hiring *today* (cached per day).
    This is an AI suggestion, not a live jobs-board feed."""
    today=datetime.now().strftime("%Y-%m-%d")
    cache=st.session_state.get("_live_openings")
    if cache and cache.get("date")==today:
        return cache["items"]
    prompt=(f"Today is {today}. List 8 well-known US technology employers that are actively hiring "
            f"software, ML, data or cloud roles right now. For each give 2 specific open role titles. "
            f"Return ONLY lines in the exact format:\nCompany | Role A, Role B")
    items=[]
    try:
        raw=call_ai(api_key, prompt, max_tokens=500)
        for line in raw.splitlines():
            if "|" in line:
                comp,roles=line.split("|",1)
                comp=comp.strip(" -•*0123456789.").strip()
                rlist=[r.strip() for r in roles.split(",") if r.strip()]
                if comp and rlist: items.append((comp, rlist[:3]))
    except Exception:
        items=[]
    st.session_state["_live_openings"]={"date":today,"items":items}
    return items


def get_company_info(company_name):
    for sector, companies in US_COMPANIES.items():
        if company_name in companies:
            return companies[company_name], sector
    return {}, ""

def prompt_resume_analysis(resume_text, company, role):
    cs = get_company_style(company)
    return f"""Analyze this resume for a candidate applying to {company} for {role}.
{f'Company interview style: {cs}' if cs else ''}
Resume:
{resume_text[:2000]}
Respond in EXACT format:
SKILLS: <comma-separated top 8 technical skills>
EXPERIENCE_LEVEL: <intern/entry/junior/mid/senior>
GAPS: <2-3 key skills missing for {company}>
STRENGTHS: <2-3 resume strengths for {company}>
MATCH_SCORE: <integer 0-100>
SUMMARY: <2 sentence professional summary>"""

def prompt_questions(role, difficulty, q_type, n, language, resume_text, company, company_style):
    cn=max(1,n//3); tn=max(1,n//3); bn=n-cn-tn
    if q_type=="technical_coding": inst=f"Generate {cn} coding questions (write actual code/function) and {tn+bn} deep technical concept/design questions. NO behavioral."
    elif q_type=="behavioral": inst=f"Generate {n} behavioral STAR-format questions only."
    else: inst=f"Mix: {cn} coding, {tn} technical, {bn} behavioral STAR."
    co=f"\n\nCompany: {company}\nInterview style: {company_style}\nTailor ALL questions to {company}'s real interview patterns, tech stack, and culture." if company and company!=GENERAL else ""
    rh=f"\n\nCandidate resume (tailor 3+ questions to it):\n{resume_text[:1500]}" if resume_text else ""
    lh=f"\n\nGenerate all questions in {language}." if language!="English" else ""
    return f"""Strict FAANG-level interviewer. Role: {difficulty}-level {role}.{co}{rh}{lh}
{inst}
Rules: number Q1,Q2,… one per line, NO preamble.
Tags: [CODING] [DESIGN] [CONCEPT] [BEHAVIORAL] at start.
Format: Q1: [TAG] question"""

def prompt_feedback(role,difficulty,question,answer,time_taken,language,company):
    co=f" at {company}" if company and company!=GENERAL else ""
    ln=f"\nRespond entirely in {language}." if language!="English" else ""
    return f"""FAANG senior interviewer evaluating for {difficulty} {role}{co}.{ln}
Q: \"\"\"{question}\"\"\"
Time: {time_taken}s  Answer: \"\"\"{answer}\"\"\"
Evaluate strictly. EXACT format:
SCORE: <1-10>
STRENGTH: <one sentence>
IMPROVEMENT: <one sentence>
IDEAL: <2-4 sentences + code if coding>
COMPLEXITY: <O(?) or N/A>
TIP: <one actionable tip>"""

# ── Parsers ────────────────────────────────────────────────────────────────────
def parse_questions(raw):
    qs,ts=[],[]
    for line in raw.strip().splitlines():
        m=re.match(r'^Q\d+[:\.\)]\s*(.*)',line.strip())
        if m:
            text=m.group(1).strip()
            tm=re.match(r'^\[(CODING|DESIGN|CONCEPT|BEHAVIORAL)\]\s*(.*)',text)
            if tm: ts.append(tm.group(1)); qs.append(tm.group(2).strip())
            else:  ts.append("CONCEPT");  qs.append(text)
    return qs,ts

def parse_feedback(raw):
    result={}
    for f in ["SCORE","STRENGTH","IMPROVEMENT","IDEAL","COMPLEXITY","TIP"]:
        m=re.search(rf'{f}:\s*(.*?)(?=\n[A-Z]+:|$)',raw,re.DOTALL)
        result[f]=m.group(1).strip() if m else "—"
    try: result["SCORE"]=int(re.search(r'\d+',result["SCORE"]).group())
    except: result["SCORE"]=5
    return result

def parse_resume_analysis(raw):
    result={}
    for f in ["SKILLS","EXPERIENCE_LEVEL","GAPS","STRENGTHS","MATCH_SCORE","SUMMARY"]:
        m=re.search(rf'{f}:\s*(.*?)(?=\n[A-Z_]+:|$)',raw,re.DOTALL)
        result[f]=m.group(1).strip() if m else "—"
    try: result["MATCH_SCORE"]=int(re.search(r'\d+',result["MATCH_SCORE"]).group())
    except: result["MATCH_SCORE"]=50
    return result

def prompt_code_eval(question, language, code):
    return f"""You are a strict coding interviewer AND a careful code judge.
A candidate answered the coding question below by writing {language} code.

QUESTION:
{question}

CANDIDATE'S {language} CODE:
```
{code}
```

Carefully dry-run / trace the code as a {language} compiler/interpreter would.
Decide whether it correctly and completely solves the question, considering syntax,
logic, and edge cases. Be strict: award a high score ONLY if it is actually correct.

Respond in EXACT format:
VERDICT: <CORRECT or PARTIAL or INCORRECT>
OUTPUT: <what the code prints/returns for a representative input, or the key dry-run result; 1-3 lines>
ISSUES: <one sentence on bugs / failing edge cases, or "none">
COMPLEXITY: <O(?) or N/A>
SCORE: <integer 0-10; 8-10 only if fully correct, 4-7 if partially correct, 0-3 if incorrect or won't compile>
TIP: <one short improvement tip>"""

def parse_code_eval(raw):
    result={}
    for f in ["VERDICT","OUTPUT","ISSUES","COMPLEXITY","SCORE","TIP"]:
        m=re.search(rf'{f}:\s*(.*?)(?=\n[A-Z]+:|$)',raw,re.DOTALL)
        result[f]=m.group(1).strip() if m else "—"
    try: result["SCORE"]=int(re.search(r'\d+',result["SCORE"]).group())
    except: result["SCORE"]=0
    v=result.get("VERDICT","").upper()
    # Enforce "correct → points, otherwise no points"
    if "INCORRECT" in v: result["SCORE"]=0
    elif "CORRECT" in v and "INCORRECT" not in v: result["SCORE"]=max(result["SCORE"],8)
    return result

def sc(s): return "score-high" if s>=8 else("score-mid" if s>=5 else "score-low")
def se(s): return "🏆" if s>=9 else("✅" if s>=7 else("👍" if s>=5 else "📚"))
def tag_badge(tag):
    m={"CODING":("🧑‍💻 Coding","badge-coding"),"DESIGN":("🏗️ Design","badge-tech"),"CONCEPT":("💡 Concept","badge-tech"),"BEHAVIORAL":("💬 Behavioral","badge-claude")}
    l,c=m.get(tag,("❓","badge-tech"))
    return f'<span class="badge {c}">{l}</span>'

def make_certificate(name,role,score,date,violations,language,company,failed=False,reason="",issuer=""):
    if failed:
        color="#791F1F"; status="FAILED — INTEGRITY VIOLATION"; border="#791F1F"
    else:
        color="#085041" if score>=6 else "#791F1F"
        status="PASSED WITH DISTINCTION" if score>=8 else("PASSED" if score>=6 else "COMPLETED")
        border="#534AB7"
    integrity="✅ Zero Violations" if violations==0 else f"⚠️ {violations} Violation(s)"
    uid=hashlib.md5(f"{name}{role}{date}{issuer}".encode()).hexdigest()[:10].upper()
    cl=f"<div style='font-size:15px;color:#888;margin:6px 0'>Target Company: <strong>{company}</strong></div>" if company and company!=GENERAL else ""
    # Company-issued (custom interview) branding
    issuer_seal = (f"<div style='margin:10px 0 4px;display:inline-block;background:{border};color:#fff;"
                   f"padding:8px 22px;border-radius:10px;font-size:13px;font-weight:bold;letter-spacing:.5px'>"
                   f"🏢 OFFICIAL INTERVIEW · ISSUED BY {issuer.upper()}</div>") if issuer else ""
    brand_title = issuer if issuer else "AI Interview Coach"
    sub_org = (f"in partnership with AI Interview Coach" if issuer else "")
    fail_block = (f"<div style='background:#fcebeb;border:1px solid #f5b5b5;border-radius:10px;padding:12px 16px;margin:14px 0;color:#791F1F;font-size:13px'>"
                  f"<strong>This interview was terminated by the AI proctor.</strong><br>{reason or 'Repeated proctoring violations were detected.'}<br>"
                  f"Result is recorded as <strong>FAILED</strong>. The candidate may retake the interview in a quiet, well-lit room while looking at the screen.</div>") if failed else ""
    score_disp = "0" if failed else str(score)
    footer_issuer = f"Issued by {issuer} · " if issuer else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:Georgia,serif;background:#f5f0e8;margin:0;padding:40px;display:flex;justify-content:center;}}
.cert{{background:white;border:8px double {border};border-radius:20px;padding:60px 70px;max-width:750px;text-align:center;}}
.title{{font-size:30px;color:#534AB7;font-weight:bold;}} .sub{{font-size:11px;color:#aaa;letter-spacing:5px;text-transform:uppercase;margin-bottom:18px;}}
.name{{font-size:34px;color:#26215C;font-weight:bold;border-bottom:3px solid {border};display:inline-block;padding:0 24px 6px;margin:14px 0;}}
.score{{font-size:56px;font-weight:bold;color:{color};margin:14px 0;}}
.status{{background:{border};color:white;padding:10px 32px;border-radius:99px;font-size:14px;font-weight:bold;display:inline-block;margin:12px 0;}}
.org{{font-size:12px;color:#999;margin-bottom:24px}}
.footer{{margin-top:32px;font-size:11px;color:#bbb;border-top:1px solid #eee;padding-top:16px;}}
</style></head><body><div class="cert">
<div style="font-size:48px">{'⛔' if failed else '🎯'}</div><div class="title">{brand_title}</div>
<div class="sub">{'Proctoring Report' if failed else 'Certificate of Achievement'}</div>
{issuer_seal}
<div class="org">{sub_org}</div>
<div style="font-size:16px;color:#666">This certifies that</div>
<div class="name">{name}</div>
<div style="font-size:16px;color:#666">{'attempted' if failed else 'completed'} the AI-proctored interview for <strong>{role}</strong></div>
{cl}<div class="score">{score_disp}<span style="font-size:26px;color:#aaa">/10</span></div>
<div class="status">{status}</div>
{fail_block}
<div style="font-size:13px;color:#888;margin:8px 0">{integrity} · 🌐 {language}</div>
<div class="footer">{footer_issuer}Issued: {date} · Certificate ID: {uid}<br>This document was generated by an automated AI proctoring system and reflects performance and integrity signals captured during the session.<br>Powered by AI Interview Coach</div>
</div><script>setTimeout(function(){{window.print();}},600);</script></body></html>"""

# ══════════════════════════════════════════════════════════════════════════════
#  PDF GENERATION (professional certificate + detailed report via reportlab)
# ══════════════════════════════════════════════════════════════════════════════
def make_certificate_pdf(name, role, score, date, violations, language, company,
                         failed=False, reason="", issuer=""):
    """A premium, print-ready PDF certificate drawn with reportlab."""
    if not REPORTLAB_OK: return None
    buf=_io.BytesIO()
    W,H=A4  # portrait
    c=_rl_canvas.Canvas(buf, pagesize=A4)

    NAVY=_rc.HexColor("#26215C"); PURPLE=_rc.HexColor("#534AB7")
    GOLD=_rc.HexColor("#C9A227"); GREEN=_rc.HexColor("#0B7A4B")
    RED=_rc.HexColor("#9C2B2B"); GREY=_rc.HexColor("#8A8A9C"); INK=_rc.HexColor("#1f1f2e")
    accent = RED if failed else (GREEN if (not failed and score>=6) else PURPLE)

    # Background tint
    c.setFillColor(_rc.HexColor("#FBFAF6")); c.rect(0,0,W,H,fill=1,stroke=0)
    # Outer + inner decorative borders
    c.setStrokeColor(accent); c.setLineWidth(6); c.rect(16*mm,16*mm,W-32*mm,H-32*mm)
    c.setStrokeColor(GOLD);   c.setLineWidth(1.6); c.rect(20*mm,20*mm,W-40*mm,H-40*mm)
    # Corner flourishes
    c.setStrokeColor(accent); c.setLineWidth(2)
    for (cx,cy,dx,dy) in [(20*mm,H-20*mm,1,-1),(W-20*mm,H-20*mm,-1,-1),(20*mm,20*mm,1,1),(W-20*mm,20*mm,-1,1)]:
        c.line(cx,cy,cx+12*mm*dx,cy); c.line(cx,cy,cx,cy+12*mm*dy)

    # Faint diagonal watermark
    c.saveState(); c.translate(W/2,H/2); c.rotate(32)
    c.setFont("Helvetica-Bold",54); c.setFillColor(_rc.HexColor("#ECEAF6"))
    c.drawCentredString(0,0,"AI INTERVIEW COACH"); c.restoreState()

    top=H-46*mm
    c.setFillColor(accent); c.setFont("Helvetica-Bold",11)
    c.drawCentredString(W/2, top+20*mm, ("⛔ PROCTORING REPORT" if failed else "★  CERTIFICATE OF ACHIEVEMENT  ★"))
    c.setFillColor(NAVY); c.setFont("Helvetica-Bold",30)
    c.drawCentredString(W/2, top+6*mm, issuer if issuer else "AI Interview Coach")
    if issuer:
        c.setFillColor(GREY); c.setFont("Helvetica-Oblique",11)
        c.drawCentredString(W/2, top, "in partnership with AI Interview Coach")
        # company seal pill
        c.setFillColor(accent); c.roundRect(W/2-52*mm, top-12*mm, 104*mm, 9*mm, 4*mm, fill=1, stroke=0)
        c.setFillColor(_rc.white); c.setFont("Helvetica-Bold",9)
        c.drawCentredString(W/2, top-9.2*mm, f"OFFICIAL INTERVIEW · ISSUED BY {issuer.upper()}")

    y=top-26*mm
    c.setFillColor(GREY); c.setFont("Helvetica",12); c.drawCentredString(W/2,y,"This certifies that")
    y-=12*mm
    c.setFillColor(NAVY); c.setFont("Helvetica-Bold",26); c.drawCentredString(W/2,y,name or "Candidate")
    c.setStrokeColor(accent); c.setLineWidth(1.4); c.line(W/2-58*mm,y-3*mm,W/2+58*mm,y-3*mm)
    y-=14*mm
    c.setFillColor(INK); c.setFont("Helvetica",12.5)
    c.drawCentredString(W/2,y,("attempted" if failed else "successfully completed") + " the AI-proctored interview for")
    y-=8*mm
    c.setFillColor(PURPLE); c.setFont("Helvetica-Bold",16); c.drawCentredString(W/2,y,role or "—")
    if company and company!=GENERAL:
        y-=8*mm; c.setFillColor(GREY); c.setFont("Helvetica",11)
        c.drawCentredString(W/2,y,f"Target company: {company}")

    # Score badge (circle)
    y-=30*mm; cx=W/2; r=20*mm
    c.setFillColor(accent); c.circle(cx,y,r,fill=1,stroke=0)
    c.setFillColor(_rc.white); c.setFont("Helvetica-Bold",30)
    c.drawCentredString(cx, y-4*mm, ("0" if failed else str(score)))
    c.setFont("Helvetica",11); c.drawCentredString(cx, y-12*mm, "/ 10")

    # Status pill
    y-=30*mm
    status=("FAILED — INTEGRITY VIOLATION" if failed else
            ("PASSED WITH DISTINCTION" if score>=8 else ("PASSED" if score>=6 else "COMPLETED")))
    c.setFillColor(accent); c.roundRect(W/2-46*mm,y, 92*mm, 11*mm, 5*mm, fill=1, stroke=0)
    c.setFillColor(_rc.white); c.setFont("Helvetica-Bold",12)
    c.drawCentredString(W/2, y+3*mm, status)

    if failed:
        y-=20*mm
        c.setFillColor(_rc.HexColor("#FBECEC")); c.setStrokeColor(_rc.HexColor("#E3A9A9"))
        c.roundRect(28*mm,y-6*mm,W-56*mm,16*mm,4*mm,fill=1,stroke=1)
        c.setFillColor(RED); c.setFont("Helvetica-Bold",9.5)
        c.drawCentredString(W/2,y+4*mm,"This interview was terminated by the AI proctor.")
        c.setFont("Helvetica",8.5); c.setFillColor(_rc.HexColor("#6b1f1f"))
        txt=(reason or "Repeated proctoring violations were detected.")[:120]
        c.drawCentredString(W/2,y-1.5*mm,txt)

    # Integrity + language line
    y2=34*mm
    integ = "Zero Violations" if violations==0 else f"{violations} Violation(s)"
    c.setFillColor(GREY); c.setFont("Helvetica",10)
    c.drawCentredString(W/2, y2, f"Integrity: {integ}   ·   Language: {language}")
    # Footer
    uid=hashlib.md5(f"{name}{role}{date}{issuer}".encode()).hexdigest()[:10].upper()
    c.setStrokeColor(_rc.HexColor("#E2E0EC")); c.setLineWidth(0.8); c.line(34*mm,28*mm,W-34*mm,28*mm)
    c.setFillColor(GREY); c.setFont("Helvetica",8)
    foot=(f"Issued by {issuer} · " if issuer else "")+f"Issued: {date} · Certificate ID: {uid}"
    c.drawCentredString(W/2,24*mm,foot)
    c.drawCentredString(W/2,20.5*mm,"Generated by an automated AI proctoring system · Powered by AI Interview Coach")
    c.showPage(); c.save()
    buf.seek(0); return buf.getvalue()

def make_report_pdf(meta, questions, tags, answers, scores, feedbacks, failed=False, reason=""):
    """A clean, professional multi-page PDF report via reportlab Platypus."""
    if not REPORTLAB_OK: return None
    buf=_io.BytesIO()
    doc=SimpleDocTemplate(buf, pagesize=A4, leftMargin=18*mm, rightMargin=18*mm,
                          topMargin=16*mm, bottomMargin=16*mm, title="Interview Report")
    ss=getSampleStyleSheet()
    H1=ParagraphStyle("H1",parent=ss["Title"],textColor=_rc.HexColor("#26215C"),fontSize=20,spaceAfter=2)
    SUB=ParagraphStyle("SUB",parent=ss["Normal"],textColor=_rc.HexColor("#7a7a90"),fontSize=10,spaceAfter=8)
    H2=ParagraphStyle("H2",parent=ss["Heading2"],textColor=_rc.HexColor("#534AB7"),fontSize=13,spaceBefore=10,spaceAfter=4)
    BODY=ParagraphStyle("BODY",parent=ss["Normal"],fontSize=9.5,leading=14)
    SMALL=ParagraphStyle("SMALL",parent=ss["Normal"],fontSize=8.5,textColor=_rc.HexColor("#555"),leading=12)
    import html as _h
    def esc(x): return _h.escape(str(x))
    company=meta.get("company",""); company=("" if company in ("",GENERAL) else company)
    el=[]
    el.append(Paragraph(("⛔ FAILED Interview Report" if failed else "AI Interview Report"), H1))
    el.append(Paragraph(f"{esc(meta.get('name','Candidate'))} · {esc(meta.get('role',''))}"
                        + (f" · {esc(company)}" if company else ""), SUB))

    rows=[["Candidate",meta.get("name","—"),"Email",meta.get("email","—")],
          ["Role",meta.get("role","—"),"Company",company or "General"],
          ["Level",meta.get("difficulty","—"),"Language",meta.get("language","English")],
          ["Date",meta.get("date","—"),"Duration",meta.get("duration","—")],
          ["Overall",f"{meta.get('avg','—')}/10","Violations",str(meta.get("violations",0))],
          ["Coding",f"{meta.get('coding','N/A')}/10","Technical",f"{meta.get('technical','N/A')}/10"],
          ["Behavioral",f"{meta.get('behavioral','N/A')}/10","Resume Match",f"{meta.get('match',0)}%"]]
    t=Table(rows, colWidths=[28*mm,52*mm,30*mm,52*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,-1),_rc.HexColor("#EEEDFE")),
        ("BACKGROUND",(2,0),(2,-1),_rc.HexColor("#EEEDFE")),
        ("TEXTCOLOR",(0,0),(0,-1),_rc.HexColor("#3C3489")),
        ("TEXTCOLOR",(2,0),(2,-1),_rc.HexColor("#3C3489")),
        ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),("FONTNAME",(2,0),(2,-1),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,-1),9),("GRID",(0,0),(-1,-1),0.5,_rc.HexColor("#D9D7EC")),
        ("VALIGN",(0,0),(-1,-1),"MIDDLE"),("ROWBACKGROUNDS",(1,0),(1,-1),[_rc.white,_rc.HexColor("#FAFAFE")]),
        ("ROWBACKGROUNDS",(3,0),(3,-1),[_rc.white,_rc.HexColor("#FAFAFE")]),
        ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5)]))
    el.append(t)

    if failed:
        el.append(Spacer(1,8))
        rb=Table([[Paragraph("<b>⛔ Terminated by AI proctor.</b> "+esc(reason or
                  "Repeated proctoring violations were detected."), SMALL)]], colWidths=[162*mm])
        rb.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),_rc.HexColor("#FBECEC")),
                    ("BOX",(0,0),(-1,-1),0.6,_rc.HexColor("#E3A9A9")),("LEFTPADDING",(0,0),(-1,-1),10),
                    ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8)]))
        el.append(rb)

    el.append(Paragraph("Question-by-question breakdown", H2))
    for i,q in enumerate(questions):
        fb=feedbacks.get(i,{}); sc=scores.get(i,0); ans=answers.get(i,""); tg=tags[i] if i<len(tags) else "CONCEPT"
        el.append(Paragraph(f"<b>Q{i+1} · [{esc(tg)}] · {esc(sc)}/10</b>", BODY))
        el.append(Paragraph(esc(q), BODY))
        if ans=="(skipped)":
            el.append(Paragraph("<i>Skipped.</i>", SMALL))
        else:
            el.append(Paragraph("<b>Answer:</b> "+esc(ans)[:1200], SMALL))
            if fb.get("STRENGTH"):    el.append(Paragraph("<b>Strength:</b> "+esc(fb.get('STRENGTH','—')), SMALL))
            if fb.get("IMPROVEMENT"): el.append(Paragraph("<b>Improve:</b> "+esc(fb.get('IMPROVEMENT','—')), SMALL))
            if fb.get("COMPLEXITY","N/A") not in ("N/A","—",""):
                el.append(Paragraph("<b>Complexity:</b> "+esc(fb.get('COMPLEXITY','')), SMALL))
            if fb.get("TIP"):         el.append(Paragraph("<b>Tip:</b> "+esc(fb.get('TIP','—')), SMALL))
        el.append(Spacer(1,7))
    el.append(Spacer(1,6))
    el.append(Paragraph("Generated by an automated AI proctoring system · Powered by AI Interview Coach", SMALL))
    doc.build(el); buf.seek(0); return buf.getvalue()


def make_detailed_report(meta, questions, tags, answers, scores, feedbacks, failed=False, reason=""):
    """Build a detailed, printable HTML interview report covering every question,
    the scores, the proctoring outcome, and recommendations."""
    import html as _h
    def esc(x): return _h.escape(str(x))
    name=meta.get("name","Candidate"); role=meta.get("role","")
    company=meta.get("company",""); company=("" if company in ("",GENERAL) else company)
    date=meta.get("date",""); diff=meta.get("difficulty","")
    language=meta.get("language","English"); duration=meta.get("duration","—")
    avg=meta.get("avg",0); ac=meta.get("coding","N/A"); at=meta.get("technical","N/A"); ab=meta.get("behavioral","N/A")
    violations=meta.get("violations",0); match=meta.get("match",0)
    uid=hashlib.md5(f"{name}{role}{date}".encode()).hexdigest()[:10].upper()
    head_color="#791F1F" if failed else "#4b2aad"
    banner = (f"<div style='background:#fcebeb;border:1px solid #f5b5b5;color:#791F1F;border-radius:10px;padding:14px 18px;margin:12px 0'>"
              f"<b>RESULT: FAILED — terminated by AI proctor.</b><br>{esc(reason or 'Repeated proctoring violations were detected.')}<br>"
              f"The candidate received a red on-screen warning after the second head turn; a fourth head turn ended the session. "
              f"No interview score is awarded. The candidate may retake the interview in a quiet, well-lit room while looking at the screen.</div>"
              if failed else
              f"<div style='background:#e1f5ee;border:1px solid #9ae6c4;color:#085041;border-radius:10px;padding:14px 18px;margin:12px 0'>"
              f"<b>RESULT: COMPLETED.</b> Overall score <b>{avg}/10</b>. This report details every question, your answer, the AI score and feedback.</div>")
    rows=""
    n=len(questions)
    for i in range(n):
        q=questions[i]; tg=tags[i] if i<len(tags) else "CONCEPT"
        a=answers.get(i, answers.get(str(i),"")); fb=feedbacks.get(i, feedbacks.get(str(i),{})) or {}
        scv=scores.get(i, scores.get(str(i),0))
        a_disp= "(not answered — interview ended)" if (failed and not a) else (a if a else "(skipped)")
        rows+=(f"<div class='q'><div class='qh'><b>Q{i+1} · {esc(tg)}</b><span class='sc'>{esc(scv)}/10</span></div>"
               f"<div class='qt'>{esc(q)}</div>"
               f"<div class='lbl'>Answer</div><pre>{esc(a_disp)}</pre>"
               + (f"<div class='lbl'>Strength</div><div class='fb'>{esc(fb.get('STRENGTH','—'))}</div>"
                  f"<div class='lbl'>Improve</div><div class='fb'>{esc(fb.get('IMPROVEMENT','—'))}</div>"
                  f"<div class='lbl'>Model answer / expected</div><pre>{esc(fb.get('IDEAL','—'))}</pre>"
                  f"<div class='lbl'>Tip</div><div class='fb'>{esc(fb.get('TIP','—'))}</div>" if fb else "")
               + "</div>")
    if not rows:
        rows="<p style='color:#666'>The interview ended before any question was scored.</p>"
    cl=f"<tr><td>Target company</td><td>{esc(company)}</td></tr>" if company else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Interview Report — {esc(name)}</title>
<style>
body{{font-family:Georgia,'Times New Roman',serif;color:#1c1c28;max-width:840px;margin:0 auto;padding:32px;background:#fff;}}
h1{{color:{head_color};margin:0 0 2px;font-size:26px}}
.meta{{color:#666;font-size:13px;margin-bottom:14px}}
table.info{{border-collapse:collapse;width:100%;margin:10px 0 4px;font-size:13.5px}}
table.info td{{border:1px solid #e3e3ee;padding:7px 11px}} table.info td:first-child{{color:#666;width:34%}}
.cards{{display:flex;gap:10px;flex-wrap:wrap;margin:12px 0}}
.card{{flex:1;min-width:120px;border:1px solid #e3e3ee;border-radius:10px;padding:10px 14px;text-align:center}}
.card b{{display:block;font-size:22px;color:{head_color}}} .card span{{font-size:11px;color:#666}}
h2{{font-size:17px;color:{head_color};border-bottom:2px solid #eee;padding-bottom:5px;margin:22px 0 10px}}
.q{{border:1px solid #e3e3ee;border-radius:10px;padding:13px 16px;margin:10px 0}}
.qh{{display:flex;justify-content:space-between;align-items:center}} .qh .sc{{font-weight:bold;color:{head_color}}}
.qt{{margin:6px 0 8px;font-style:italic;color:#333}}
.lbl{{font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:#888;margin:8px 0 2px;font-family:Arial}}
pre{{background:#f6f6fb;border:1px solid #ececf4;border-radius:7px;padding:9px 11px;white-space:pre-wrap;font-size:12.5px;font-family:Consolas,monospace;margin:0}}
.fb{{font-size:13.5px}}
.footer{{margin-top:26px;border-top:1px solid #eee;padding-top:12px;color:#999;font-size:11px}}
</style></head><body>
<h1>{'⛔ Proctoring Failure Report' if failed else '📄 AI Interview Report'}</h1>
<div class="meta">Candidate: <b>{esc(name)}</b> · {esc(role)}{(' · '+esc(company)) if company else ''} · {esc(diff)} · 🌐 {esc(language)}</div>
{banner}
<h2>Session details</h2>
<table class="info">
<tr><td>Candidate</td><td>{esc(name)} ({esc(meta.get('email',''))})</td></tr>
<tr><td>Role</td><td>{esc(role)}</td></tr>{cl}
<tr><td>Experience level</td><td>{esc(diff)}</td></tr>
<tr><td>Language</td><td>{esc(language)}</td></tr>
<tr><td>Started</td><td>{esc(date)}</td></tr>
<tr><td>Approx. duration</td><td>{esc(duration)}</td></tr>
<tr><td>Questions presented</td><td>{n}</td></tr>
<tr><td>Integrity violations</td><td>{esc(violations)}</td></tr>
<tr><td>Resume match</td><td>{esc(match)}%</td></tr>
<tr><td>Report ID</td><td>{uid}</td></tr>
</table>
<h2>Score summary</h2>
<div class="cards">
<div class="card"><b>{esc(avg)}</b><span>OVERALL /10</span></div>
<div class="card"><b>{esc(ac)}</b><span>CODING</span></div>
<div class="card"><b>{esc(at)}</b><span>TECHNICAL</span></div>
<div class="card"><b>{esc(ab)}</b><span>BEHAVIORAL</span></div>
<div class="card"><b>{esc(violations)}</b><span>VIOLATIONS</span></div>
</div>
<h2>Question-by-question detail</h2>
{rows}
<div class="footer">Report ID {uid} · Generated by AI Interview Coach automated proctoring system on {esc(date)}.<br>
This document reflects performance and integrity signals captured during the session and is intended for practice and screening purposes.</div>
<script>setTimeout(function(){{window.print();}},700);</script>
</body></html>"""


if not st.session_state["logged_in"]:
    hero("🔒 AI Interview Coach",
         "Secure proctored mock interviews · company-specific questions · resume analysis · instant AI scoring",
         ["🎥 Camera-proctored","🏢 200+ companies","🤖 Claude · GPT-4o · Gemini","📜 Shareable certificate"])

    tab_login, tab_signup = st.tabs(["🔑 Log In", "📝 Create Account"])

    with tab_login:
        st.markdown("#### Welcome back")
        li_email = st.text_input("Email", key="li_email", placeholder="you@email.com")
        li_pass  = st.text_input("Password", key="li_pass", type="password")
        if st.button("Log In", type="primary", use_container_width=True):
            ok, user, msg = authenticate(li_email, li_pass)
            if ok and user.get("role")=="recruiter":
                st.warning("This is a recruiter account. The recruiter dashboard is now a separate app — run **`streamlit run recruiter_dashboard.py`** and log in there.")
            elif ok:
                st.session_state.update({
                    "logged_in":True,"user_role":user["role"],
                    "user_email":user["email"],"candidate_name":user["name"],
                    "candidate_email":user["email"],
                })
                st.session_state["stage"]="company_select"
                st.rerun()
            else:
                st.error(msg)

    with tab_signup:
        st.markdown("#### Create your candidate account")
        su_name = st.text_input("Full Name", key="su_name", placeholder="Your full name")
        su_email= st.text_input("Email", key="su_email", placeholder="you@email.com")
        su_pass = st.text_input("Password", key="su_pass", type="password", help="Min 6 characters")
        st.markdown("")
        if st.button("Create Account", type="primary", use_container_width=True):
            if not su_name.strip(): st.error("Enter your name.")
            elif not su_email.strip() or "@" not in su_email: st.error("Enter a valid email.")
            elif len(su_pass) < 6: st.error("Password must be at least 6 characters.")
            else:
                ok, msg = create_user(su_email, su_pass, su_name, "candidate")
                if ok: st.success(msg + " You can now log in.")
                else:  st.error(msg)
    st.stop()   # nothing below renders until logged in

# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR  (role-aware — candidates never see the recruiter dashboard)
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(f"**👤 {st.session_state['candidate_name']}**")
    st.caption(f"🎓 Candidate · {st.session_state['user_email']}")
    if st.button("🚪 Log Out", use_container_width=True):
        keep=defaults.copy()
        for k in list(st.session_state.keys()): del st.session_state[k]
        for k,v in keep.items(): st.session_state[k]=v
        st.rerun()
    st.divider()

    # AI provider config (needed by both, but candidates use it for interviews)
    st.header("⚙️ AI Setup")
    # Detect connectivity once per session. Offline → Demo Mode auto-on and the
    # option is shown; Online → Demo Mode is hidden entirely and live AI is used.
    if "is_online" not in st.session_state:
        st.session_state["is_online"]=check_internet()
    online=st.session_state["is_online"]
    if online:
        st.session_state["demo_mode"]=False
    else:
        st.session_state["demo_mode"]=True
        st.warning("📴 You appear to be **offline** — running in **Offline Demo Mode** "
                   "(pre-loaded questions + instant offline scoring). No API key or internet needed.")
        if st.button("🔄 Recheck connection", use_container_width=True):
            st.session_state["is_online"]=check_internet(); st.rerun()
    demo_mode=st.session_state["demo_mode"]
    provider_label=st.selectbox("AI Provider",list(PROVIDERS.keys()),index=0, disabled=demo_mode)
    provider=PROVIDERS[provider_label]
    st.session_state["provider"]=provider
    api_key=""
    if online:
        # Model picker (newest versions first) with a Custom… escape hatch
        opts=MODEL_OPTIONS.get(provider,["Custom…"])
        cur=st.session_state.get(f"model_{provider}", DEFAULT_MODEL.get(provider))
        idx=opts.index(cur) if cur in opts else 0
        pick=st.selectbox("Model", opts, index=idx, disabled=demo_mode, key=f"modelpick_{provider}")
        if pick=="Custom…":
            custom=st.text_input("Custom model id", value=("" if cur in opts else cur),
                                 placeholder="e.g. gpt-5.6 / claude-... / gemini-...", key=f"modelcustom_{provider}")
            st.session_state[f"model_{provider}"]=custom.strip() or DEFAULT_MODEL.get(provider)
        else:
            st.session_state[f"model_{provider}"]=pick

        # Shared access: if the host configured a key (secrets/env), EVERY user
        # who is interviewing gets access automatically — no key entry needed.
        shared=shared_key_for(provider)
        if shared:
            api_key=shared
            st.session_state[f"_shared_{provider}"]=True
            st.success(f"✅ Shared interview access is enabled for **{provider_label.split('(')[0].strip()}** — "
                       "no API key needed. Provided by the app administrator.")
            # Also remember a shared OpenAI key for Whisper voice if available.
            ok_shared=shared_key_for("openai")
            if ok_shared: st.session_state["_openai_key"]=ok_shared
        else:
            phs={"claude":("Anthropic API Key","sk-ant-..."),
                 "openai":("OpenAI API Key","sk-..."),
                 "gemini":("Google Gemini API Key","AIza...")}
            lbl,ph=phs[provider]
            api_key=st.text_input(lbl,type="password",placeholder=ph)
            if provider=="openai" and api_key:
                st.session_state["_openai_key"]=api_key
            if provider!="openai":
                st.caption("🎙️ Voice answers use OpenAI Whisper — add an OpenAI key once to enable them.")
                _ok=st.text_input("OpenAI key for voice (optional)", type="password", placeholder="sk-...", key="voice_openai_key")
                if _ok: st.session_state["_openai_key"]=_ok
        st.caption(f"Active model: `{_model_for(provider)}`")
    else:
        st.caption("API keys are hidden while offline. Reconnect and recheck to use live AI (Claude · GPT-4o · Gemini).")

    if st.session_state["user_role"]=="candidate":
        st.divider()
        st.markdown("**🔒 Proctoring Active**")
        st.markdown("• 📷 Camera recording\n• Copy/paste blocked\n• DevTools blocked\n• Tab switches logged")
        if st.session_state["stage"] not in ("company_select",):
            v=st.session_state.get("violations",0)
            if v>0: st.error(f"⚠️ Violations: {v}")
            if st.button("🔄 New Interview",use_container_width=True):
                for k in ["stage","questions","q_types_list","current_q","answers","feedbacks",
                          "scores","violations","q_start_time","session_saved","adaptive_difficulty",
                          "target_company","target_role","company_sector","resume_summary","resume_match_score"]:
                    st.session_state[k]=defaults[k]
                st.rerun()


# (Recruiter dashboard lives in the separate app: recruiter_dashboard.py)


# ══════════════════════════════════════════════════════════════════════════════
#  CANDIDATE FLOW  (only candidates reach here)
# ══════════════════════════════════════════════════════════════════════════════

# ── STAGE: COMPANY + ROLE SELECTION (company is OPTIONAL) ──────────────────────
if st.session_state["stage"]=="company_select":
    st.components.v1.html(LOCKDOWN_JS,height=0)
    hero(f"🎯 Hi {st.session_state['candidate_name']}, let's set up your interview",
         "Practice on your own, or enter a company interview code your recruiter gave you.",
         ["🏢 Company optional","🔖 Interview codes","📄 Resume-aware"])

    # ── Company interview code (recruiter-designed custom interview) ───────────
    with st.expander("🔖 Have a company interview code? Enter it here", expanded=False):
        st.caption("Recruiters can design a custom interview with their own questions and lockdown rules. "
                   "Paste the code they shared to take the official, certified interview.")
        code_in = st.text_input("Interview code", placeholder="e.g. ACME-1A2B3C", key="custom_code_in")
        if st.button("🚀 Load this interview", use_container_width=True):
            cfg = get_custom_interview(code_in)
            if not cfg:
                st.error("No interview found for that code. Check it with your recruiter.")
            else:
                demo = st.session_state.get("demo_mode", False)
                role = cfg.get("role","Software Engineer")
                difficulty = cfg.get("difficulty","mid")
                language = cfg.get("language","English")
                company = cfg.get("company") or GENERAL
                n = cfg.get("num_questions",8)
                if cfg.get("q_type")=="custom" and cfg.get("custom_questions"):
                    qs=[q for _,q in cfg["custom_questions"]]; tags=[t for t,_ in cfg["custom_questions"]]
                    qs=qs[:n]; tags=tags[:n]
                else:
                    if demo:
                        qs,tags=demo_questions(n, cfg.get("q_type","technical_coding"))
                    elif not api_key:
                        st.error("This interview uses AI-generated questions — paste an API key in the sidebar, then load again."); st.stop()
                    else:
                        with st.spinner("Preparing your interview questions…"):
                            raw=call_ai(api_key, prompt_questions(role,difficulty,cfg.get("q_type","technical_coding"),
                                        n, language, "", company, get_company_style(company)))
                            qs,tags=parse_questions(raw)
                if not qs:
                    st.error("Could not prepare questions for this interview. Try again or contact your recruiter."); st.stop()
                st.session_state.update({
                    "is_custom":True, "custom_cfg":cfg, "custom_code":cfg["code"],
                    "proctoring_opts":cfg.get("proctoring") or default_proctoring(),
                    "target_company":company, "target_role":role,
                    "role":role, "difficulty":difficulty, "difficulty_label":cfg.get("difficulty_label",""),
                    "language":language, "api_key":api_key,
                    "questions":qs, "q_types_list":tags, "time_limit":cfg.get("minutes",5)*60,
                    "current_q":0,"answers":{},"feedbacks":{},"scores":{},"violations":0,
                    "q_start_time":time.time(),"interview_start":datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "session_saved":False,"company_style":get_company_style(company),
                    "stage":"syscheck",
                })
                st.rerun()
    st.divider()

    # ── 📅 Companies hiring today (updates daily, automatically) ───────────────
    st.markdown(f"""<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;margin:2px 0 10px">
      <div><span style="font-size:20px;font-weight:800;color:#f3f1ff">🔥 Hiring today</span>
      <span style="font-size:12px;color:#a9a9bd;margin-left:8px">📅 Updated for {_today_human()}</span></div>
      <span style="background:rgba(124,92,255,.18);color:#c4b5fd;border:1px solid rgba(124,92,255,.35);border-radius:99px;padding:4px 12px;font-size:12px;font-weight:700">↻ Refreshes every day</span>
    </div>""", unsafe_allow_html=True)
    st.caption("A fresh set of companies and roles surfaces automatically each day. Tap **Practice** to jump straight in — or browse the full list below.")

    feat = daily_featured(9)
    fcols = st.columns(3)
    for i,(sec,name,info,roles) in enumerate(feat):
        color=info.get("color","#7c5cff"); logo=info.get("logo","?")[:2]
        with fcols[i%3]:
            st.markdown(f"""<div style="background:linear-gradient(135deg,{color}26,#15151f 70%);border:1px solid {color}66;
                border-radius:14px;padding:14px 16px;margin-bottom:8px;min-height:128px;box-shadow:0 12px 30px -22px rgba(0,0,0,.8)">
              <div style="display:flex;align-items:center;gap:10px;margin-bottom:8px">
                <div style="background:{color};color:#fff;border-radius:9px;width:38px;height:38px;display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:800;box-shadow:0 6px 16px -6px {color}">{logo}</div>
                <div><div style="font-weight:700;font-size:14px;color:#f3f1ff">{name}</div>
                <div style="font-size:11px;color:#9a9ab0">📍 {info.get('hq','')}</div></div>
              </div>
              <div style="font-size:11.5px;color:#c7c7d6;line-height:1.55"><b style="color:#fff">Open today:</b> {", ".join(roles)}</div>
            </div>""", unsafe_allow_html=True)
            pick_role = st.selectbox("Role", roles, key=f"feat_role_{i}", label_visibility="collapsed")
            if st.button(f"Practice →", key=f"feat_go_{i}", use_container_width=True):
                st.session_state["target_company"]=name
                st.session_state["company_sector"]=sec
                st.session_state["target_role"]=pick_role
                st.session_state["stage"]="setup"; st.rerun()

    with st.expander("🔄 Pull today's live openings with AI (optional)"):
        st.caption("Ask the AI for employers actively hiring right now. This is an AI suggestion, not a live jobs feed.")
        if not api_key:
            st.info("Add an API key in the sidebar to enable live AI openings.")
        elif st.button("🔄 Update today's live openings", use_container_width=True):
            with st.spinner("Asking the AI who's hiring today…"):
                live=fetch_live_openings(api_key)
            if not live:
                st.warning("Couldn't fetch live openings right now — the daily board above is always available.")
            else:
                st.success(f"AI-suggested openings for {_today_human()}:")
                for comp, rlist in live:
                    lc1,lc2=st.columns([3,1])
                    with lc1: st.markdown(f"**{comp}** — {', '.join(rlist)}")
                    with lc2:
                        if st.button("Practice →", key=f"live_{comp}", use_container_width=True):
                            st.session_state["target_company"]=comp
                            st.session_state["company_sector"]=""
                            st.session_state["target_role"]=rlist[0]
                            st.session_state["stage"]="setup"; st.rerun()
    st.divider()
    _toggle = getattr(st, "toggle", st.checkbox)
    want_company = _toggle("🏢 Target a specific company (optional)", value=False,
                           help="Leave off for a general interview. Turn on to tailor questions to a company.")

    sector_names = list(US_COMPANIES.keys())

    if not want_company:
        st.session_state["target_company"] = GENERAL
        st.session_state["company_sector"] = ""
        st.info("General interview — standard FAANG-style questions. (No company selected.)")
        cat = st.selectbox("Role category", list(ROLE_CATEGORIES.keys()))
        role = st.selectbox("Role", ROLE_CATEGORIES[cat])
        st.session_state["target_role"] = role
    else:
        sel_sector = st.selectbox("1️⃣ Pick a sector", sector_names)
        companies_in_sector = list(US_COMPANIES[sel_sector].keys())
        sel_company = st.selectbox("2️⃣ Pick a company", companies_in_sector)
        st.session_state["target_company"] = sel_company
        st.session_state["company_sector"] = sel_sector

        info = US_COMPANIES[sel_sector][sel_company]
        color = info.get("color","#534AB7")
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,{color}26,#15151f 70%);border:2px solid {color};border-radius:14px;padding:18px 22px;margin:12px 0;box-shadow:0 14px 34px -24px {color}">
          <div style="display:flex;align-items:center;gap:14px;margin-bottom:10px">
            <div style="background:{color};color:white;border-radius:10px;width:46px;height:46px;display:flex;align-items:center;justify-content:center;font-size:17px;font-weight:700;box-shadow:0 6px 16px -6px {color}">{info.get('logo','?')[:2]}</div>
            <div><p style="font-weight:700;font-size:18px;margin:0;color:#f3f1ff">{sel_company}</p>
            <p style="font-size:12px;color:#a9a9bd;margin:0">📍 {info.get('hq','')} · {sel_sector}</p></div>
          </div>
          <p style="font-size:13px;color:#c7c7d6;margin:0;line-height:1.6"><strong style="color:#fff">Interview style:</strong> {info.get('style','')}</p>
        </div>""", unsafe_allow_html=True)

        roles = info.get("roles",[]) + ["Other — type manually"]
        sel_role = st.selectbox(f"3️⃣ Role at {sel_company}", roles)
        if sel_role == "Other — type manually":
            sel_role = st.text_input("Type the role title", placeholder="e.g. Machine Learning Engineer")
        st.session_state["target_role"] = sel_role

    st.divider()

    # ── Resume upload + match analysis ─────────────────────────────────────────
    st.subheader("📄 Upload Your Resume (optional)")
    st.caption("Claude/GPT/Gemini will tailor questions to your background and score your match")
    resume_file = st.file_uploader("Resume PDF", type=["pdf"], label_visibility="collapsed")
    if resume_file:
        if PYPDF_OK:
            rt = extract_pdf_text(resume_file)
            if rt:
                st.session_state["resume_text"] = rt
                st.success(f"✅ Resume read — {len(rt):,} characters")
            else:
                st.warning("Could not read text from this PDF.")
        else:
            st.warning("Run: pip install pypdf")

    target_company = st.session_state.get("target_company","")
    target_role    = st.session_state.get("target_role","")

    if st.session_state.get("resume_text") and target_role and api_key:
        if st.button("🔍 Analyze my resume for this company & role", use_container_width=True):
            with st.spinner("Analyzing your resume..."):
                raw = call_ai(api_key, prompt_resume_analysis(st.session_state["resume_text"], target_company, target_role), max_tokens=600)
                analysis = parse_resume_analysis(raw)
            st.session_state["resume_analysis"]=analysis
            st.session_state["resume_match_score"]=analysis.get("MATCH_SCORE",0)
            st.session_state["resume_summary"]=analysis.get("SUMMARY","")

    if "resume_analysis" in st.session_state:
        a=st.session_state["resume_analysis"]; match=a.get("MATCH_SCORE",0)
        bc="#085041" if match>=70 else("#633806" if match>=50 else "#791F1F")
        st.markdown(f"""
        <div style="background:#13131f;border:1px solid #23233a;border-radius:12px;padding:16px 20px;margin:12px 0;color:#e7e7f0">
          <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
            <p style="font-weight:600;font-size:14px;margin:0;color:#e7e7f0">📊 Resume Match Score</p>
            <p style="font-size:22px;font-weight:700;margin:0;color:{bc}">{match}%</p>
          </div>
          <div class="resume-match-bar"><div style="height:8px;border-radius:99px;background:{bc};width:{match}%"></div></div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px;font-size:13px">
            <div><strong>✅ Strengths:</strong><br>{a.get('STRENGTHS','—')}</div>
            <div><strong>⚠️ Gaps:</strong><br>{a.get('GAPS','—')}</div>
            <div><strong>🛠 Skills:</strong><br>{a.get('SKILLS','—')}</div>
            <div><strong>💡 Summary:</strong><br>{a.get('SUMMARY','—')}</div>
          </div>
        </div>""", unsafe_allow_html=True)

    st.divider()
    if st.button("Continue to Interview Setup →", type="primary", use_container_width=True):
        if not target_role or not str(target_role).strip():
            st.error("Please choose a role first."); st.stop()
        st.session_state["stage"]="setup"
        st.rerun()


# ── STAGE: SETUP ───────────────────────────────────────────────────────────────
elif st.session_state["stage"]=="setup":
    st.components.v1.html(LOCKDOWN_JS,height=0)
    company=st.session_state.get("target_company","")
    role_prefill=st.session_state.get("target_role","")
    st.title("🔒 Interview Configuration")
    cd=f" → **{company}**" if company and company!=GENERAL else ""
    st.caption(f"**{st.session_state['candidate_name']}**{cd}")
    st.divider()
    col1,col2=st.columns(2)
    with col1:
        if company and company!=GENERAL:
            info,_=get_company_info(company)
            roles=info.get("roles",[])
            if role_prefill and role_prefill not in roles: roles=[role_prefill]+roles
            idx=roles.index(role_prefill) if role_prefill in roles else 0
            role=st.selectbox("Role",roles if roles else ["Software Engineer"],index=idx)
        else:
            role=st.text_input("Role",value=role_prefill or "Software Engineer")
        lang_label=st.selectbox("🌐 Interview Language",list(LANGUAGES.keys()))
        difficulty_label=st.selectbox("Experience Level",list(DIFFICULTIES.keys()))
    with col2:
        qmap={"🔧 Technical + Coding (Recommended)":"technical_coding","💬 Behavioral Only":"behavioral","🔀 Full Mix":"mixed"}
        q_type_label=st.selectbox("Question Focus",list(qmap.keys()))
        num_questions=st.slider("Number of Questions",5,50,10,
                                help="Generate anywhere from a quick 5-question screen up to a full 50-question marathon.")
        diff_key=DIFFICULTIES[difficulty_label]
        default_mins=DEFAULT_TIME[diff_key]//60
        st.markdown('<div class="time-config">',unsafe_allow_html=True)
        custom_mins=st.slider("⏱️ Minutes per question",1,10,default_mins)
        st.markdown(f"<small style='color:#666'>= {custom_mins*60}s per question</small>",unsafe_allow_html=True)
        st.markdown('</div>',unsafe_allow_html=True)
        if st.session_state.get("resume_text"): st.success("📄 Resume loaded — questions tailored")
        if company and company!=GENERAL: st.info(f"🎯 Tailored for **{company}**")
    if st.session_state.get("demo_mode"):
        st.info("🧪 Offline Demo Mode is ON — pre-loaded questions, instant offline scoring, no API key needed.")
    st.warning("📷 This interview is **proctored** — your camera will turn on. Please allow camera access when prompted.")
    st.markdown("")
    if st.button("🚀 Start Secure Proctored Interview",type="primary",use_container_width=True):
        demo=st.session_state.get("demo_mode",False)
        if not demo and not api_key: st.error("Enter your AI API key in the sidebar, or turn on 🧪 Offline Demo Mode."); st.stop()
        difficulty=DIFFICULTIES[difficulty_label]
        q_type=qmap[q_type_label]
        language=LANGUAGES[lang_label]
        company_style=get_company_style(company) if company else ""
        if demo:
            questions,tags=demo_questions(num_questions,q_type)
        else:
            pipeline_stepper("generate")
            with st.spinner(f"Generating {num_questions} questions..."):
                raw=call_ai(api_key,prompt_questions(role,difficulty,q_type,num_questions,language,st.session_state.get("resume_text",""),company,company_style))
                questions,tags=parse_questions(raw)
        if not questions: st.error("Could not generate questions. Check your API key."); st.stop()
        st.session_state.update({
            "stage":"syscheck","questions":questions,"q_types_list":tags,
            "current_q":0,"answers":{},"feedbacks":{},"scores":{},"violations":0,
            "role":role,"difficulty":difficulty,"difficulty_label":difficulty_label,
            "language":language,"api_key":api_key,"time_limit":custom_mins*60,
            "q_start_time":time.time(),"interview_start":datetime.now().strftime("%Y-%m-%d %H:%M"),
            "session_saved":False,"company_style":company_style,
        })
        st.rerun()

# ── STAGE: SYSTEM CHECK (auto camera + face capture; Start unlocks only when ready)
elif st.session_state["stage"]=="syscheck":
    _opts = st.session_state.get("proctoring_opts") or default_proctoring()
    st.components.v1.html(lockdown_js(_opts), height=0)
    _is_custom = st.session_state.get("is_custom", False)
    _cfg = st.session_state.get("custom_cfg") or {}
    if _is_custom:
        hero(f"🎥 {_cfg.get('company','Company')} · {_cfg.get('title','Interview')}",
             "This is an official company interview. The checks below run automatically; Start appears when they pass.",
             ["🏢 "+_cfg.get('company','Company'),"🔒 Custom lockdown","🚀 Start when ready"])
    else:
        hero("🎥 Getting you ready…",
             "Everything runs automatically — camera turns on, your face is detected, and your photo is captured. The Start button appears only when the checks pass.",
             ["🎥 Auto camera","🤳 Auto face photo","🚀 Start when ready"])

    _need_panel = any(_opts.get(k) for k in ("camera","gaze","voice","face_required"))
    _gate = bool(_opts.get("face_required"))

    cca, ccb = st.columns([1,1])
    with cca:
        if _need_panel:
            st.markdown("##### Live proctoring monitor")
            st.caption("Camera and the active checks run automatically. Allow camera access if the browser asks.")
            st.components.v1.html(
                proctor_panel(compact=False, show_fullscreen=False,
                              gaze=bool(_opts.get("gaze")), voice=bool(_opts.get("voice")),
                              precheck=bool(_opts.get("face_required")), hide_voice_ui=True,
                              start_gate=_gate, start_label="Start Interview"),
                height=640)
        else:
            st.info("This interview has no camera proctoring enabled by the recruiter. Press Start when ready.")
    with ccb:
        st.markdown("##### 🔒 Active protections for this interview")
        _active=[lbl for k,(lbl,_,_) in PROCTOR_OPTIONS.items() if _opts.get(k)]
        if _active:
            st.markdown("\n".join(f"- {l}" for l in _active))
        else:
            st.markdown("_No lockdown protections enabled for this interview._")
        if _gate:
            st.info("The green **START INTERVIEW** button appears automatically once your camera and face are detected. If the camera fails, it won't appear.")
        else:
            st.info("Press **Start Interview** below when you're ready.")
        st.warning("Camera & mic need a secure context: they work on **localhost** and any **https://** deployment, but not plain http on a remote server.")
        st.markdown("")
        # When face-gating is on, JS hides this button until checks pass; otherwise it's clickable now.
        if st.button("🚀 Start Interview", type="primary", use_container_width=True, key="real_start_btn"):
            st.session_state["stage"]="interviewing"
            st.session_state["q_start_time"]=time.time()
            st.rerun()
        if not _is_custom:
            if st.button("← Back to setup", use_container_width=True):
                st.session_state["stage"]="setup"; st.rerun()
        else:
            if st.button("← Cancel", use_container_width=True):
                st.session_state["stage"]="company_select"; st.session_state["is_custom"]=False; st.rerun()

# ── STAGE: INTERVIEWING (with camera proctoring) ───────────────────────────────
elif st.session_state["stage"]=="interviewing":
    _opts = st.session_state.get("proctoring_opts") or default_proctoring()
    st.components.v1.html(lockdown_js(_opts), height=0)
    questions=st.session_state["questions"]; tags=st.session_state.get("q_types_list",["CONCEPT"]*len(questions))
    current=st.session_state["current_q"]; total=len(questions)
    role=st.session_state["role"]; difficulty=st.session_state["difficulty"]
    api_key=st.session_state.get("api_key",api_key)
    time_limit=st.session_state.get("time_limit",240)
    elapsed=int(time.time()-st.session_state.get("q_start_time",time.time()))
    remaining=max(0,time_limit-elapsed)
    language=st.session_state.get("language","English")
    company=st.session_state.get("target_company","")
    answered=list(st.session_state["scores"].values())
    make_harder=len(answered)>=2 and sum(answered)/len(answered)>=8.5
    _cur_tag = tags[current] if current < len(tags) else "CONCEPT"
    _submit_label = "Submit Code for AI Check" if _cur_tag=="CODING" else "Submit Answer"

    # Layout: main interview column + camera column
    _cam_on = any(_opts.get(k) for k in ("camera","gaze","voice"))
    if _cam_on:
        main_col, cam_col = st.columns([3,1])
    else:
        main_col = st.container(); cam_col = None

    if cam_col is not None:
      with cam_col:
        st.components.v1.html(proctor_panel(compact=True, show_fullscreen=False,
                                            gaze=bool(_opts.get("gaze")), voice=bool(_opts.get("voice")),
                                            autofs=bool(_opts.get("fullscreen")), hide_voice_ui=True), height=430)
        v=st.session_state.get("violations",0)
        bg="#c53030" if v>0 else "#276749"
        st.markdown(f'<div style="background:{bg};color:#fff;border-radius:8px;padding:6px 10px;font-size:12px;font-weight:600;text-align:center;margin-top:6px">{"⚠️" if v>0 else "✅"} Violations: {v}</div>',unsafe_allow_html=True)
        st.components.v1.html(live_timer(remaining, submit_label=_submit_label, compact=True), height=70)
        if _opts.get("gaze"):
            st.markdown('<div style="font-size:11px;color:#a9a9bd;margin-top:8px;line-height:1.5">👁️ Keep your head facing the screen. After <b>2 head turns</b> you get one <b>red warning</b>; at the <b>4th head turn</b> the exam <b>ends as FAILED</b>.</div>',unsafe_allow_html=True)
            if st.button("⛔ End & see FAILED report + certificate", use_container_width=True, type="primary",
                         help="If the on-screen overlay says EXAM TERMINATED, click here to download your detailed report and certificate."):
                st.session_state["terminated_reason"]="The candidate's head was turned away from the screen four times during the interview (a clear red warning was shown after the second time)."
                st.session_state["stage"]="terminated"; st.rerun()

    with main_col:
        co_tag=f' <span class="lang-badge">🏢 {company}</span>' if company and company!=GENERAL else ""
        lang_tag=f' <span class="lang-badge">🌐 {language}</span>' if language!="English" else ""
        if not _cam_on:
            th1, th2 = st.columns([3,1])
            with th1: st.markdown(f"### 🔒 {role}{co_tag}{lang_tag}",unsafe_allow_html=True)
            with th2: st.components.v1.html(live_timer(remaining, submit_label=_submit_label), height=70)
        else:
            st.markdown(f"### 🔒 {role}{co_tag}{lang_tag}",unsafe_allow_html=True)
        st.caption(f"{st.session_state.get('difficulty_label','')} · {st.session_state['candidate_name']}")
        if answered:
            avg_sf=sum(answered)/len(answered)
            if avg_sf>=8.5: aat,aac="↑ Harder","diff-up"
            elif avg_sf<=4: aat,aac="↓ Adjusted","diff-down"
            else: aat,aac="→ Steady","diff-same"
            st.markdown(f'Adaptive: <span class="adaptive-badge {aac}">{aat}</span>',unsafe_allow_html=True)
        pct=int((current/total)*100)
        st.markdown(f'<div class="progress-bar-wrap"><div class="progress-bar-fill" style="width:{pct}%"></div></div>',unsafe_allow_html=True)
        st.markdown(f"**Question {current+1} of {total}** &nbsp; {tag_badge(tags[current])}",unsafe_allow_html=True)
        st.markdown('<div class="secure-banner">🔒 Proctored — camera recording, copy/paste & DevTools blocked. All actions logged.</div>',unsafe_allow_html=True)

        q_text=questions[current]; tag=tags[current]
        card_cls="code-question" if tag=="CODING" else "question-card"
        prefix="🧑‍💻 " if tag=="CODING" else("🏗️ " if tag=="DESIGN" else "❓ ")
        st.markdown(f'<div class="{card_cls}">{prefix}{q_text}</div>',unsafe_allow_html=True)
        if tag=="CODING": st.caption("💡 Write working code + explain O(?) complexity + edge cases.")
        elif tag=="DESIGN": st.caption("💡 Describe components, data flow, scalability and trade-offs.")

        is_coding = (tag=="CODING")
        code_lang = None
        if is_coding:
            # ── Multi-language code editor ─────────────────────────────────────
            default_lang = st.session_state.get("preferred_code_lang","Python")
            li = CODE_LANGUAGES.index(default_lang) if default_lang in CODE_LANGUAGES else 0
            code_lang = st.selectbox("👨‍💻 Coding language", CODE_LANGUAGES, index=li, key=f"lang_{current}")
            st.session_state["preferred_code_lang"] = code_lang
            answer = st.text_area(f"Write your {code_lang} solution here",
                height=300,
                placeholder=f"# Write complete, runnable {code_lang} code.\n# The AI will trace your code, check correctness, and award points only if it is correct.",
                key=f"code_{current}")
            st.caption("After you submit, the AI dry-runs your code, shows the output, and awards points **only if it is correct**.")
            bc1,bc2=st.columns([4,1])
            with bc1: submit=st.button("✅ Submit Code for AI Check",type="primary",use_container_width=True)
            with bc2: skip=st.button("⏭ Skip",use_container_width=True)
        else:
            # ── Voice answer (optional) — record, transcribe with Whisper ──────
            with st.expander("🎙️ Answer by voice instead (optional)"):
                st.caption("Record your spoken answer — Whisper transcribes it into the box below. Uses your OpenAI key.")
                audio = None
                if hasattr(st, "audio_input"):
                    audio = st.audio_input("Record your answer", key=f"aud_{current}")
                else:
                    audio = st.file_uploader("Upload a voice clip (wav/mp3/m4a)", type=["wav","mp3","m4a"], key=f"audf_{current}")
                if audio is not None:
                    if st.button("📝 Transcribe my voice answer", key=f"tr_{current}", use_container_width=True):
                        with st.spinner("Transcribing with Whisper..."):
                            okey = st.session_state.get("_openai_key","") or (api_key if st.session_state.get("provider")=="openai" else "")
                            text = transcribe_audio(okey, audio.getvalue())
                        if text:
                            st.session_state[f"voicetext_{current}"] = text
                            st.success("Transcribed! Review and edit below, then submit.")
            prefill = st.session_state.get(f"voicetext_{current}", "")
            answer=st.text_area("Your Answer",height=220, value=prefill,
                placeholder="Type here, or use 🎙️ voice above.\nFor BEHAVIORAL: Situation → Action → Result",
                key=f"ans_{current}")
            bc1,bc2=st.columns([4,1])
            with bc1: submit=st.button("✅ Submit Answer",type="primary",use_container_width=True)
            with bc2: skip=st.button("⏭ Skip",use_container_width=True)

    if remaining==0 and current not in st.session_state["answers"]:
        st.error("⏰ Time's up!"); submit=True; st.session_state["violations"]+=1

    if skip:
        st.session_state["answers"][current]="(skipped)"
        st.session_state["feedbacks"][current]={"SCORE":0,"STRENGTH":"—","IMPROVEMENT":"Skipped.","IDEAL":"—","COMPLEXITY":"N/A","TIP":"Always attempt."}
        st.session_state["scores"][current]=0
        if current+1<total: st.session_state["current_q"]+=1; st.session_state["q_start_time"]=time.time()
        else: st.session_state["stage"]="results"
        st.rerun()

    if submit:
        if not answer.strip() and remaining>0: st.warning("Write your answer first."); st.stop()
        demo=st.session_state.get("demo_mode",False)
        if not demo: pipeline_stepper("evaluate")
        if is_coding:
            if demo:
                ce=demo_code_eval(answer)
            else:
                with st.spinner(f"AI is checking your {code_lang} code..."):
                    raw_ce=call_ai(api_key,prompt_code_eval(q_text, code_lang, answer or "(no code)"))
                    ce=parse_code_eval(raw_ce)
            verdict=ce.get("VERDICT","INCORRECT").upper()
            fb={"SCORE":ce["SCORE"],
                "STRENGTH":(f"Verdict: {ce.get('VERDICT','?')} — output: {ce.get('OUTPUT','—')}"),
                "IMPROVEMENT":ce.get("ISSUES","—"),
                "IDEAL":f"[{code_lang}] Expected behaviour / output:\n{ce.get('OUTPUT','—')}",
                "COMPLEXITY":ce.get("COMPLEXITY","N/A"),
                "TIP":ce.get("TIP","—"),
                "LANG":code_lang,"VERDICT":ce.get("VERDICT","?")}
            st.session_state["answers"][current]=f"```{code_lang}\n{answer}\n```"
        else:
            if demo:
                fb=demo_feedback(answer)
            else:
                with st.spinner("Evaluating..."):
                    raw_fb=call_ai(api_key,prompt_feedback(role,difficulty,q_text,answer or "(no answer)",elapsed,language,company))
                    fb=parse_feedback(raw_fb)
            st.session_state["answers"][current]=answer
        st.session_state["feedbacks"][current]=fb
        st.session_state["scores"][current]=fb["SCORE"]
        score=fb["SCORE"]; cls=sc(score); emoji=se(score)
        if is_coding:
            vv=fb.get("VERDICT","?").upper()
            vbadge=("✅ CORRECT — points awarded" if "CORRECT" in vv and "INCORRECT" not in vv
                    else ("🟡 PARTIAL — partial points" if "PARTIAL" in vv else "❌ INCORRECT — no points"))
            st.markdown(f'<div style="margin:6px 0 10px;font-weight:700;color:#e7e7f0">{vbadge}</div>',unsafe_allow_html=True)
        cx=""
        if fb.get("COMPLEXITY","N/A") not in("N/A","—",""):
            cx=f'<p style="margin:8px 0 6px"><strong>⚡ Complexity</strong><br><code>{fb["COMPLEXITY"]}</code></p>'
        st.markdown(f"""<div class="feedback-box">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px">
            <span style="font-size:24px">{emoji}</span>
            <span class="score-pill {cls}">Score: {score}/10</span>
            <span style="font-size:12px;color:#888">⏱ {elapsed}s</span></div>
          <p style="margin:0 0 6px"><strong>✅ Strength</strong><br>{fb['STRENGTH']}</p>
          <p style="margin:8px 0 6px"><strong>📈 Improve</strong><br>{fb['IMPROVEMENT']}</p>
          <p style="margin:8px 0 6px"><strong>💡 Model Answer</strong><br>
            <pre style="white-space:pre-wrap;font-size:13px;background:#f0f0f0;padding:10px;border-radius:6px">{fb['IDEAL']}</pre></p>
          {cx}
          <p style="margin:8px 0 0"><strong>🔑 Tip</strong><br>{fb['TIP']}</p>
        </div>""",unsafe_allow_html=True)
        if score>=9: st.balloons()
        st.markdown("")
        if current+1<total:
            if st.button(f"Next ({current+2}/{total}) {'Harder ↑' if make_harder else '→'}",type="primary",use_container_width=True):
                st.session_state["current_q"]+=1; st.session_state["q_start_time"]=time.time(); st.rerun()
        else:
            if st.button("🏁 See Final Results",type="primary",use_container_width=True):
                st.session_state["stage"]="results"; st.rerun()


# ── STAGE: TERMINATED (failed by proctor) ──────────────────────────────────────
elif st.session_state["stage"]=="terminated":
    # Remove any leftover full-screen proctor overlays from the previous page,
    # and exit full-screen so the report is fully readable/clickable.
    st.components.v1.html("""<script>
    (function(){ try{
      var d=(window.parent&&window.parent.document)?window.parent.document:document;
      ['gz-term-ov','gz-warn-ov','pp-voicebar'].forEach(function(id){ var e=d.getElementById(id); if(e&&e.parentNode) e.parentNode.removeChild(e); });
      if(d.fullscreenElement && d.exitFullscreen){ d.exitFullscreen(); }
    }catch(e){} })();
    </script>""", height=0)
    role=st.session_state.get("role","Candidate")
    cname=st.session_state.get("candidate_name","")
    cemail=st.session_state.get("candidate_email","")
    company=st.session_state.get("target_company","")
    language=st.session_state.get("language","English")
    diff_label=st.session_state.get("difficulty_label","")
    started=st.session_state.get("interview_start","")
    reason=st.session_state.get("terminated_reason","Repeated proctoring violations were detected.")
    violations=st.session_state.get("violations",0)+2

    hero("⛔ Interview Terminated — FAILED",
         "The AI proctor ended this session due to repeated head / eye movement away from the screen.",
         ["🚫 Integrity violation","👁️ Look-away detected","📄 Failed report issued"])

    st.error("This attempt is recorded as **FAILED** because the proctoring rules were broken more than once "
             "(looking away / turning the head after a first warning).")
    st.markdown(
        "**What happened**\n\n"
        f"- {reason}\n"
        "- You received a **first warning** during the interview.\n"
        "- The same behaviour was detected again, so the exam **ended immediately**.\n\n"
        "**What this means**\n\n"
        "- No interview score is awarded for this attempt.\n"
        "- A failed proctoring report and certificate have been generated below.\n"
        "- You may **retake** the interview in a quiet, well-lit room, looking at the screen and keeping your head still."
    )

    # Save a failed session so recruiters can see it
    started_dt=st.session_state.get("q_start_time")
    if not st.session_state.get("session_saved"):
        save_session({"candidate_name":cname,"candidate_email":cemail,"role":role,
            "target_company":company,"difficulty_label":diff_label,"date":started,
            "language":language,"avg_score":0,"avg_coding":"N/A","avg_tech":"N/A","avg_behav":"N/A",
            "violations":violations,"resume_used":bool(st.session_state.get("resume_text","")),
            "resume_match":st.session_state.get("resume_match_score",0),
            "resume_summary":st.session_state.get("resume_summary",""),
            "status":"FAILED — proctoring","terminated_reason":reason,
            "questions":st.session_state.get("questions",[]),"tags":st.session_state.get("q_types_list",[]),
            "answers":{},"scores":{},"feedbacks":{},
        })
        st.session_state["session_saved"]=True

    # Build detailed certificate + detailed report
    qs=st.session_state.get("questions",[]); tgs=st.session_state.get("q_types_list",[])
    answers=st.session_state.get("answers",{}); scores=st.session_state.get("scores",{}); feedbacks=st.session_state.get("feedbacks",{})
    meta={"name":cname,"email":cemail,"role":role,"company":company,"date":started,
          "difficulty":diff_label,"language":language,"avg":0,"coding":"N/A","technical":"N/A",
          "behavioral":"N/A","violations":violations,"match":st.session_state.get("resume_match_score",0),
          "duration":"ended early (terminated)"}
    _issuer = (st.session_state.get("custom_cfg") or {}).get("company","") if st.session_state.get("is_custom") else ""
    cert_html=make_certificate(cname,role,0,started,violations,language,company,failed=True,reason=reason,issuer=_issuer)
    report_html=make_detailed_report(meta, qs, tgs, answers, scores, feedbacks, failed=True, reason=reason)
    cert_pdf=make_certificate_pdf(cname,role,0,started,violations,language,company,failed=True,reason=reason,issuer=_issuer)
    report_pdf=make_report_pdf(meta, qs, tgs, answers, scores, feedbacks, failed=True, reason=reason)
    _safe=cname.replace(' ','_')

    st.subheader("📄 Your failed report & certificate")
    if not REPORTLAB_OK:
        st.caption("ℹ️ For PDF downloads run: `pip install reportlab`. HTML versions are provided below.")
    dl1, dl2 = st.columns(2)
    with dl1:
        if report_pdf:
            st.download_button("⬇️ Download DETAILED Report (PDF)", data=report_pdf,
                file_name=f"interview_report_{_safe}.pdf", mime="application/pdf", use_container_width=True)
        else:
            st.download_button("⬇️ Download DETAILED Report (HTML)", data=report_html,
                file_name=f"interview_report_{_safe}.html", mime="text/html", use_container_width=True)
        st.caption("Full report: session details, every question, and the proctoring reason.")
    with dl2:
        if cert_pdf:
            st.download_button("⬇️ Download FAILED Certificate (PDF)", data=cert_pdf,
                file_name=f"certificate_FAILED_{_safe}.pdf", mime="application/pdf", use_container_width=True)
        else:
            cert_b64=base64.b64encode(cert_html.encode()).decode()
            st.markdown(f"""<a href="data:text/html;base64,{cert_b64}" download="certificate_FAILED_{_safe}.html"
               style="display:block;text-align:center;background:#791F1F;color:#fff;padding:11px 22px;border-radius:10px;font-size:14px;font-weight:700;text-decoration:none">⬇️ Download FAILED Certificate (HTML)</a>""", unsafe_allow_html=True)
        st.caption("Printable certificate marked FAILED with the integrity-violation note.")

    st.divider()
    if st.button("🔄 Retake interview", type="primary", use_container_width=True):
        for k in ["stage","questions","q_types_list","current_q","answers","feedbacks",
                  "scores","violations","q_start_time","session_saved","adaptive_difficulty",
                  "target_company","target_role","company_sector","resume_summary","resume_match_score",
                  "terminated_reason"]:
            st.session_state[k]=defaults.get(k, "" )
        if "resume_analysis" in st.session_state: del st.session_state["resume_analysis"]
        st.session_state["stage"]="company_select"
        st.rerun()

# ── STAGE: RESULTS ─────────────────────────────────────────────────────────────
elif st.session_state["stage"]=="results":
    st.components.v1.html(LOCKDOWN_JS,height=0)
    questions=st.session_state["questions"]; tags=st.session_state.get("q_types_list",[])
    feedbacks=st.session_state["feedbacks"]; answers=st.session_state["answers"]
    scores=st.session_state["scores"]; role=st.session_state["role"]
    violations=st.session_state.get("violations",0); api_key=st.session_state.get("api_key",api_key)
    diff_label=st.session_state.get("difficulty_label",""); started=st.session_state.get("interview_start","")
    cname=st.session_state.get("candidate_name",""); cemail=st.session_state.get("candidate_email","")
    language=st.session_state.get("language","English"); total=len(questions)
    company=st.session_state.get("target_company","")

    valid=[s for s in scores.values() if s>0]
    avg=round(sum(valid)/len(valid),1) if valid else 0
    c_s=[scores[i] for i,t in enumerate(tags) if t=="CODING"             and i in scores and scores[i]>0]
    t_s=[scores[i] for i,t in enumerate(tags) if t in("CONCEPT","DESIGN") and i in scores and scores[i]>0]
    b_s=[scores[i] for i,t in enumerate(tags) if t=="BEHAVIORAL"          and i in scores and scores[i]>0]
    ac=round(sum(c_s)/len(c_s),1) if c_s else "N/A"
    at=round(sum(t_s)/len(t_s),1) if t_s else "N/A"
    ab=round(sum(b_s)/len(b_s),1) if b_s else "N/A"

    if not st.session_state.get("session_saved"):
        session_data={"candidate_name":cname,"candidate_email":cemail,"role":role,
            "target_company":company,"difficulty_label":diff_label,"date":started,
            "language":language,"avg_score":avg,"avg_coding":ac,"avg_tech":at,"avg_behav":ab,
            "violations":violations,"resume_used":bool(st.session_state.get("resume_text","")),
            "resume_match":st.session_state.get("resume_match_score",0),
            "resume_summary":st.session_state.get("resume_summary",""),
            "questions":questions,"tags":tags,
            "answers":{str(k):v for k,v in answers.items()},
            "scores":{str(k):v for k,v in scores.items()},
            "feedbacks":{str(k):v for k,v in feedbacks.items()},
        }
        save_session(session_data); st.session_state["session_saved"]=True

    if avg>=8: verdict,vc,tip="Excellent — ready to apply!","#085041","Strong performance. Apply with confidence."
    elif avg>=6: verdict,vc,tip="Good — more prep needed","#633806","Review weak areas and practice 2-3 more sessions."
    else: verdict,vc,tip="Keep practicing!","#791F1F","Review fundamentals, practice LeetCode, study system design."

    st.title("🏆 Interview Results")
    company_line=f" → **{company}**" if company and company!=GENERAL else ""
    st.caption(f"{role}{company_line} · {diff_label} · {cname} · 🌐 {language}")
    if violations>0: st.error(f"⚠️ {violations} integrity violation(s) recorded.")
    if avg>=8: st.balloons()

    match_score=st.session_state.get("resume_match_score",0)

    def _stat_card(icon,label,value,suffix,accent):
        return (f'<div style="flex:1;min-width:150px;background:linear-gradient(135deg,#181826,#1f1f33);'
                f'border:1px solid #2b2b42;border-left:5px solid {accent};border-radius:14px;'
                f'padding:14px 18px;box-shadow:0 12px 30px -20px rgba(0,0,0,.8)">'
                f'<div style="font-size:12px;color:#c7c7d6;font-weight:700;letter-spacing:.3px;display:flex;align-items:center;gap:7px">{icon} {label}</div>'
                f'<div style="font-size:26px;font-weight:800;color:{accent};margin-top:6px;line-height:1">'
                f'{value}<span style="font-size:14px;color:#9a9ab0;font-weight:600">{suffix}</span></div></div>')

    cards =  _stat_card("🧑‍💻","Coding",   ac, "/10", "#f5b942")
    cards += _stat_card("💡","Technical",   at, "/10", "#38bdf8")
    cards += _stat_card("💬","Behavioral",  ab, "/10", "#a78bfa")
    if violations>0:
        cards += _stat_card("⚠️","Integrity", violations, "", "#f87171")
    else:
        cards += _stat_card("✅","Integrity", 0, "", "#34d399")
    if match_score>0:
        macc = "#34d399" if match_score>=70 else ("#f5b942" if match_score>=50 else "#f87171")
        cards += _stat_card("📄","Resume Match", match_score, "%", macc)

    # Animated circular score gauge (SVG)
    try: _av=float(avg)
    except: _av=0.0
    _circ=2*3.14159*52
    _off=_circ*(1-max(0.0,min(1.0,_av/10)))
    ring=(f'<svg width="132" height="132" viewBox="0 0 120 120" style="flex-shrink:0">'
          f'<defs><linearGradient id="scoreg" x1="0" y1="0" x2="1" y2="1">'
          f'<stop offset="0" stop-color="#7c5cff"/><stop offset="1" stop-color="#29b5e8"/></linearGradient></defs>'
          f'<circle cx="60" cy="60" r="52" fill="none" stroke="#2b2b42" stroke-width="11"/>'
          f'<circle cx="60" cy="60" r="52" fill="none" stroke="url(#scoreg)" stroke-width="11" stroke-linecap="round" '
          f'stroke-dasharray="{_circ:.0f}" stroke-dashoffset="{_off:.0f}" transform="rotate(-90 60 60)">'
          f'<animate attributeName="stroke-dashoffset" from="{_circ:.0f}" to="{_off:.0f}" dur="1.1s" fill="freeze"/></circle>'
          f'<text x="60" y="62" text-anchor="middle" fill="#fff" font-size="32" font-weight="800" font-family="Inter">{avg}</text>'
          f'<text x="60" y="82" text-anchor="middle" fill="#9a9ab0" font-size="12" font-family="Inter">/ 10</text></svg>')

    st.markdown(f"""
    <div style="background:linear-gradient(135deg,#13131f,#16162400);border:1px solid #23233a;border-radius:16px;padding:24px 28px;margin-bottom:20px;box-shadow:0 16px 44px -28px rgba(124,92,255,.5)">
      <div style="display:flex;gap:24px;align-items:center;flex-wrap:wrap">
        {ring}
        <div style="flex:1;min-width:240px">
          <div style="font-size:13px;color:#a9a9bd;margin-bottom:4px;letter-spacing:.5px;text-transform:uppercase">Overall Result</div>
          <div style="font-size:20px;font-weight:800;color:{vc};margin-bottom:8px">{verdict}</div>
          <div style="font-size:13px;color:#b9b9cc">{tip}</div>
        </div>
      </div>
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:18px">{cards}</div>
    </div>""",unsafe_allow_html=True)

    # ── Skill radar chart (coding / technical / behavioral + integrity + match) ──
    def _num(x):
        try: return float(x)
        except: return 0.0
    radar_axes=["Coding","Technical","Behavioral","Integrity","Resume Match"]
    radar_vals=[_num(ac), _num(at), _num(ab),
                max(0,10-min(violations,10)),
                _num(match_score)/10.0]
    if any(v>0 for v in radar_vals):
        st.subheader("📊 Your skill profile")
        drew=False
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import numpy as np
            ang=np.linspace(0,2*np.pi,len(radar_axes),endpoint=False).tolist(); ang+=ang[:1]
            vals=radar_vals+radar_vals[:1]
            fig=plt.figure(figsize=(4.6,4.6)); fig.patch.set_alpha(0)
            axp=plt.subplot(111,polar=True); axp.set_facecolor("none")
            axp.plot(ang,vals,color="#7c5cff",linewidth=2)
            axp.fill(ang,vals,color="#7c5cff",alpha=0.30)
            axp.set_xticks(ang[:-1]); axp.set_xticklabels(radar_axes,color="#cfcfe0",fontsize=9)
            axp.set_yticks([2,4,6,8,10]); axp.set_yticklabels(["2","4","6","8","10"],color="#8a8a9e",fontsize=7)
            axp.set_ylim(0,10)
            for spine in axp.spines.values(): spine.set_color("#3a3a55")
            axp.grid(color="#3a3a55",alpha=0.5)
            c1,c2,c3=st.columns([1,2,1])
            with c2: st.pyplot(fig,use_container_width=True)
            plt.close(fig); drew=True
        except Exception:
            drew=False
        if not drew:
            try:
                import pandas as pd
                st.bar_chart(pd.DataFrame({"score (0-10)":radar_vals}, index=radar_axes))
            except Exception:
                for ax,vv in zip(radar_axes,radar_vals):
                    st.markdown(f"**{ax}:** {round(vv,1)}/10")
        st.caption("Integrity = 10 minus violations · Resume Match scaled to /10. Add an API key for full AI scoring if you ran the demo.")

    _issuer = (st.session_state.get("custom_cfg") or {}).get("company","") if st.session_state.get("is_custom") else ""
    cert_pdf=make_certificate_pdf(cname,role,avg,started,violations,language,company,issuer=_issuer)
    _safe=cname.replace(' ','_')
    st.markdown("""<div style="background:#1a1730;border:1px solid #2a2440;border-radius:12px;padding:16px 20px;margin-bottom:8px;">
      <p style="font-weight:600;font-size:14px;margin:0;color:#e7e7f0;">🎓 Certificate Ready!</p>
      <p style="font-size:12px;color:#b7a6ff;margin:0">Download your professional PDF certificate — add it to LinkedIn or your resume.</p>
    </div>""",unsafe_allow_html=True)
    if cert_pdf:
        st.download_button("⬇️ Download Certificate (PDF)", data=cert_pdf,
            file_name=f"certificate_{_safe}.pdf", mime="application/pdf", use_container_width=True)
    else:
        cert_html=make_certificate(cname,role,avg,started,violations,language,company,issuer=_issuer)
        cert_b64=base64.b64encode(cert_html.encode()).decode()
        st.caption("ℹ️ For a PDF certificate run: `pip install reportlab`. HTML version below.")
        st.markdown(f"""<a href="data:text/html;base64,{cert_b64}" download="certificate_{_safe}.html"
           style="display:inline-block;background:#534AB7;color:#fff;padding:10px 22px;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none;">⬇️ Download Certificate (HTML)</a>""",unsafe_allow_html=True)

    lb=load_json(LEADERBOARD_FILE,{})
    if role in lb:
        pos=next((i+1 for i,e in enumerate(lb[role]) if e["name"]==cname),None)
        if pos==1: st.success(f"🥇 Ranked #1 on the {role} leaderboard!")
        elif pos==2: st.success(f"🥈 Ranked #2 on the {role} leaderboard!")
        elif pos==3: st.success(f"🥉 Ranked #3 on the {role} leaderboard!")
        elif pos: st.info(f"🏆 Ranked #{pos} on the {role} leaderboard.")

    # ── Share via WhatsApp / Email (replaces SMTP) ─────────────────────────────
    st.markdown(share_links_html(role,avg,company),unsafe_allow_html=True)

    st.divider()
    st.subheader("📋 Question Breakdown")
    for i,q in enumerate(questions):
        fb=feedbacks.get(i,{}); score=scores.get(i,0); ans=answers.get(i,""); tag=tags[i] if i<len(tags) else "CONCEPT"
        with st.expander(f"{se(score)} Q{i+1} [{tag}]: {q[:70]}{'…' if len(q)>70 else ''}  —  {score}/10"):
            if ans=="(skipped)": st.warning("Skipped.")
            else:
                st.markdown(f"**Answer:**\n```\n{ans}\n```")
                col_a,col_b=st.columns(2)
                with col_a:
                    st.markdown(f"**Score:** {score}/10\n\n**Strength:** {fb.get('STRENGTH','—')}\n\n**Improve:** {fb.get('IMPROVEMENT','—')}")
                with col_b:
                    if fb.get('COMPLEXITY','N/A') not in('N/A','—',''): st.markdown(f"**Complexity:** `{fb.get('COMPLEXITY','')}`")
                    st.markdown(f"**Tip:** {fb.get('TIP','—')}")
                st.markdown(f"**Model Answer:**\n```\n{fb.get('IDEAL','—')}\n```")

    st.divider()
    if st.session_state.get("demo_mode") and valid:
        if st.button("🤖 Coaching Summary (offline demo)",use_container_width=True):
            top = max(range(total), key=lambda i: scores.get(i,0)) if total else 0
            low = min(range(total), key=lambda i: scores.get(i,0)) if total else 0
            st.info(f"(Offline demo) Overall you scored {avg}/10 for {role}. Your strongest answer was Q{top+1}, "
                    f"and Q{low+1} has the most room to grow. Keep answers structured: state your approach, give an example, "
                    f"note trade-offs or complexity. Add an API key for a fully personalized AI coaching report. You're on the right track — keep practicing!")
    elif api_key and valid:
        if st.button("🤖 Generate Personalised Coaching Summary",use_container_width=True):
            qa="\n".join(f"Q{i+1}[{tags[i] if i<len(tags) else ''}]: {questions[i]}\nA: {answers.get(i,'skipped')}\nScore:{scores.get(i,0)}/10" for i in range(total))
            company_ctx=f"\nTarget company: {company}. Include company-specific advice." if company and company!=GENERAL else ""
            p=f"""Proctored interview: {cname}, {role}, {diff_label}. Violations: {violations}.{company_ctx}
{qa}
Write 5-6 sentence coaching report: overall summary, biggest strength, top weakness, specific study resource for {role}{f' at {company}' if company and company!=GENERAL else ''}, encouraging close. Flowing paragraphs."""
            pipeline_stepper("coach")
            with st.spinner("Writing..."):
                summary=call_ai(api_key,p,max_tokens=600)
            st.info(summary)

    st.markdown("")
    col1,col2=st.columns(2)
    with col1:
        if st.button("🔄 Practice Again",use_container_width=True):
            for k in ["stage","questions","q_types_list","current_q","answers","feedbacks",
                      "scores","violations","q_start_time","session_saved","adaptive_difficulty",
                      "target_company","target_role","company_sector","resume_summary","resume_match_score"]:
                st.session_state[k]=defaults[k]
            if "resume_analysis" in st.session_state: del st.session_state["resume_analysis"]
            st.rerun()
    with col2:
        lines=[f"AI Interview Report — {cname}",f"{role} | {company or 'General'} | {diff_label} | {language}",
               f"Date: {started} | Score: {avg}/10 | Violations: {violations}",
               f"Coding:{ac} Technical:{at} Behavioral:{ab} ResumeMatch:{match_score}%\n{'='*55}"]
        for i,q in enumerate(questions):
            fb=feedbacks.get(i,{})
            lines.append(f"Q{i+1}[{tags[i] if i<len(tags) else ''}]: {q}\nA: {answers.get(i,'')}\nScore:{scores.get(i,0)}/10\n{fb.get('STRENGTH','')} | {fb.get('IMPROVEMENT','')}\n")
        st.download_button("⬇️ Download Report",data="\n".join(lines),
            file_name=f"report_{cname.replace(' ','_')}.txt",mime="text/plain",use_container_width=True)

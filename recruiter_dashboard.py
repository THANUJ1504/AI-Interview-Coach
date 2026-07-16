import re
import streamlit as st
import json, hashlib, hmac, secrets, csv, io
from datetime import datetime, date
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════════════
#  AI Interview Coach — RECRUITER DASHBOARD (standalone app)
#  Run with:  streamlit run recruiter_dashboard.py
#  Shares the same interview_data/ folder as coach.py (candidate app), so it sees
#  every candidate result and any custom interviews you create or import here.
# ══════════════════════════════════════════════════════════════════════════════
st.set_page_config(page_title="Recruiter Dashboard — AI Interview Coach", page_icon="🏢", layout="wide")

# ══════════════════════════════════════════════════════════════════════════════
#  GRAPHICS / THEME  (dark gradients, animated orbs, textured hero, glass cards)
# ══════════════════════════════════════════════════════════════════════════════
st.markdown("""<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
  .stApp{
    background:
      radial-gradient(900px 520px at 12% -8%, #1c1740 0%, rgba(28,23,64,0) 55%),
      radial-gradient(820px 460px at 100% 0%, #10243f 0%, rgba(16,36,63,0) 50%),
      linear-gradient(180deg, #07070d 0%, #0b0b14 100%);
    color:#e7e7f0;
  }
  html, body, [class*="css"]{ font-family:'Inter',-apple-system,Segoe UI,sans-serif; color:#e7e7f0; }
  .stApp, .stMarkdown, .stMarkdown p, label, .stCaption, [data-testid="stCaptionContainer"],
  h1,h2,h3,h4,h5,h6, .stRadio label, .stSelectbox label, .stSlider label, .stTextInput label{ color:#e7e7f0 !important; }
  [data-testid="stCaptionContainer"], .stCaption, small{ color:#a9a9bd !important; }
  a{ color:#b7a6ff; }

  /* Floating gradient orbs behind everything */
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

  section[data-testid="stSidebar"]{ background:linear-gradient(180deg,#0c0c16,#0a0a12); border-right:1px solid #1d1d2c; }
  section[data-testid="stSidebar"] *{ color:#e7e7f0; }

  details, .streamlit-expanderHeader, [data-testid="stExpander"]{ background:#13131f !important; border:1px solid #23233a !important; border-radius:12px !important; transition:transform .15s ease, box-shadow .15s ease; }
  [data-testid="stExpander"] *{ color:#e7e7f0; }
  div[data-testid="stExpander"]:hover{ box-shadow:0 14px 34px -22px rgba(0,0,0,.6); }

  /* Textured hero */
  .hero{ position:relative; overflow:hidden; border-radius:20px; padding:28px 32px; margin-bottom:18px;
    background:linear-gradient(135deg,#4b2aad 0%,#6d3bd1 45%,#8b5cf6 100%);
    background-size:160% 160%; animation:heroFlow 12s ease infinite;
    box-shadow:0 18px 40px -18px rgba(75,42,173,.55); }
  @keyframes heroFlow{ 0%{background-position:0% 50%} 50%{background-position:100% 50%} 100%{background-position:0% 50%} }
  .hero:before{ content:""; position:absolute; inset:0; opacity:.35;
    background-image:radial-gradient(rgba(255,255,255,.18) 1px, transparent 1px); background-size:18px 18px;
    animation:twinkle 6s ease-in-out infinite alternate; }
  @keyframes twinkle{ 0%{opacity:.22} 100%{opacity:.42} }
  .hero:after{ content:""; position:absolute; right:-60px; top:-60px; width:240px; height:240px; border-radius:50%;
    background:radial-gradient(circle at 30% 30%, rgba(255,255,255,.30), rgba(255,255,255,0) 70%); }
  .hero h1{ color:#fff; font-size:27px; font-weight:800; margin:0 0 6px; position:relative; letter-spacing:-.4px; text-shadow:0 2px 18px rgba(0,0,0,.25); }
  .hero p{ color:rgba(255,255,255,.92); font-size:14px; margin:0; position:relative; }
  .hero .pillrow{ margin-top:14px; position:relative; display:flex; gap:8px; flex-wrap:wrap; }
  .hero .hpill{ background:rgba(255,255,255,.18); color:#fff; padding:5px 14px; border-radius:99px; font-size:12px; font-weight:600; backdrop-filter:blur(4px); border:1px solid rgba(255,255,255,.25); }
  .accent-bar{ height:5px;border-radius:99px;margin:0 0 14px;
    background:linear-gradient(90deg,#7c5cff,#29b5e8,#cc785c,#7c5cff); background-size:300% 100%; animation:heroFlow 8s linear infinite; }

  /* Glass surfaces / cards */
  .surface{ background:#13131f; border:1px solid #23233a; border-radius:16px; padding:20px 24px;
            box-shadow:0 10px 30px -20px rgba(0,0,0,.6); margin-bottom:14px; color:#e7e7f0; transition:transform .15s ease, box-shadow .15s ease; }
  .surface:hover{ transform:translateY(-2px); box-shadow:0 18px 40px -22px rgba(124,92,255,.55); }

  /* Stat tiles */
  div[data-testid="stMetric"]{ background:linear-gradient(135deg,#181826,#1f1f33); border:1px solid #2b2b42;
    border-left:5px solid #7c5cff; border-radius:14px; padding:14px 18px; box-shadow:0 12px 30px -20px rgba(0,0,0,.8); }
  div[data-testid="stMetricValue"]{ color:#fff !important; font-weight:800; }
  div[data-testid="stMetricLabel"]{ color:#a9a9bd !important; }

  /* Buttons — gradient + shimmer */
  .stButton>button[kind="primary"], .stButton>button[data-testid="baseButton-primary"]{
    background:linear-gradient(135deg,#5b46d6,#7c3aed)!important; border:none!important; color:#fff!important;
    font-weight:700!important; border-radius:12px!important; box-shadow:0 10px 22px -10px rgba(124,58,237,.7)!important;
    transition:transform .12s ease, box-shadow .12s ease; }
  .stButton>button[kind="primary"]:hover{ transform:translateY(-1px); box-shadow:0 14px 26px -10px rgba(124,58,237,.85)!important; }
  .stButton>button{ position:relative; overflow:hidden; border-radius:12px!important; }
  .stButton>button:after{ content:""; position:absolute; top:0; left:-120%; width:60%; height:100%;
    background:linear-gradient(120deg,transparent,rgba(255,255,255,.35),transparent); transform:skewX(-20deg); }
  .stButton>button:hover:after{ animation:btnShine .8s ease; }
  @keyframes btnShine{ to{ left:140%; } }

  /* Inputs */
  .stTextInput input, .stTextArea textarea, .stSelectbox div[data-baseweb="select"]>div, .stNumberInput input, .stDateInput input{
    border-radius:10px!important; background:#15151f!important; color:#e7e7f0!important; border-color:#2a2a40!important; }
  .stTextInput input::placeholder, .stTextArea textarea::placeholder{ color:#6f6f86!important; }
  .stTextInput input:focus, .stSelectbox div[data-baseweb="select"]:focus-within{
    box-shadow:0 0 0 3px rgba(124,92,255,.35) !important; border-color:#7c5cff !important; }
  .stTabs [data-baseweb="tab-list"]{ gap:6px; }
  .stTabs [data-baseweb="tab"]{ border-radius:10px 10px 0 0; font-weight:600; }

  .codechip{ display:inline-block; background:linear-gradient(135deg,#1a1730,#241a40); border:1px solid #4b2aad;
    color:#d8ccff; font-family:monospace; font-weight:800; letter-spacing:1px; padding:8px 18px; border-radius:10px; font-size:18px; }
  .qprev{ background:#0e0e18; border:1px solid #23233a; border-left:3px solid #7c5cff; border-radius:0 10px 10px 0;
    padding:8px 12px; margin:6px 0; font-size:13px; color:#cdd6f4; }
  .pill{ display:inline-block; padding:3px 10px; border-radius:99px; font-size:11px; font-weight:700; margin:2px 4px 2px 0; }
  .pill-on{ background:#13311f; color:#7ee0a8; border:1px solid #2a7d52; }
  .pill-off{ background:#2a1620; color:#ff9b9b; border:1px solid #7a1f1f; }
</style>""", unsafe_allow_html=True)

# ── Storage (same folder as the candidate app) ────────────────────────────────
DATA_DIR = Path("interview_data"); DATA_DIR.mkdir(exist_ok=True)
USERS_FILE       = DATA_DIR / "users.json"
SESSIONS_FILE    = DATA_DIR / "sessions.json"
LEADERBOARD_FILE = DATA_DIR / "leaderboard.json"
CUSTOM_FILE      = DATA_DIR / "custom_interviews.json"

def load_json(p, d):
    try:
        if p.exists(): return json.loads(p.read_text())
    except: pass
    return d
def save_json(p, data):
    try: p.write_text(json.dumps(data, indent=2, default=str))
    except: pass

# ── Password hashing / accounts (shared users.json) ───────────────────────────
def hash_password(password, salt=None):
    if salt is None: salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"
def verify_password(password, stored):
    try:
        salt, _ = stored.split("$", 1)
        return hmac.compare_digest(stored, hash_password(password, salt))
    except: return False
def get_users(): return load_json(USERS_FILE, {})
def create_user(email, password, name, role, company="", emp_id=""):
    users = get_users(); email = email.lower().strip()
    if email in users: return False, "An account with this email already exists."
    users[email] = {"name":name.strip(),"email":email,"password":hash_password(password),
                    "role":role,"company":company.strip(),"emp_id":emp_id.strip(),
                    "created":datetime.now().strftime("%Y-%m-%d %H:%M")}
    save_json(USERS_FILE, users); return True, "Account created successfully."
def authenticate(email, password):
    users = get_users(); email = email.lower().strip()
    if email not in users: return False, None, "No account found with this email."
    if not verify_password(password, users[email]["password"]): return False, None, "Incorrect password."
    return True, users[email], "Login successful."

# ── Custom interview storage ──────────────────────────────────────────────────
def _new_interview_code(company):
    base="".join(ch for ch in (company or "ORG").upper() if ch.isalnum())[:4] or "ORG"
    return f"{base}-{secrets.token_hex(3).upper()}"
def save_custom_interview(cfg):
    data=load_json(CUSTOM_FILE, {}); data[cfg["code"]]=cfg; save_json(CUSTOM_FILE, data)
def get_custom_interview(code):
    if not code: return None
    return load_json(CUSTOM_FILE, {}).get(code.strip().upper())
def list_custom_interviews(recruiter_email):
    data=load_json(CUSTOM_FILE, {})
    return sorted([c for c in data.values() if c.get("recruiter","")==recruiter_email],
                  key=lambda c: c.get("created",""), reverse=True)
def delete_custom_interview(code):
    data=load_json(CUSTOM_FILE, {})
    if code in data: del data[code]; save_json(CUSTOM_FILE, data)

# ── Shared constants ──────────────────────────────────────────────────────────
DIFFICULTIES = {"🎓 Intern":"intern","🟢 Entry":"entry","🔵 Junior":"junior","🟡 Mid-Level":"mid","🟠 Senior":"senior","🔴 Staff/Lead":"staff","⚫ Principal":"principal"}
LANGUAGES    = {"🇺🇸 English":"English","🇪🇸 Spanish":"Spanish","🇮🇳 Hindi":"Hindi","🇫🇷 French":"French","🇩🇪 German":"German","🇧🇷 Portuguese":"Portuguese","🇨🇳 Chinese":"Chinese","🇯🇵 Japanese":"Japanese"}

# Advanced lockdown / proctoring technologies a recruiter can switch on per interview.
PROCTOR_OPTIONS = {
    "camera":      ("📷 Webcam proctoring + recording", "Live camera self-view with a recording indicator throughout the exam.", True),
    "face_required":("🤳 Require face detection before start", "The Start button unlocks only after the candidate's face is detected and a photo is auto-captured.", True),
    "gaze":        ("👁️ Gaze / head-pose monitoring", "After 2 head turns the candidate gets one red warning; a 4th head turn ends the exam as failed.", True),
    "voice":       ("🗣️ Human-voice detection", "Detects talking (human speech 300–3400 Hz, fluctuating). Steady machine noise (fan/AC) is ignored.", True),
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
def default_proctoring(): return {k:dflt for k,(lbl,hlp,dflt) in PROCTOR_OPTIONS.items()}

# Quick lockdown presets for the Create Interview tab
PRESETS = {
    "⚖️ Standard (recommended)": default_proctoring(),
    "🔒 Strict (everything on)": {k:True for k in PROCTOR_OPTIONS},
    "🌤️ Relaxed (camera + gaze only)": {k:(k in ("camera","face_required","gaze")) for k in PROCTOR_OPTIONS},
    "📝 Open book (no lockdown)": {k:False for k in PROCTOR_OPTIONS},
}

def _num(x):
    try: return float(x)
    except: return 0.0
def se(s):
    s=_num(s)
    return "🏆" if s>=9 else("✅" if s>=7 else("👍" if s>=5 else "📚"))

def hero(title, subtitle, pills=None):
    pill_html="".join(f'<span class="hpill">{p}</span>' for p in (pills or []))
    pr=f'<div class="pillrow">{pill_html}</div>' if pill_html else ""
    st.markdown('<div class="accent-bar"></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="hero"><h1>{title}</h1><p>{subtitle}</p>{pr}</div>', unsafe_allow_html=True)

def lockdown_pills(opts):
    chips=""
    for k,(lbl,_,_) in PROCTOR_OPTIONS.items():
        on=bool((opts or {}).get(k))
        chips+=f'<span class="pill {"pill-on" if on else "pill-off"}">{lbl.split(" ",1)[0]} {lbl.split(" ",1)[1] if " " in lbl else lbl}</span>'
    return f'<div style="margin:6px 0">{chips}</div>'

# ── Session state ─────────────────────────────────────────────────────────────
for k,v in {"logged_in":False,"user_email":"","user_name":"","just_created":"","clone_into":None}.items():
    if k not in st.session_state: st.session_state[k]=v

# ══════════════════════════════════════════════════════════════════════════════
#  RECRUITER LOGIN / SIGN-UP
# ══════════════════════════════════════════════════════════════════════════════
if not st.session_state["logged_in"]:
    hero("🏢 Recruiter Dashboard", "Sign in to review candidate results and design custom proctored interviews.",
         ["📊 Analytics","🛠️ Create interviews","📂 Import interview files","🔒 Lockdown options"])
    tab_login, tab_signup = st.tabs(["🔑 Log In", "📝 Create Recruiter Account"])
    with tab_login:
        li_email = st.text_input("Email", key="li_email", placeholder="you@company.com")
        li_pass  = st.text_input("Password", key="li_pass", type="password")
        if st.button("Log In", type="primary", use_container_width=True):
            ok, user, msg = authenticate(li_email, li_pass)
            if not ok: st.error(msg)
            elif user.get("role")!="recruiter":
                st.error("This is a recruiter-only dashboard. That account is a candidate account — use the candidate app (coach.py).")
            else:
                st.session_state.update({"logged_in":True,"user_email":user["email"],"user_name":user["name"]})
                st.rerun()
    with tab_signup:
        su_name = st.text_input("Full Name", key="su_name")
        su_email= st.text_input("Work Email", key="su_email", placeholder="you@company.com")
        su_company = st.text_input("Company Name", key="su_company", placeholder="e.g. ACME Corp")
        su_empid   = st.text_input("Employee ID", key="su_empid", placeholder="e.g. ACME-48213")
        su_pass = st.text_input("Password", key="su_pass", type="password", help="Min 6 characters")
        if st.button("Create Recruiter Account", type="primary", use_container_width=True):
            if not su_name.strip(): st.error("Enter your name.")
            elif not su_email.strip() or "@" not in su_email: st.error("Enter a valid email.")
            elif not su_company.strip(): st.error("Enter your company name.")
            elif len(su_pass) < 6: st.error("Password must be at least 6 characters.")
            else:
                ok, msg = create_user(su_email, su_pass, su_name, "recruiter", su_company, su_empid)
                st.success(msg + " You can now log in.") if ok else st.error(msg)
    st.stop()

# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════
with st.sidebar:
    st.markdown(f"**🏢 {st.session_state['user_name']}**")
    st.caption(f"Recruiter · {st.session_state['user_email']}")
    _my_company = get_users().get(st.session_state["user_email"],{}).get("company","")
    if _my_company: st.caption(f"🏷️ {_my_company}")
    st.divider()
    _mine_count=len(list_custom_interviews(st.session_state["user_email"]))
    _sess_count=len(load_json(SESSIONS_FILE,[]))
    st.markdown(f"**📋 My interviews:** {_mine_count}")
    st.markdown(f"**🗂️ Sessions on file:** {_sess_count}")
    st.divider()
    if st.button("🚪 Log Out", use_container_width=True):
        for k in ["logged_in","user_email","user_name","just_created","clone_into"]:
            st.session_state[k]= "" if k!="logged_in" else False
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
#  DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════
hero("🏢 Recruiter Dashboard",
     "All candidate results · build, import & share custom proctored interviews — a private workspace only recruiters can see",
     ["📊 Live analytics","🔎 Filter & rank","⬇️ CSV export","🏆 Leaderboards","📂 Import / export interviews"])
tab_analytics, tab_create, tab_mine = st.tabs(["📊 Analytics","🛠️ Create Interview","📋 My Interviews"])

# ── helper: build a download payload for one interview ────────────────────────
def _interview_bytes(cfg):
    return json.dumps(cfg, indent=2, default=str).encode("utf-8")

# ══════════════════════════════════════════════════════════════════════════════
#  TAB: CREATE INTERVIEW  (now with import, clone, presets, and more settings)
# ══════════════════════════════════════════════════════════════════════════════
with tab_create:
    st.subheader("🛠️ Design or import a custom proctored interview")

    # ── 📂 Directly add existing created interview files ──────────────────────
    with st.expander("📂 Import existing interview file(s)  —  add interviews you already created", expanded=False):
        st.caption("Upload an interview **.json** file (a single interview, or a whole custom_interviews.json export). "
                   "These get added straight into your workspace and become usable codes immediately.")
        up = st.file_uploader("Interview .json file", type=["json"], key="imp_file")
        claim = st.checkbox("Assign imported interviews to my account (show under My Interviews; my company on the certificate)", value=True)
        regen = st.checkbox("Generate fresh codes (avoid overwriting existing codes)", value=False)
        if up is not None and st.button("➕ Add these interviews", use_container_width=True, type="primary"):
            try:
                raw = json.loads(up.getvalue().decode("utf-8"))
            except Exception as e:
                st.error(f"That file isn't valid JSON: {e}"); raw=None
            if raw is not None:
                # Accept: single cfg (dict with 'code'), a dict of code->cfg, or a list of cfg
                incoming=[]
                if isinstance(raw, dict) and "code" in raw: incoming=[raw]
                elif isinstance(raw, dict): incoming=list(raw.values())
                elif isinstance(raw, list): incoming=raw
                added=0
                for cfg in incoming:
                    if not isinstance(cfg, dict): continue
                    cfg=dict(cfg)
                    if regen or not cfg.get("code"):
                        cfg["code"]=_new_interview_code(cfg.get("company","ORG"))
                    cfg.setdefault("created", datetime.now().strftime("%Y-%m-%d %H:%M"))
                    cfg.setdefault("proctoring", default_proctoring())
                    cfg.setdefault("num_questions", len(cfg.get("custom_questions") or []) or 8)
                    cfg.setdefault("minutes", 5)
                    if claim:
                        cfg["recruiter"]=st.session_state["user_email"]
                        if not cfg.get("company"): cfg["company"]=_my_company
                    save_custom_interview(cfg); added+=1
                if added: st.success(f"✅ Imported {added} interview(s). Open the **My Interviews** tab to see them.")
                else: st.warning("No valid interviews were found in that file.")

    # ── 📑 Clone an existing interview into the form ──────────────────────────
    _mine_for_clone = list_custom_interviews(st.session_state["user_email"])
    if _mine_for_clone:
        with st.expander("📑 Start from one of your existing interviews (clone & edit)", expanded=False):
            opts_map={f"{c['code']} · {c.get('title','')}":c for c in _mine_for_clone}
            pick=st.selectbox("Pick an interview to clone", list(opts_map.keys()))
            if st.button("📥 Load it into the form below", use_container_width=True):
                src=opts_map[pick]
                st.session_state["clone_into"]=src
                # seed widget keys from the source
                inv={v:k for k,v in DIFFICULTIES.items()}; invl={v:k for k,v in LANGUAGES.items()}
                st.session_state["ci_company"]=src.get("company","")
                st.session_state["ci_title"]=src.get("title","")+" (copy)"
                st.session_state["ci_role"]=src.get("role","")
                st.session_state["ci_diff"]=inv.get(src.get("difficulty"), list(DIFFICULTIES.keys())[3])
                st.session_state["ci_lang"]=invl.get(src.get("language"), list(LANGUAGES.keys())[0])
                st.session_state["ci_num"]=int(src.get("num_questions",8))
                st.session_state["ci_min"]=int(src.get("minutes",5))
                if src.get("q_type")=="custom" and src.get("custom_questions"):
                    st.session_state["ci_mode"]="✍️ Write my own"
                    st.session_state["ci_qtext"]="\n".join(f"[{t}] {q}" for t,q in src["custom_questions"])
                for k in PROCTOR_OPTIONS:
                    st.session_state[f"ci_opt_{k}"]=bool((src.get("proctoring") or {}).get(k))
                st.rerun()

    st.caption("Build your company's own interview, choose exactly which lockdown technologies are enforced, then share the code. The company name appears on the candidate's certificate.")
    _company_default = st.session_state.get("ci_company", _my_company)
    ci1, ci2 = st.columns(2)
    with ci1:
        ci_company = st.text_input("🏢 Company name (shown on the certificate)", value=_company_default, key="ci_company")
        ci_title   = st.text_input("Interview title", placeholder="e.g. Backend Engineer Screen", key="ci_title")
        ci_role    = st.text_input("Role", placeholder="e.g. Backend Engineer", key="ci_role")
        ci_diff_label = st.selectbox("Experience level", list(DIFFICULTIES.keys()), key="ci_diff")
        ci_lang_label = st.selectbox("Language", list(LANGUAGES.keys()), key="ci_lang")
    with ci2:
        ci_mode  = st.radio("Questions", ["🤖 AI-generated","✍️ Write my own"], key="ci_mode")
        _qmap_ci = {"🔧 Technical + Coding":"technical_coding","💬 Behavioral Only":"behavioral","🔀 Full Mix":"mixed"}
        ci_focus_label = st.selectbox("Focus (AI-generated mode)", list(_qmap_ci.keys()), key="ci_focus", disabled=ci_mode.startswith("✍️"))
        ci_num   = st.slider("Number of questions", 3, 50, 8, key="ci_num")
        ci_min   = st.slider("Minutes per question", 1, 15, 5, key="ci_min")

    ci_qtext=""
    if ci_mode.startswith("✍️"):
        st.markdown("**Write your questions** — one per line. Optionally start a line with a tag: `[CODING]`, `[DESIGN]`, `[CONCEPT]`, `[BEHAVIORAL]`.")
        ci_qtext = st.text_area("Your questions", height=170, key="ci_qtext",
            placeholder="[CODING] Reverse a singly linked list and return the new head.\n[BEHAVIORAL] Tell me about a time you resolved a conflict.")
        # live preview
        _prev=[]
        for _ln in ci_qtext.splitlines():
            _ln=_ln.strip()
            if not _ln: continue
            _m=re.match(r"^\[(CODING|DESIGN|CONCEPT|BEHAVIORAL)\]\s*(.*)", _ln, re.I)
            tag=_m.group(1).upper() if _m else "CONCEPT"; body=_m.group(2).strip() if _m else _ln
            _prev.append((tag,body))
        if _prev:
            st.caption(f"Preview — {len(_prev)} question(s):")
            st.markdown("".join(f'<div class="qprev"><b>{t}</b> · {q}</div>' for t,q in _prev[:12]), unsafe_allow_html=True)
            if len(_prev)>12: st.caption(f"… and {len(_prev)-12} more")

    # ── More interview settings ───────────────────────────────────────────────
    with st.expander("⚙️ More settings (passing score, attempts, expiry, instructions)", expanded=False):
        ms1,ms2,ms3=st.columns(3)
        with ms1:
            ci_pass = st.slider("✅ Passing score (out of 10)", 0, 10, 6, key="ci_pass")
            ci_attempts = st.number_input("🔁 Attempts allowed", 1, 10, 1, key="ci_attempts")
        with ms2:
            ci_shuffle = st.checkbox("🔀 Shuffle question order", value=False, key="ci_shuffle")
            ci_allow_skip = st.checkbox("⏭️ Allow skipping questions", value=True, key="ci_allow_skip")
        with ms3:
            ci_set_expiry = st.checkbox("📅 Set an expiry date", value=False, key="ci_set_expiry")
            ci_expiry = st.date_input("Expires on", value=date.today(), key="ci_expiry", disabled=not ci_set_expiry)
        ci_instructions = st.text_area("📣 Instructions shown to the candidate (optional)", height=80, key="ci_instructions",
            placeholder="e.g. Find a quiet, well-lit room. Keep your face to the camera. You have N minutes per question.")
        ci_tags = st.text_input("🏷️ Internal tags / notes (only you see these)", key="ci_tags", placeholder="e.g. campus-drive-2026, backend, batch-A")
        st.caption("These are saved onto the interview file. The candidate app applies questions, timing and lockdown today; "
                   "the rest are recorded with the interview for your records, the certificate, and future use.")

    # ── 🏢 Company options (branding, access, visibility) ─────────────────────
    with st.expander("🏢 Company options — branding, access control & result visibility", expanded=False):
        co1,co2=st.columns(2)
        with co1:
            st.markdown("**🎨 Branding**")
            ci_brand_color = st.color_picker("Certificate / brand colour", value="#4B2AAD", key="ci_brand_color")
            ci_logo = st.text_input("Logo initials (1–3 letters on the badge)", value=(ci_company[:2].upper() if ci_company else ""), key="ci_logo", max_chars=3)
            ci_issuer_on = st.checkbox("Show our company as the certificate issuer", value=True, key="ci_issuer_on")
            ci_pass_msg = st.text_input("Custom 'passed' message on the certificate", key="ci_pass_msg", placeholder="e.g. Cleared the ACME screening round")
        with co2:
            st.markdown("**🔐 Access control**")
            ci_pin = st.text_input("Access PIN candidates must enter (optional)", key="ci_pin", placeholder="leave blank for open access", max_chars=12)
            ci_allowlist = st.text_area("Allow only these candidate emails (one per line, optional)", height=70, key="ci_allowlist",
                placeholder="alice@example.com\nbob@example.com")
            ci_max_cand = st.number_input("Max candidates allowed (0 = unlimited)", 0, 100000, 0, key="ci_max_cand")
            ci_show_score = st.checkbox("Show the score to the candidate at the end", value=True, key="ci_show_score")
        st.markdown("**⚖️ Score weighting** (how the three sections combine, for your records)")
        w1,w2,w3=st.columns(3)
        with w1: ci_w_code = st.slider("Coding weight", 0, 100, 40, key="ci_w_code")
        with w2: ci_w_tech = st.slider("Technical weight", 0, 100, 35, key="ci_w_tech")
        with w3: ci_w_behav = st.slider("Behavioural weight", 0, 100, 25, key="ci_w_behav")
        _wsum=ci_w_code+ci_w_tech+ci_w_behav
        if _wsum!=100: st.caption(f"⚠️ Weights add up to {_wsum}% (they don't need to total 100, but it's tidy if they do).")
        st.caption("Branding (colour, logo, issuer, message) and access rules are saved on the interview file. "
                   "The candidate app applies questions, timing and lockdown now; branding/access are recorded for the certificate and future enforcement.")

    # ── Lockdown presets + checkboxes ─────────────────────────────────────────
    st.markdown("##### 🔒 Lockdown technologies to enforce")
    pcol1,pcol2=st.columns([2,1])
    with pcol1: preset=st.selectbox("Quick preset", list(PRESETS.keys()), key="ci_preset")
    with pcol2:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        if st.button("Apply preset", use_container_width=True):
            for _k,_v in PRESETS[preset].items(): st.session_state[f"ci_opt_{_k}"]=_v
            st.rerun()
    st.caption("Tick every protection you want active during this interview. Candidates taking this code get exactly these.")
    _opt_cols = st.columns(2); ci_opts={}
    for _i,(_k,(_lbl,_hlp,_dflt)) in enumerate(PROCTOR_OPTIONS.items()):
        with _opt_cols[_i%2]:
            ci_opts[_k]=st.checkbox(_lbl, value=_dflt, help=_hlp, key=f"ci_opt_{_k}")
    _on_count=sum(1 for v in ci_opts.values() if v)
    st.caption(f"🔐 {_on_count}/{len(PROCTOR_OPTIONS)} protections enabled.")

    st.markdown("")
    if st.button("✅ Create interview & generate code", type="primary", use_container_width=True):
        if not ci_company.strip() or not ci_title.strip() or not ci_role.strip():
            st.error("Company, title and role are required.")
        else:
            _custom_qs=[]
            _ok=True
            if ci_mode.startswith("✍️"):
                for _ln in ci_qtext.splitlines():
                    _ln=_ln.strip()
                    if not _ln: continue
                    _m=re.match(r"^\[(CODING|DESIGN|CONCEPT|BEHAVIORAL)\]\s*(.*)", _ln, re.I)
                    if _m: _custom_qs.append([_m.group(1).upper(), _m.group(2).strip()])
                    else: _custom_qs.append(["CONCEPT", _ln])
                if not _custom_qs:
                    st.error("Add at least one question, or switch to AI-generated."); _ok=False
            if _ok:
                _code=_new_interview_code(ci_company)
                _cfg={"code":_code,"title":ci_title.strip(),"company":ci_company.strip(),
                      "recruiter":st.session_state["user_email"],"role":ci_role.strip(),
                      "difficulty":DIFFICULTIES[ci_diff_label],"difficulty_label":ci_diff_label,
                      "language":LANGUAGES[ci_lang_label],
                      "q_type":("custom" if ci_mode.startswith("✍️") else _qmap_ci[ci_focus_label]),
                      "custom_questions":_custom_qs,"num_questions":ci_num,"minutes":ci_min,
                      "proctoring":ci_opts,
                      # extra settings
                      "passing_score":st.session_state.get("ci_pass",6),
                      "attempts":int(st.session_state.get("ci_attempts",1)),
                      "shuffle":bool(st.session_state.get("ci_shuffle",False)),
                      "allow_skip":bool(st.session_state.get("ci_allow_skip",True)),
                      "expiry":(str(st.session_state.get("ci_expiry")) if st.session_state.get("ci_set_expiry") else ""),
                      "instructions":st.session_state.get("ci_instructions","").strip(),
                      "tags":st.session_state.get("ci_tags","").strip(),
                      # company options
                      "brand_color":st.session_state.get("ci_brand_color","#4B2AAD"),
                      "logo":st.session_state.get("ci_logo","").strip(),
                      "issuer_on":bool(st.session_state.get("ci_issuer_on",True)),
                      "pass_message":st.session_state.get("ci_pass_msg","").strip(),
                      "access_pin":st.session_state.get("ci_pin","").strip(),
                      "allowlist":[e.strip().lower() for e in st.session_state.get("ci_allowlist","").splitlines() if e.strip()],
                      "max_candidates":int(st.session_state.get("ci_max_cand",0)),
                      "show_score":bool(st.session_state.get("ci_show_score",True)),
                      "weights":{"coding":st.session_state.get("ci_w_code",40),
                                 "technical":st.session_state.get("ci_w_tech",35),
                                 "behavioral":st.session_state.get("ci_w_behav",25)},
                      "created":datetime.now().strftime("%Y-%m-%d %H:%M")}
                save_custom_interview(_cfg)
                st.session_state["just_created"]=_code

    if st.session_state.get("just_created"):
        _c=st.session_state["just_created"]
        _cfg=get_custom_interview(_c)
        st.success("✅ Interview created! Share this code with candidates:")
        st.markdown(f'<div class="codechip">{_c}</div>', unsafe_allow_html=True)
        st.caption("Candidates log in, choose “I have an interview code”, enter this code, and the interview runs with your settings + lockdown options. The certificate shows your company name.")
        if _cfg:
            st.download_button("⬇️ Download this interview file (.json)", data=_interview_bytes(_cfg),
                file_name=f"interview_{_c}.json", mime="application/json", use_container_width=True)
            st.caption("Keep this file as a backup, or share it so another recruiter can import it under 📂 above.")

# ══════════════════════════════════════════════════════════════════════════════
#  TAB: MY INTERVIEWS  (download / duplicate / delete)
# ══════════════════════════════════════════════════════════════════════════════
with tab_mine:
    st.subheader("📋 My custom interviews")
    _mine=list_custom_interviews(st.session_state["user_email"])

    # Export ALL of my interviews as one file
    if _mine:
        _all={c["code"]:c for c in _mine}
        st.download_button("⬇️ Export ALL my interviews (.json)",
            data=json.dumps(_all, indent=2, default=str).encode("utf-8"),
            file_name=f"my_interviews_{datetime.now().strftime('%Y%m%d')}.json",
            mime="application/json", use_container_width=True)
        _search=st.text_input("🔎 Search by code, title, role or tag", placeholder="type to filter…")
        if _search:
            s=_search.lower()
            _mine=[c for c in _mine if s in (c.get("code","")+c.get("title","")+c.get("role","")+c.get("tags","")).lower()]

    if not _mine:
        st.info("No custom interviews yet. Create one in the 🛠️ Create Interview tab, or import a file there.")
    else:
        for _c in _mine:
            _title=_c.get('title',''); _exp=_c.get('expiry','')
            _badge=" · ⏳ expires "+_exp if _exp else ""
            with st.expander(f"🔖 {_c['code']} · {_title} · {_c.get('role','')}{_badge}"):
                st.markdown(f"**Company:** {_c.get('company')}  ")
                st.markdown(f"**Level:** {_c.get('difficulty_label','')} · **{_c.get('num_questions')} questions** · {_c.get('minutes')} min each · 🌐 {_c.get('language')}")
                _q=_c.get("q_type"); st.markdown(f"**Questions:** {'your custom set' if _q=='custom' else 'AI-generated ('+str(_q)+')'}")
                # extra settings, if present
                _extra=[]
                if _c.get("passing_score") is not None: _extra.append(f"pass ≥ {_c.get('passing_score')}/10")
                if _c.get("attempts"): _extra.append(f"{_c.get('attempts')} attempt(s)")
                if _c.get("shuffle"): _extra.append("shuffled")
                if _c.get("allow_skip") is False: _extra.append("no skipping")
                if _c.get("tags"): _extra.append("🏷️ "+_c.get("tags"))
                if _c.get("access_pin"): _extra.append("🔐 PIN-protected")
                if _c.get("allowlist"): _extra.append(f"✉️ {len(_c.get('allowlist'))} allow-listed")
                if _c.get("max_candidates"): _extra.append(f"max {_c.get('max_candidates')}")
                if _c.get("show_score") is False: _extra.append("score hidden")
                if _extra: st.markdown("**Settings:** "+" · ".join(str(x) for x in _extra))
                if _c.get("instructions"): st.info("📣 "+_c.get("instructions"))
                st.markdown("**🔒 Lockdown enforced:**", unsafe_allow_html=True)
                st.markdown(lockdown_pills(_c.get("proctoring")), unsafe_allow_html=True)
                st.markdown("**Share code:**")
                st.markdown(f'<div class="codechip">{_c["code"]}</div>', unsafe_allow_html=True)

                b1,b2,b3=st.columns(3)
                with b1:
                    st.download_button("⬇️ Download (.json)", data=_interview_bytes(_c),
                        file_name=f"interview_{_c['code']}.json", mime="application/json",
                        key=f"dl_{_c['code']}", use_container_width=True)
                with b2:
                    if st.button("📑 Duplicate", key=f"dup_{_c['code']}", use_container_width=True):
                        _copy=dict(_c); _copy["code"]=_new_interview_code(_c.get("company","ORG"))
                        _copy["title"]=(_c.get("title","")+" (copy)").strip()
                        _copy["created"]=datetime.now().strftime("%Y-%m-%d %H:%M")
                        save_custom_interview(_copy); st.rerun()
                with b3:
                    if st.button("🗑️ Delete", key=f"del_{_c['code']}", use_container_width=True):
                        delete_custom_interview(_c["code"]); st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
#  TAB: ANALYTICS
# ══════════════════════════════════════════════════════════════════════════════
with tab_analytics:
    sessions=load_json(SESSIONS_FILE,[])
    lb_data =load_json(LEADERBOARD_FILE,{})
    if not sessions:
        st.info("No interview sessions yet. Candidates' results will appear here once they complete interviews.")
    else:
        total=len(sessions)
        avg_all=round(sum(_num(s.get("avg_score",0)) for s in sessions)/total,1)
        flagged=sum(1 for s in sessions if _num(s.get("violations",0))>0 or "FAIL" in str(s.get("status","")).upper())
        passed =sum(1 for s in sessions if _num(s.get("avg_score",0))>=6)
        m1,m2,m3,m4=st.columns(4)
        m1.metric("Total",total); m2.metric("Avg Score",f"{avg_all}/10")
        m3.metric("Passed",passed); m4.metric("Flagged / failed",flagged)
        st.divider()

        # ── Analytics charts ───────────────────────────────────────────────────
        st.subheader("📊 Analytics")
        try:
            import pandas as pd
            ch1,ch2=st.columns(2)
            with ch1:
                st.caption("Score distribution")
                buckets={"0-2":0,"2-4":0,"4-6":0,"6-8":0,"8-10":0}
                for s in sessions:
                    v=_num(s.get("avg_score",0))
                    if v<2:buckets["0-2"]+=1
                    elif v<4:buckets["2-4"]+=1
                    elif v<6:buckets["4-6"]+=1
                    elif v<8:buckets["6-8"]+=1
                    else:buckets["8-10"]+=1
                st.bar_chart(pd.DataFrame({"candidates":list(buckets.values())}, index=list(buckets.keys())))
            with ch2:
                st.caption("Average score by company")
                from collections import defaultdict
                agg=defaultdict(list)
                for s in sessions:
                    agg[s.get("target_company") or "General"].append(_num(s.get("avg_score",0)))
                comp_avg={k:round(sum(v)/len(v),1) for k,v in agg.items()}
                comp_avg=dict(sorted(comp_avg.items(), key=lambda x:x[1], reverse=True)[:8])
                st.bar_chart(pd.DataFrame({"avg score":list(comp_avg.values())}, index=list(comp_avg.keys())))
            st.caption("Interviews over time")
            from collections import Counter
            by_day=Counter(s.get("date","")[:10] for s in sessions if s.get("date"))
            if by_day:
                days=sorted(by_day.keys())
                st.line_chart(pd.DataFrame({"interviews":[by_day[d] for d in days]}, index=days))
        except ImportError:
            st.info("Install pandas for charts: pip install pandas")
        st.divider()

        fc1,fc2,fc3=st.columns(3)
        with fc1: role_f=st.selectbox("Role",["All"]+sorted(set(s.get("role","") for s in sessions)))
        with fc2: company_f=st.selectbox("Company",["All"]+sorted(set(s.get("target_company","") for s in sessions if s.get("target_company",""))))
        with fc3: score_f=st.selectbox("Score",["All","Excellent (8+)","Good (6-8)","Needs work (<6)","Failed / flagged"])
        filtered=sessions[:]
        if role_f!="All": filtered=[s for s in filtered if s.get("role","")==role_f]
        if company_f!="All": filtered=[s for s in filtered if s.get("target_company","")==company_f]
        if score_f=="Excellent (8+)": filtered=[s for s in filtered if _num(s.get("avg_score",0))>=8]
        elif score_f=="Good (6-8)": filtered=[s for s in filtered if 6<=_num(s.get("avg_score",0))<8]
        elif score_f=="Needs work (<6)": filtered=[s for s in filtered if _num(s.get("avg_score",0))<6]
        elif score_f=="Failed / flagged": filtered=[s for s in filtered if _num(s.get("violations",0))>0 or "FAIL" in str(s.get("status","")).upper()]
        st.subheader(f"📋 Candidates ({len(filtered)})")
        for idx,s in enumerate(sorted(filtered,key=lambda x:_num(x.get("avg_score",0)),reverse=True)):
            sc_v=s.get("avg_score",0); viol=int(_num(s.get("violations",0))); co=s.get("target_company",""); match=s.get("resume_match",0)
            flag="⛔" if "FAIL" in str(s.get("status","")).upper() else ("⚠️" if viol>0 else "✅")
            with st.expander(f"{se(sc_v)} **{s.get('candidate_name','?')}** → {co or 'General'} · {s.get('role','')} · {sc_v}/10 {flag}"):
                c1,c2=st.columns(2)
                with c1:
                    st.markdown(f"**Email:** {s.get('candidate_email','—')}")
                    st.markdown(f"**Company:** {co or 'General'}")
                    st.markdown(f"**Date:** {s.get('date','—')}")
                    st.markdown(f"**Level:** {s.get('difficulty_label','—')}")
                with c2:
                    st.markdown(f"**Coding:** {s.get('avg_coding','N/A')}/10")
                    st.markdown(f"**Technical:** {s.get('avg_tech','N/A')}/10")
                    st.markdown(f"**Behavioral:** {s.get('avg_behav','N/A')}/10")
                    st.markdown(f"**Resume Match:** {match}%")
                if s.get("status"): st.warning(f"Status: {s.get('status')} — {s.get('terminated_reason','')}")
                if s.get("resume_summary"): st.info(f"📄 {s.get('resume_summary')}")
        st.divider()
        buf=io.StringIO(); w=csv.writer(buf)
        w.writerow(["Name","Email","Company","Role","Level","Date","Score","Coding","Technical","Behavioral","Violations","ResumeMatch","Status"])
        for s in filtered:
            w.writerow([s.get("candidate_name"),s.get("candidate_email"),s.get("target_company"),s.get("role"),s.get("difficulty_label"),s.get("date"),s.get("avg_score"),s.get("avg_coding"),s.get("avg_tech"),s.get("avg_behav"),s.get("violations"),s.get("resume_match",0),s.get("status","")])
        st.download_button("⬇️ Export CSV",buf.getvalue(),f"candidates_{datetime.now().strftime('%Y%m%d')}.csv","text/csv",use_container_width=True)
        st.divider()
        st.subheader("🏆 Leaderboard")
        lb_role=st.selectbox("Role ",list(lb_data.keys()) if lb_data else ["—"])
        if lb_role in lb_data:
            for i,e in enumerate(lb_data[lb_role]):
                medal=["🥇","🥈","🥉"][i] if i<3 else f"#{i+1}"
                st.markdown(f"{medal} **{e['name']}** — {e['score']}/10 — {e.get('date','')}")

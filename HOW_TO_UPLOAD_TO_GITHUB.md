# 🚀 How to put this project on GitHub

A short, beginner-friendly walkthrough. Pick **Option A** (no command line) or **Option B** (git).

---

## Before you start

1. Open `LICENSE` and replace `Your Name` with your actual name.
2. Open `README.md` and replace `<your-username>` in the clone URL with your GitHub username.
3. **Do not upload** the `interview_data/` folder or `.streamlit/secrets.toml` — they hold accounts and API keys. The included `.gitignore` already excludes them.

---

## Option A — Upload through the website (easiest)

1. Go to **github.com** → click **+** (top right) → **New repository**.
2. **Repository name:** `AI-Interview-Coach`
3. **Description:** paste the text from `docs/GITHUB_ABOUT.txt`.
4. Choose **Public**. Do **not** tick "Add a README" (you already have one).
5. Click **Create repository**.
6. On the new page click **uploading an existing file**.
7. Drag in everything from this folder: `coach.py`, `recruiter_dashboard.py`, `requirements.txt`, `README.md`, `LICENSE`, `.gitignore`, and the `docs` and `.streamlit` folders.
8. Click **Commit changes**.

Your README with the flowcharts will render automatically on the repo home page.

---

## Option B — Using git (command line)

Install [Git](https://git-scm.com/downloads) first, then run these from inside this folder:

```bash
git init
git add .
git commit -m "Initial commit — AI Interview Coach"
git branch -M main
git remote add origin https://github.com/<your-username>/AI-Interview-Coach.git
git push -u origin main
```

If you're asked for a password, use a **Personal Access Token** (GitHub → Settings → Developer settings → Personal access tokens), not your account password.

---

## Finish the repo page (2 minutes, big impact)

1. On your repo page, click the **⚙️ gear** next to **About** (top right).
2. Paste the **description** from `docs/GITHUB_ABOUT.txt`.
3. Add the **topics/tags** listed in that same file (`python`, `streamlit`, `llm`, `proctoring`…).
4. If you deploy the app, paste the live URL into the **Website** field.

---

## Optional — free live demo (recommended)

A live link makes the project far more convincing, and it gives you **HTTPS**, which the camera/proctoring features require.

1. Go to [share.streamlit.io](https://share.streamlit.io) and sign in with GitHub.
2. Click **New app** → pick your `AI-Interview-Coach` repo → main file: `coach.py` → **Deploy**.
3. (Optional) In **Advanced settings → Secrets**, paste your API key so the live demo has full AI grading:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   ```
4. Copy the resulting URL into your README and the repo's **Website** field.

---

## Adding screenshots (makes the README shine)

1. Run the app, take screenshots of the interview screen, the results page, and the recruiter dashboard.
2. Save them into `docs/images/` as `screenshot_interview.png`, `screenshot_results.png`, `screenshot_dashboard.png`.
3. Add them near the top of `README.md`:

```markdown
## 📸 Screenshots

| Interview | Results | Recruiter dashboard |
|---|---|---|
| ![](docs/images/screenshot_interview.png) | ![](docs/images/screenshot_results.png) | ![](docs/images/screenshot_dashboard.png) |
```

---

## For your resume

```
AI Interview Coach — Python, Streamlit, LLM APIs (Claude/GPT/Gemini)
github.com/<your-username>/AI-Interview-Coach

• Built a full-stack AI mock-interview platform with a candidate app and a separate
  recruiter dashboard sharing a common data layer, covering 80+ company-specific
  interview styles across 15 sectors.
• Engineered a 3-stage LLM pipeline (question generation → answer evaluation →
  coaching) with résumé-aware tailoring, a 38-language code editor, and automatic
  offline fallback.
• Implemented browser-based proctoring (MediaPipe gaze tracking, human-voice
  detection, 13 lockdown controls) plus salted-SHA-256 auth, PDF certificate/report
  generation, and recruiter analytics with CSV export.
```

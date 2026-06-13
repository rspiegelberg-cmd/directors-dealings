# One-time GitHub setup + ongoing push workflow

Run all of this in **Windows PowerShell** (not in Claude). Git on Windows is
safe; running git from Claude's Linux sandbox can corrupt files over the FUSE
mount, so we never do that.

---

## STEP 0 — Check the tools are installed (run once)

```powershell
git --version
gh --version
```

- If `git` is missing: install from https://git-scm.com/download/win
- If `gh` (GitHub CLI) is missing: install from https://cli.github.com
  (optional — there's a manual path below if you'd rather not use it)

Set your identity if you've never used git on this machine:

```powershell
git config --global user.name "Rupert Spiegelberg"
git config --global user.email "rspiegelberg@gmail.com"
```

---

## STEP 1 — Create the local repo and stage files

```powershell
cd C:\Dev\DirectorsDealings
git init -b main
git add -A
```

---

## STEP 2 — SAFETY CHECK before the first commit (important — public repo)

These commands confirm secrets, the database, and caches are NOT being
committed. Run them and read the output.

```powershell
# A) These must all report the file is ignored:
git check-ignore .env .data\directors.db .scripts\_mktcap_cache

# B) This must print NOTHING (no secrets/DB/caches staged):
git status --short | Select-String -Pattern "\.env$|directors\.db|_scrape_cache|_mktcap_cache|_price_cache"

# C) This must print NOTHING (no file over ~95 MB staged):
git ls-files | ForEach-Object { if ((Get-Item $_).Length -gt 95MB) { "TOO BIG: $_" } }
```

If (A) lists the three paths as ignored, and (B) and (C) print nothing, you're
safe to continue. If anything looks wrong, stop and tell Claude.

---

## STEP 3 — Commit

```powershell
git commit -m "Initial commit: Directors Dealings dashboard"
```

---

## STEP 4 — Create the GitHub repo and push

**Option A — GitHub CLI (easiest, one command):**

```powershell
gh repo create directors-dealings --public --source . --remote origin --push
```

(The first time, `gh` may ask you to log in: `gh auth login` → GitHub.com →
HTTPS → log in via browser.)

**Option B — Manual (no CLI):**

1. Go to https://github.com/new
2. Repository name: `directors-dealings`
3. Set it to **Public**. Do **not** add a README, .gitignore, or licence
   (we already have them — adding them causes a conflict).
4. Click **Create repository**, then run (replace USERNAME):

```powershell
git remote add origin https://github.com/USERNAME/directors-dealings.git
git push -u origin main
```

---

## STEP 5 — Turn on GitHub Pages

1. On GitHub, open your repo → **Settings** → **Pages**.
2. Under **Build and deployment → Source**, choose **GitHub Actions**.

That's it. The workflow in `.github/workflows/pages.yml` deploys the `outputs/`
folder. Watch progress under the repo's **Actions** tab. When it finishes, your
site is live at:

```
https://USERNAME.github.io/directors-dealings/
```

(If the first deploy doesn't start on its own: Actions tab → "Deploy dashboard
to GitHub Pages" → **Run workflow**.)

---

## ONGOING — save + push every time you rebuild

Whenever you've rebuilt the dashboard locally and want the changes saved to
GitHub and pushed live, just double-click **`push_to_github.bat`**, or run:

```powershell
cd C:\Dev\DirectorsDealings
git add -A
git commit -m "Update dashboard"
git push
```

Your local copy always keeps the full database and the interactive Flask app;
GitHub gets the code and the built `outputs/` site, and the live web page
refreshes automatically a minute or two after each push.

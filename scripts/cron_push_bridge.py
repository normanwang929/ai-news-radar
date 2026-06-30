#!/usr/bin/env python3
"""
Cron runner: bridge trendscan → radar fork data → git push.
Runs daily to keep GitHub Pages fresh.
"""
import json, os, subprocess, sys
from pathlib import Path

REPO = Path(r"C:\Users\norman\Desktop\AI\radar-fork")
DB = Path(r"C:\Users\norman\trendscan\data\raw.db")
BRIDGE = REPO / "scripts" / "bridge_trendscan.py"

def run(cmd, cwd=REPO):
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"❌ {' '.join(cmd)}: {result.stderr[:200]}")
    return result.stdout

# 1. Run bridge
print("📡 1. Running bridge...")
ret = run([sys.executable, str(BRIDGE), "--db", str(DB), "--output-dir", str(REPO/"data"), "--window-hours", "24"])
print(ret)

# 2. Git add + commit
print("📦 2. Committing data...")
run(["git", "add", "data/"])
# Check if anything changed
status = run(["git", "status", "--porcelain"])
if not status.strip():
    print("✅ No changes. Data already current.")
    sys.exit(0)

run(["git", "commit", "-m", "data: auto bridge update"])
print("✅ Committed")

# 3. Push
print("📤 3. Pushing to GitHub...")
push_out = run(["git", "push", "origin", "master"])
print(push_out)
print("✅ Done — radar will update via GitHub Pages shortly.")

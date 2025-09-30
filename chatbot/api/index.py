# /api/index.py
import os, sys

# ensure the parent folder (where main.py lives) is importable
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from main import app as fastapi_app

# Vercel looks for a top-level `app`
app = fastapi_app
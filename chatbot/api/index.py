# /chatbot/api/index.py
import os, sys

# Add parent folder (/chatbot) to import path so we can import main.py
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from main import app  # FastAPI instance named "app"
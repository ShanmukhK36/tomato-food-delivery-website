# /chatbot/api/index.py
import os, sys
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  # add parent dir
from main import app as app  # FastAPI instance named "app"
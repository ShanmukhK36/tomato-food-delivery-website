import os, sys

# Make the parent folder (chatbot/) importable on Vercel
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from main import app
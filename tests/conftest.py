import os
import sys
from pathlib import Path

# Tests must never reach a real tracker or a paid API.
os.environ["NOTION_API_KEY"] = ""
os.environ["NOTION_DATABASE_ID"] = ""
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

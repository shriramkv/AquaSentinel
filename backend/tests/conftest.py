import os
import sys
import tempfile
from pathlib import Path

# Tests run against a throwaway database and never call Gemini
os.environ["DB_PATH"] = str(Path(tempfile.mkdtemp()) / "test.db")
os.environ["DATA_SOURCE"] = "simulator"
os.environ["GEMINI_API_KEY"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

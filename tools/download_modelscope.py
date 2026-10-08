"""Legacy entry; implementation is in tools/maintenance."""
from pathlib import Path
import sys
import runpy
sys.path.insert(0, str(Path(__file__).resolve().parent / "maintenance"))
if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "maintenance" / "download_modelscope.py"), run_name="__main__")

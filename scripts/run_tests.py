"""Run the synthetic, offline regression suite from any working directory."""
import sys,unittest
from pathlib import Path

if __name__=='__main__':
    suite=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parents[1]/'tests'))
    raise SystemExit(0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1)

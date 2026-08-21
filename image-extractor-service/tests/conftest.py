# conftest.py — ensures project root is in sys.path for pytest discovery
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

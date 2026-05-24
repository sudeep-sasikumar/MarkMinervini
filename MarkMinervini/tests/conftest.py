"""
Pytest configuration for the MarkMinervini test suite.

Adds the MarkMinervini package root to sys.path so that module imports
like ``from config import settings`` or ``from risk.position_sizer import ...``
work regardless of the directory from which pytest is invoked.
"""
import sys
import os

# Insert the MarkMinervini root (parent of this tests/ directory) into path.
# This makes ``from config import settings``, ``from risk.position_sizer import ...``
# etc. work exactly as they do when running the app normally.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

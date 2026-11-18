#!/usr/bin/env python3
"""Run the snapgrid test suite (stdlib unittest, no extra deps).

Usage: python run_tests.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"))


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(
        start_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests"),
        pattern="test_*.py",
    )
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

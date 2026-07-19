#!/usr/bin/env python3
"""Ely WebApp — launch with: python webapp.py"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

try:
    from ely.webapp.server import run
    run()
except ImportError as e:
    print(f"Missing dependency: {e}")
    print("Install: pip install fastapi uvicorn")

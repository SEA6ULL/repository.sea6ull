# -*- coding: utf-8 -*-
"""Separate Kodi script entry point for one settled artwork redraw."""
import os
import runpy
import sys

addon_path = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
# Kodi services run from the addon root; reproduce that import path when this
# dedicated entry point runs alongside (or without) the startup service.
if addon_path not in sys.path:
    sys.path.insert(0, addon_path)
if "refresh-only" not in sys.argv[1:]:
    sys.argv.append("refresh-only")
runpy.run_path(os.path.join(addon_path, "service.py"), run_name="__main__")

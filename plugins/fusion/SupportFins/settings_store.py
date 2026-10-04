"""The dialog's settings, remembered between sessions in a JSON file next to the add-in.

A save keeps keys it doesn't know (another command, a newer build) as they were.
"""

import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')

DEFAULTS = {
    'layer_height': 0.2,     # mm: has to match the slicer
    # the website's defaults
    'fin_style': 'auto',     # 'auto', 'prop' or 'stabilize'
    'fin_tines': True,
    'fin_tine_density': 0,   # % (website slider 0..1)
    'fin_coverage': 50,      # % (website slider 0..1)
    'fin_bed_pad': True,
    # Sway braces (web/sway.js): for a TALL part that drifts or wobbles as it
    # grows, rather than for an overhang. Off by default, as on the website.
    'sway_braces': False,
    'sway_grip_from': 0.0,   # mm up: where the brace tines start (0 = the whole height)
    'sway_tine_spacing': 6,  # mm between brace tines
    'sway_depth': 15,        # % of rib height: how far it reaches out at the bed
}


def _read():
    try:
        with open(PATH, encoding='utf-8') as fh:
            saved = json.load(fh)
        return saved if isinstance(saved, dict) else {}
    except (OSError, ValueError):
        return {}


def load():
    s = dict(DEFAULTS)
    s.update({k: v for k, v in _read().items() if k in DEFAULTS})
    return s


def save(s):
    merged = _read()
    merged.update({k: s[k] for k in DEFAULTS if k in s})
    try:
        with open(PATH, 'w', encoding='utf-8') as fh:
            json.dump(merged, fh, indent=2)
    except OSError:
        pass

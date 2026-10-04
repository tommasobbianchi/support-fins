"""Runs the printfins.com fin engine inside Fusion, in a hidden HTML palette.

The fins come from the website's own engine (web/*.js, unmodified), bundled by
plugins/shared/bundle.py into palette/fins_engine.js, and run by Fusion's own
browser. Nothing native ships with the add-in, so the same files serve Windows
and macOS, and a fix on the site reaches Fusion the next time the add-in is built.

(The first builds ran the bundle in mini-racer. On macOS that takes Fusion down:
Fusion ships its own libv8, macOS merges V8's weak symbols across the two copies,
and mini-racer's first context segfaults inside Fusion's V8. A palette loads
nothing native, so there is nothing to clash.)

The palette drives every exchange, because its JavaScript runs on Fusion's main
thread too: Python waiting on it would freeze Fusion.

    palette -> 'ready'    the page loaded and the engine bundle is there
    palette -> 'next'     Python answers with the queued job, or how long to idle
    palette -> 'result'   the engine's answer (or its error) for a job id
    palette -> 'log'      a line for View > Show Text Commands

Python nudges the palette with sendInfoToHTML('wake') so a new job starts at
once; the palette also polls, so a lost nudge only costs a moment.

The encoding helpers and compute_fins() have no adsk imports: compute_fins runs
the same bundle under Node, synchronously, for the tests and for checks off
Fusion. The add-in itself never calls it.
"""

import base64
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
PALETTE_DIR = os.path.join(HERE, 'palette')
BUNDLE = os.path.join(PALETTE_DIR, 'fins_engine.js')
PAGE = os.path.join(PALETTE_DIR, 'engine.html')


def page_url():
    """engine.html as a file:// URL. A bare Windows path (backslashes) reaches the
    palette's browser as file:///C:%5CUsers... and the page never loads."""
    return pathlib.Path(PAGE).resolve().as_uri()

PALETTE_ID = 'SupportFins_Engine'

# What the website starts with (plugins/shared/engine/fins_entry.js ENGINE_DEFAULTS).
DEFAULTS = {
    'mode': 'auto',
    'bedPad': True,
    'tines': True,
    'tineDensity': 0.0,
    'coverage': 0.5,
    'layerHeight': 0.2,
    # Sway braces, off unless the dialog asks. This list is an allow-list -- the
    # job below drops anything not in it -- so a key missing here never reaches
    # the engine however the caller passes it.
    'sway': None,
}

# The palette has this long to load and report before the dialog says it can't
# find the engine. Loading takes well under a second.
READY_TIMEOUT_S = 20.0


class EngineError(Exception):
    """The engine couldn't start or run, worded for the readout."""


# --------------------------------------------------------------------------
# The engine's wire format (plugins/shared/engine/bridge.js computeFinsB64)
# --------------------------------------------------------------------------

def encode_job(soup, options=None):
    """(soup base64, options JSON) for computeFinsB64. soup: flat floats, 9 per
    triangle, the part in PRINT SPACE (mm, z up, the bed at z = 0), wound outward."""
    data = array('d', soup)
    if not len(data) or len(data) % 9:
        raise EngineError('The part has no triangles to fit fins to.')
    if sys.byteorder != 'little':       # the bridge reads little-endian bytes
        data.byteswap()
    opts = dict(DEFAULTS)
    opts.update({k: v for k, v in (options or {}).items() if k in DEFAULTS})
    return base64.b64encode(data.tobytes()).decode('ascii'), json.dumps(opts)


def decode_result(raw):
    """computeFinsB64's JSON -> (fins, stats). fins is an array('d') triangle soup
    in the SAME frame as the soup that went in (the engine seats the part itself;
    its offset is undone here)."""
    out = json.loads(raw) if isinstance(raw, str) else raw
    seated = array('f')
    seated.frombytes(base64.b64decode(out['triangles']))
    if sys.byteorder != 'little':
        seated.byteswap()
    off = out['offset']                 # seated = input + offset
    ox, oy, oz = off['x'], off['y'], off['z']
    fins = array('d', seated)
    for i in range(0, len(fins), 3):
        fins[i] -= ox
        fins[i + 1] -= oy
        fins[i + 2] -= oz
    return fins, out['stats']


# --------------------------------------------------------------------------
# Off Fusion: the same bundle under Node (tests, checks)
# --------------------------------------------------------------------------

_NODE_RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const job = JSON.parse(fs.readFileSync(0, 'utf8'));
vm.runInThisContext(fs.readFileSync(job.bundle, 'utf8'));
process.stdout.write(SupportFinsEngine.computeFinsB64(job.soup, job.options));
"""


def node_available():
    """(True, None) if compute_fins can run here, else (False, why)."""
    if not os.path.isfile(BUNDLE):
        return False, ('The fin engine bundle is missing (palette/fins_engine.js). '
                       'Run plugins/fusion/build.py.')
    if not shutil.which('node'):
        return False, 'Node.js is not on PATH.'
    return True, None


def compute_fins(soup, options=None, timeout=120):
    """Run the website's fin engine on a posed part, synchronously, under Node.
    Same answer the palette gives in Fusion. Returns (fins, stats), see decode_result."""
    ok, why = node_available()
    if not ok:
        raise EngineError(why)
    soup_b64, opts = encode_job(soup, options)
    job = json.dumps({'bundle': BUNDLE, 'soup': soup_b64, 'options': opts})
    run = subprocess.run([shutil.which('node'), '-e', _NODE_RUNNER], input=job.encode('utf-8'),
                         capture_output=True, timeout=timeout)
    if run.returncode:
        err = run.stderr.decode('utf-8', 'replace').strip().splitlines()
        raise EngineError('The fin engine failed on this part (%s).' % (err[-1] if err else
                                                                       'no message'))
    return decode_result(run.stdout.decode('utf-8'))


# --------------------------------------------------------------------------
# In Fusion: the hidden palette
# --------------------------------------------------------------------------

class PaletteEngine:
    """The engine in a hidden Fusion palette. One job at a time: a newer job
    replaces one the palette hasn't picked up yet, and an answer for anything but
    the latest job is dropped, so dragging a slider never queues stale work.

        engine = PaletteEngine(ui)
        engine.start()
        engine.submit(key, soup, options, done)   # done(key, fins, stats, error)
    """

    def __init__(self, ui, log=None):
        self.ui = ui
        self.log = log or (lambda msg: None)
        self.palette = None
        self.handlers = []
        self.ready = False
        self.started_at = None
        self.start_error = None
        self.active = False          # a dialog is open: the palette polls quickly
        self._seq = 0
        self._job = None             # {'id', 'key', 'soup', 'options', 'sent'}
        self._done = None
        self.info = {}

    # ----- lifecycle

    def start(self):
        """Open the hidden palette (again, if a previous run left one)."""
        self.ready = False
        self.start_error = None
        self.started_at = time.monotonic()
        try:
            if not os.path.isfile(BUNDLE):
                raise EngineError('The fin engine bundle is missing (palette/fins_engine.js). '
                                  'Reinstall the add-in, or run plugins/fusion/build.py.')
            old = self.ui.palettes.itemById(PALETTE_ID)
            if old:
                old.deleteMe()
            # isVisible False; showCloseButton, isResizable True; 300 x 200;
            # useNewWebBrowser True: the Qt WebEngine browser, where fusionSendData
            # returns a Promise.
            pal = self.ui.palettes.add(PALETTE_ID, 'Support Fins engine', page_url(),
                                       False, True, True, 300, 200, True)
            handler = _make_html_handler(self)
            pal.incomingFromHTML.add(handler)
            self.handlers.append(handler)
            self.palette = pal
        except EngineError as e:
            self.start_error = str(e)
        except Exception as e:
            self.start_error = 'The fin engine’s palette couldn’t open: %s' % e
        return self.start_error is None

    def stop(self):
        try:
            pal = self.ui.palettes.itemById(PALETTE_ID)
            if pal:
                pal.deleteMe()
        except Exception:
            pass
        self.palette = None
        self.handlers.clear()
        self.ready = False
        self._job = None
        self._done = None

    def problem(self):
        """None while the engine is (or may still become) usable, else why not."""
        if self.start_error:
            return self.start_error
        if not self.ready and self.started_at is not None \
                and time.monotonic() - self.started_at > READY_TIMEOUT_S:
            return ('The fin engine’s palette never reported back. Stop and Run the add-in '
                    '(Shift+S), and if it keeps happening, send View > Show Text Commands.')
        return None

    # ----- jobs

    def submit(self, key, soup, options, done):
        soup_b64, opts = encode_job(soup, options)
        self._seq += 1
        self._job = {'id': self._seq, 'key': key, 'soup': soup_b64, 'options': opts,
                     'sent': False}
        self._done = done
        self.wake()
        return self._seq

    def wake(self):
        """Nudge the palette to ask for work now rather than at its next poll."""
        if self.palette is None:
            return
        try:
            self.palette.sendInfoToHTML('wake', '')
        except Exception:
            pass

    # ----- palette -> Python

    def on_html(self, action, data):
        """One message from the palette; the return value is its reply."""
        if action == 'next':
            job = self._job
            if job and not job['sent']:
                job['sent'] = True
                return json.dumps({'id': job['id'], 'soup': job['soup'],
                                   'options': job['options']})
            return json.dumps({'idle': 100 if self.active else 1000})
        msg = json.loads(data) if data else {}
        if action == 'ready':
            self.ready = bool(msg.get('engine'))
            self.info = msg
            if not self.ready:
                self.start_error = 'The fin engine bundle didn’t load in the palette.'
            self.log('Support Fins: engine palette ready (%s)' % msg.get('browser', '?'))
        elif action == 'result':
            job = self._job
            if not job or msg.get('id') != job['id']:
                return 'stale'
            self._job, done = None, self._done
            if msg.get('error'):
                self.log('Support Fins: engine error: %s' % msg['error'])
                err = 'The fin engine failed on this part (%s).' % msg['error']
                done(job['key'], None, None, err)
            else:
                fins, stats = decode_result(msg['raw'])
                done(job['key'], fins, stats, None)
        elif action == 'log':
            self.log('Support Fins palette: %s' % msg.get('msg'))
        return 'ok'


def _make_html_handler(engine):
    import adsk.core

    class _HTMLHandler(adsk.core.HTMLEventHandler):
        def notify(self, args):
            try:
                args.returnData = engine.on_html(args.action, args.data) or ''
            except Exception as e:
                engine.log('Support Fins: palette message %s failed: %s' % (args.action, e))
                args.returnData = json.dumps({'error': str(e)})

    return _HTMLHandler()

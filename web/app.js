/**
 * Support Fins: the page's entry point. Every feature lives in web/ui/ (one
 * module each: part, pose, settings, finbuild, walls, readout, remove, strength,
 * suggest, history, io, volume, export, scene). What stays here is the wiring:
 * the pointer and keyboard dispatch between the modes, startup, and window.__sf.
 *
 * COORDINATES: Z up, millimetres, bed plane at z = 0, plate centred on the
 * origin in XY. This is the printer's frame and the same one the Python
 * generators use (tools/support/breakaway.py) -- three.js defaults to Y up, so
 * that is overridden here rather than converting at every later step.
 */
import * as THREE from 'three';
import { analyze } from './overhangs.js';
import { buildFins } from './fins.js';
import { findWallPatches } from './planes.js';
import { drawnWall } from './draw.js';
import { el } from './ui/dom.js';
import {
  renderer, scene, camera, controls, frame, resize,
} from './ui/scene.js';
import {
  removeActive, cancelRemove, clearFinHover, hoverRemove, clickRemove,
} from './ui/remove.js';
import { undo, redo } from './ui/history.js';
import { loadURL } from './ui/io.js';
import { writeBinarySTL } from './stl.js';
import { applyVolume, setVolume } from './ui/volume.js';
import { buildExportGeometry } from './ui/export.js';
import './ui/plugins.js';
import { updateReadout } from './ui/readout.js';
import {
  drawnWalls, drawnTris, drawStart, selectedWall, drawActive, sizeMarkers, clearPreview,
  selectWall, removeSelected, drawHover, drawClick,
} from './ui/walls.js';
import { finTris, padTris, lastBuilt } from './ui/finbuild.js';
import { initSettings, applyMaterial } from './ui/settings.js';
import { part, topology, rotM3, lastResult, threshold } from './ui/part.js';
import { gizmo, hoverFace, layActive, cancelLay, layHover, layClick } from './ui/pose.js';

// ------------------------------------------------------------------ keyboard

// Ctrl/Cmd+Z undoes, Ctrl/Cmd+Shift+Z (or Ctrl+Y) redoes. Ignored while typing
// in a field so it never eats a text-edit undo.
addEventListener('keydown', (e) => {
  if (!part) return;
  const tag = e.target.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
  if (selectedWall && (e.key === 'Delete' || e.key === 'Backspace')) {
    e.preventDefault();
    removeSelected();
    return;
  }
  if (!(e.metaKey || e.ctrlKey)) return;
  const k = e.key.toLowerCase();
  if (k === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); }
  else if (k === 'y') { e.preventDefault(); redo(); }
});

// ---------------------------------------------------------- pointer dispatch
// One set of viewport listeners, routed by mode: remove, then draw, then lay flat.

let pressAt = null;
renderer.domElement.addEventListener('pointermove', (ev) => {
  // Remove-fins mode: hover lights the fin a click would drop, in red.
  if (removeActive()) {
    hoverFace.visible = false;
    hoverRemove(ev);
    return;
  }
  // Draw / Suggest "+ Add": the pointer places wall endpoints ON the overhang, so
  // face-lay hover is off and the cursor / band / ghost track the surface instead.
  // Draw picks ANY surface point the user aims at -- including the red overhang
  // faces themselves -- because a drawn breakaway wall sweeps under the line you
  // draw, wherever you draw it; there is no "grippable face" gate to fight.
  if (drawActive()) {
    hoverFace.visible = false;
    drawHover(ev);
    return;
  }
  // Face-lay hover ONLY while armed: otherwise a highlighted, clickable-looking
  // face invites the stray click that silently re-lays the part. Off by default,
  // clicks just orbit.
  if (!layActive() || !part || gizmo.dragging || gizmo.axis || pressAt) {
    hoverFace.visible = false;
    return;
  }
  layHover(ev);
});

renderer.domElement.addEventListener('pointerleave', () => {
  hoverFace.visible = false;
  clearFinHover();
});

renderer.domElement.addEventListener('pointerdown', (e) => {
  pressAt = { x: e.clientX, y: e.clientY };
});

renderer.domElement.addEventListener('pointerup', (e) => {
  const from = pressAt;
  pressAt = null;
  if (!from || !part || !topology) return;

  // a drag is an orbit, not a pick
  if (Math.hypot(e.clientX - from.x, e.clientY - from.y) > 4) return;

  // Remove-fins mode: one click drops just the fin under the pointer.
  if (removeActive()) {
    clickRemove(e);
    return;
  }

  // Draw / Suggest "+ Add": first click sets the start of a wall on the overhang,
  // second commits it. A breakaway wall sweeps under the line between the two
  // points, so the user draws it straight onto the red overhang -- no face gate.
  if (drawActive()) {
    drawClick(e);
    return;
  }

  if (gizmo.dragging || gizmo.axis) return;

  // Lay a face flat -- ONLY when armed via the button. Off by default so a stray
  // viewport click orbits instead of silently discarding a careful rotation.
  if (!layActive()) return;
  layClick(e);
});

// Cancel an armed mode / wall-in-progress: Escape, or a right-click in the viewport.
addEventListener('keydown', (e) => {
  if (e.key !== 'Escape') return;
  if (removeActive()) { cancelRemove(); return; }
  if (layActive()) { cancelLay(); return; }
  if (drawActive() && drawStart) {
    clearPreview();
    updateReadout(lastBuilt);
  } else if (selectedWall) {
    selectWall(selectedWall);        // toggles it off
  }
});
renderer.domElement.addEventListener('contextmenu', (e) => {
  if (removeActive()) { e.preventDefault(); cancelRemove(); return; }
  if (layActive()) { e.preventDefault(); cancelLay(); return; }
  if (!drawActive()) return;
  e.preventDefault();
  if (drawStart) { clearPreview(); updateReadout(lastBuilt); }
});

// ----------------------------------------------------------------- main loop

const fpsEl = el('fps');
let frames = 0, last = performance.now();

function tick(now) {
  requestAnimationFrame(tick);
  controls.update();
  sizeMarkers();
  renderer.render(scene, camera);
  if (++frames >= 20) {
    fpsEl.textContent = `${Math.round((frames * 1000) / (now - last))} fps`;
    frames = 0;
    last = now;
  }
}

initSettings();
applyVolume();
resize();
frame(new THREE.Vector3(60, 60, 60));
requestAnimationFrame(tick);

// debug surface, used to cross-check against the Python probes
window.__sf = { get part() { return part; }, camera, get topo() { return topology; },
                analyze, get threshold() { return threshold; },
                get rot() { return rotM3.elements; },
                get result() { return lastResult; },
                get finTris() { return finTris; },
                get padTris() { return padTris; },
                get drawnTris() { return drawnTris; },
                get drawnWalls() { return drawnWalls; },
                buildFins, findWallPatches, drawnWall, buildExportGeometry };

const params = new URLSearchParams(location.search);
const wanted = params.get('stl');
if (wanted) loadURL(wanted).catch((err) => console.error('?stl=', err));

// ------------------------------------------------------------ OrcaSlicer mode
// ?orca is set by the OrcaSlicer plugin (orca-plugin/support_fins.py), which
// serves this app from a loopback server inside Orca. The part arrives as it sits
// on Orca's plate, the slicer's layer height / filament / bed seed the controls,
// and "Send to OrcaSlicer" posts the finned STL back for the plugin to load.
if (params.has('orca')) initOrca().catch((err) => alert(`OrcaSlicer: ${err.message}`));

async function initOrca() {
  const s = await (await fetch('/orca/session')).json();
  if (s.layer_height) el('layer-height').value = s.layer_height;
  if (s.material) {
    el('material').value = s.material;
    applyMaterial(s.material);
  }
  if (s.volume) setVolume(s.volume[0], s.volume[1], s.volume[2]);
  if (!s.objects.length) throw new Error('the plate is empty -- add a part first');

  const pick = el('orca-object');
  s.objects.forEach((o, i) => pick.add(new Option(o.name, String(i))));
  pick.hidden = s.objects.length < 2;
  // the last path segment is what loadURL names the part, so give it the Orca name
  const load = () => loadURL(
    `/orca/mesh/${pick.value}/${encodeURIComponent(s.objects[Number(pick.value)].name)}.stl`);
  pick.addEventListener('change', load);
  await load();

  // the part comes from the plate and goes back to it: no file open, no 3MF export
  el('file').closest('label').hidden = true;
  el('plugins').closest('.plugins-wrap').hidden = true;
  el('export-3mf').hidden = true;
  // "runs entirely in your browser" isn't true here: the badge carries the credit instead
  const badge = document.querySelector('#topbar .badge');
  badge.textContent = 'by Matthew Trahan · fin technique: Slant 3D';
  badge.title = 'Support Fins by Matthew Trahan (github.com/gittrahan/support-fins). '
    + 'Designed-in support fins are Slant 3D\'s technique. OrcaSlicer port by Tommaso Bianchi.';
  const send = el('to-orca');
  send.hidden = false;
  send.addEventListener('click', async () => {
    const g = buildExportGeometry();
    if (!g) return;
    send.disabled = true;
    try {
      const res = await fetch(`/orca/result?name=${encodeURIComponent(g.base)}`, {
        method: 'POST', body: writeBinarySTL([...g.partTris, ...g.finTris], g.base) });
      const msg = await res.text();
      if (!res.ok) throw new Error(msg);
      alert(msg);
    } catch (err) {
      alert(`Could not send to OrcaSlicer: ${err.message}`);
    } finally {
      send.disabled = false;
    }
  });
}

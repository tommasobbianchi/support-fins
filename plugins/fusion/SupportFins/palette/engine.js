// The Insert Support Fins engine page (a hidden Fusion palette).
//
// Python never waits on this page: the page asks for work ('next'), runs the
// printfins.com engine (SupportFinsEngine, from fins_engine.js) and posts the
// answer back ('result'). See ../engine_host.py.
//
// Fusion injects `adsk` some time after the page loads. In the Qt WebEngine
// browser fusionSendData returns a Promise of Python's args.returnData; older
// browsers returned the string directly, so both are accepted.
(function () {
  'use strict';

  let timer = null;
  let busy = false;
  let jobs = 0;

  // A status line, for when the palette is shown (it normally stays hidden).
  function status(text) {
    const el = document.getElementById('status');
    if (el) el.textContent = text;
  }
  window.addEventListener('error', (e) => status('error: ' + e.message + ' (' + e.filename + ':' + e.lineno + ')'));

  // palette.sendInfoToHTML lands here. Reply at once and do the work later, so
  // Python is never held up.
  window.fusionJavaScriptHandler = {
    handle(action) {
      if (action === 'wake') schedule(0);
      return 'OK';
    },
  };

  function waitForFusion(timeoutMs) {
    return new Promise((resolve, reject) => {
      const t0 = Date.now();
      (function tick() {
        if (window.adsk && typeof window.adsk.fusionSendData === 'function') return resolve();
        if (Date.now() - t0 > timeoutMs) return reject(new Error('Fusion never injected adsk.fusionSendData'));
        setTimeout(tick, 50);
      })();
    });
  }

  async function send(action, data) {
    const r = window.adsk.fusionSendData(action, typeof data === 'string' ? data : JSON.stringify(data ?? {}));
    return (r && typeof r.then === 'function') ? await r : r;
  }

  function schedule(ms) {
    clearTimeout(timer);
    timer = setTimeout(pull, ms);
  }

  async function pull() {
    if (busy) return;
    busy = true;
    let next = 1000;
    try {
      const reply = JSON.parse((await send('next', '')) || '{}');
      if (reply.id) {
        const t0 = performance.now();
        let msg;
        try {
          const raw = SupportFinsEngine.computeFinsB64(reply.soup, reply.options);
          msg = { id: reply.id, raw, ms: Math.round(performance.now() - t0) };
        } catch (e) {
          msg = { id: reply.id, error: String((e && e.message) || e) };
        }
        await send('result', msg);
        jobs += 1;
        status(msg.error ? 'engine error: ' + msg.error : 'ran ' + jobs + ' job(s); last took ' + msg.ms + ' ms');
        next = 0;                       // ask again at once: a newer job may be waiting
      } else if (reply.idle) {
        next = reply.idle;
      }
    } catch (e) {
      status('poll failed: ' + String(e));
      try { await send('log', { msg: 'poll failed: ' + String(e) }); } catch (_) { /* ignore */ }
    } finally {
      busy = false;
    }
    schedule(next);
  }

  status('waiting for Fusion…');
  waitForFusion(15000).then(async () => {
    const engine = typeof SupportFinsEngine === 'object'
      && typeof SupportFinsEngine.computeFinsB64 === 'function';
    const ua = navigator.userAgent.match(/(Chrome|QtWebEngine|Neutron)\/[\d.]+/g);
    status('connected; engine ' + (engine ? 'loaded' : 'MISSING'));
    await send('ready', { engine, browser: ua ? ua.join(', ') : navigator.userAgent });
    status('ready; engine ' + (engine ? 'loaded' : 'MISSING'));
    schedule(0);
  }).catch((e) => status('no Fusion connection: ' + String(e)));
})();

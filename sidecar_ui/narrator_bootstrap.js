/*
 * Narrator frame bootstrap. app.js inlines this script into the srcdoc of the
 * sandboxed Overview frame, after the data block and before the narrator's
 * view.html. It gives the template a small API:
 *
 *   narrator.data              the current view.json (validated by the parent)
 *   narrator.meta              {head, members: [{name, role, status}]}
 *   narrator.onData(fn)        fn(view, meta) now (if data is here) and on
 *                              every update; updates never reload the page
 *   narrator.navigate(action)  ask the parent to open {type, target}
 *   narrator.feedback(id)      ask the parent to start UI feedback on an item
 *
 * plus a 💬 button on hover over any [data-narrator-id] element, and an error
 * trap that reports uncaught errors (the parent then shows its plain view).
 *
 * This script is UNTRUSTED: the template runs in the same frame and can
 * replace or bypass any of it. The parent validates every message it gets
 * (source, type, size, user activation, targets against the snapshot); this
 * file only makes the honest path easy.
 *
 * Messages to the parent: READY, RENDERED {as_of_seq, height}, ERROR
 * {message}, NAVIGATE {action: {type, target}}, FEEDBACK_INTENT {target_id}.
 * Messages from the parent: DATA_UPDATE {view, meta}, THEME {tokens, scheme}.
 */
(() => {
  const parentWin = window.parent;
  const send = (msg) => {
    try {
      parentWin.postMessage(msg, '*');
    } catch {
      // Nothing to do; the parent's watchdog notices a silent frame.
    }
  };

  let data = null;
  let meta = null;
  try {
    const el = document.getElementById('narrator-data');
    const payload = JSON.parse(el ? el.textContent : 'null');
    data = payload?.view ?? null;
    meta = payload?.meta ?? null;
  } catch {
    // Leave data null; the parent sends DATA_UPDATE after READY anyway.
  }

  const subs = [];
  let reported = false;
  function reportError(err) {
    if (reported) return;
    reported = true;
    const message = String(err && (err.stack || err.message) || err || 'error').slice(0, 500);
    send({ type: 'ERROR', message });
  }

  function height() {
    const d = document.documentElement;
    return Math.ceil(Math.max(d ? d.scrollHeight : 0, document.body ? document.body.scrollHeight : 0));
  }

  function rendered() {
    send({ type: 'RENDERED', as_of_seq: Number(data?.as_of_seq) || 0, height: height() });
  }

  function call(fn) {
    try {
      fn(data, meta);
    } catch (e) {
      reportError(e);
    }
  }

  let started = false;
  function deliver() {
    for (const fn of subs) call(fn);
    rendered();
  }

  function applyTheme(msg) {
    const tokens = msg && typeof msg.tokens === 'object' ? msg.tokens : {};
    const root = document.documentElement;
    for (const [k, v] of Object.entries(tokens)) {
      if (/^--[a-z0-9-]{1,40}$/.test(k) && typeof v === 'string') root.style.setProperty(k, v);
    }
    if (msg?.scheme === 'dark' || msg?.scheme === 'light') {
      root.style.colorScheme = msg.scheme;
      root.dataset.theme = msg.scheme;
    }
  }

  window.narrator = Object.freeze({
    get data() {
      return data;
    },
    get meta() {
      return meta;
    },
    onData(fn) {
      if (typeof fn !== 'function') return;
      subs.push(fn);
      if (started && data) {
        call(fn);
        rendered();
      }
    },
    navigate(action) {
      if (!action || typeof action !== 'object') return;
      send({ type: 'NAVIGATE', action: { type: String(action.type || ''), target: action.target } });
    },
    feedback(id) {
      send({ type: 'FEEDBACK_INTENT', target_id: String(id || '') });
    },
  });

  window.addEventListener('message', (e) => {
    if (e.source !== parentWin) return;
    const msg = e.data;
    if (!msg || typeof msg !== 'object') return;
    if (msg.type === 'DATA_UPDATE') {
      data = msg.view ?? null;
      meta = msg.meta ?? null;
      deliver();
    } else if (msg.type === 'THEME') {
      applyTheme(msg);
    }
  });
  window.addEventListener('error', (e) => reportError(e.error || e.message));
  window.addEventListener('unhandledrejection', (e) => reportError(e.reason));

  // 💬 affordance: one floating button, shown over the hovered item.
  let hoverId = '';
  let btn = null;
  function ensureButton() {
    // Templates often re-render the whole body, which drops the button.
    if (btn && btn.isConnected) return btn;
    if (btn) {
      document.body.appendChild(btn);
      return btn;
    }
    btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = '💬';
    btn.title = 'Give feedback on this item';
    btn.setAttribute('aria-label', 'Give feedback on this item');
    btn.style.cssText = 'position:absolute;z-index:2147483647;display:none;width:24px;height:24px;' +
        'padding:0;border-radius:6px;border:1px solid var(--border, rgba(0,0,0,.15));' +
        'background:var(--card, #fff);color:var(--foreground, #222);font-size:13px;line-height:22px;' +
        'cursor:pointer;box-shadow:0 1px 3px rgba(0,0,0,.15)';
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (hoverId) send({ type: 'FEEDBACK_INTENT', target_id: hoverId });
    });
    document.body.appendChild(btn);
    return btn;
  }
  document.addEventListener('mouseover', (e) => {
    if (btn && (e.target === btn)) return;
    const el = e.target instanceof Element ? e.target.closest('[data-narrator-id]') : null;
    if (!el) {
      if (btn) btn.style.display = 'none';
      hoverId = '';
      return;
    }
    const b = ensureButton();
    hoverId = el.getAttribute('data-narrator-id') || '';
    const r = el.getBoundingClientRect();
    b.style.top = `${window.scrollY + r.top + 4}px`;
    b.style.left = `${window.scrollX + Math.max(0, r.right - 28)}px`;
    b.style.display = 'block';
  });

  // Keep the parent's frame height in step with the content.
  let lastHeight = 0;
  let raf = 0;
  function watchHeight() {
    if (typeof ResizeObserver !== 'function' || !document.body) return;
    new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        const h = height();
        if (Math.abs(h - lastHeight) > 2) {
          lastHeight = h;
          rendered();
        }
      });
    }).observe(document.body);
  }

  function start() {
    started = true;
    send({ type: 'READY' });
    deliver();
    watchHeight();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();

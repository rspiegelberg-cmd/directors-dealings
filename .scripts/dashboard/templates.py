"""HTML page chrome for the Stage 5 dashboard.

Single base_page() returns the full HTML doc (Tailwind CDN + Chart.js CDN
+ header nav + body slot + footer). No Jinja, just str.format.

Locked: Tailwind 3.4.x CDN + Chart.js 4.4.6 (jsDelivr, pinned). Light mode
default. No build step.
"""
from __future__ import annotations

from . import render_helpers as h

TAILWIND_CDN = "https://cdn.tailwindcss.com/3.4.16"
CHARTJS_CDN = ("https://cdn.jsdelivr.net/npm/chart.js@4.4.6/dist/"
               "chart.umd.min.js")
CHARTJS_ANNOTATION_CDN = ("https://cdn.jsdelivr.net/npm/"
                          "chartjs-plugin-annotation@3.0.1/dist/"
                          "chartjs-plugin-annotation.min.js")


def base_page(title: str, body: str, generated_at_iso: str | None,
              build_sha: str = "local",
              extra_head: str = "",
              include_chartjs: bool = True,
              include_annotation: bool = False,
              nav_links: list | None = None) -> str:
    """Return a complete HTML5 document.

    `nav_links`: list of (label, href) pairs rendered top-right of header.
    Defaults to [("Today", "index.html"), ("Performance", "performance.html")].
    """
    if nav_links is None:
        nav_links = [("Today", "index.html"), ("Performance", "performance.html"),
                     ("Baskets", "baskets.html"), ("Review", "/review")]

    scripts = [f'<script src="{TAILWIND_CDN}"></script>']
    if include_chartjs:
        scripts.append(f'<script src="{CHARTJS_CDN}"></script>')
    if include_annotation:
        scripts.append(f'<script src="{CHARTJS_ANNOTATION_CDN}"></script>')

    nav_html = " ".join(
        f'<a href="{h.esc(href)}" '
        f'class="text-xs text-indigo-600 hover:text-indigo-700 ml-4">'
        f'{h.esc(label)}</a>'
        for label, href in nav_links
    )

    head = "\n".join(scripts) + "\n" + extra_head

    page = (
        '<!doctype html>\n'
        '<html lang="en">\n'
        '<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="build-sha" content="{h.esc(build_sha)}">\n'
        f'<title>{h.esc(title)}</title>\n'
        f'{head}\n'
        '<style>\n'
        '  html, body { background:#f8fafc; color:#0f172a; }\n'
        '  .tabular-nums { font-variant-numeric: tabular-nums; }\n'
        '  .pulse-highlight { animation: pulseBg 2s ease-out 1; }\n'
        '  @keyframes pulseBg {\n'
        '    0%   { background-color: #fef3c7; }\n'
        '    100% { background-color: transparent; }\n'
        '  }\n'
        '</style>\n'
        '</head>\n'
        '<body class="bg-slate-50 text-slate-900 font-sans">\n'
        '<header class="border-b border-slate-200 bg-white px-4 sm:px-6 '
        'h-auto min-h-[48px] py-2 sm:py-0 sm:h-12 '
        'flex items-center justify-between gap-2">\n'
        f'<h1 class="text-sm font-semibold text-slate-900 tracking-tight '
        f'min-w-0 truncate">'
        f'{h.esc(title)}</h1>\n'
        f'<nav class="flex items-center flex-shrink-0">{_refresh_button_html()}{nav_html}</nav>\n'
        '</header>\n'
        '<main class="max-w-7xl mx-auto">\n'
        f'{body}\n'
        '</main>\n'
        f'{h.generated_at_footer(generated_at_iso, build_sha)}\n'
        f'{_refresh_modal_and_js()}'
        '</body>\n'
        '</html>\n'
    )
    return page


def toast_and_modal_js() -> str:
    """Return the inline <script> implementing the deprecate modal + toast.

    Used by performance.html. POSTs to /api/deprecate (relative).
    Optimistic UI + revert on failure.
    """
    return """
<script>
(function(){
  let inflight = false;
  function showToast(msg, ok){
    const t = document.createElement('div');
    t.className = (ok ? 'bg-emerald-600' : 'bg-rose-600')
      + ' fixed bottom-4 right-4 text-white text-xs px-4 py-2 rounded shadow-lg z-50';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function(){ t.remove(); }, ok ? 3000 : 6000);
  }
  function closeModal(){
    const m = document.getElementById('depModal');
    if (m) m.remove();
  }
  window.onDeprecateClick = function(evt){
    // Sprint 14 Phase 3: the whole scoreboard row is now a focus-mode click
    // target. Stop the event so clicking Deprecate doesn't also open the
    // cohort focus view.
    if (evt && evt.stopPropagation) evt.stopPropagation();
    const btn = evt.currentTarget;
    const sid = btn.getAttribute('data-signal-id');
    const action = btn.getAttribute('data-action') || 'deprecate';
    if (btn.disabled || inflight) return;
    const verb = action === 'deprecate' ? 'Deprecate' : 'Reactivate';
    const modal = document.createElement('div');
    modal.id = 'depModal';
    modal.className = 'fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50';
    modal.innerHTML =
      '<div class="bg-white rounded-lg shadow-xl max-w-md w-full p-6">'
      + '<h3 class="text-sm font-semibold text-slate-900 mb-2">' + verb + ' ' + sid.toUpperCase() + '?</h3>'
      + '<p class="text-xs text-slate-700 mb-4 leading-relaxed">'
      + 'This will write <code>' + sid + '</code> = "' + action + '" to '
      + '<code>.data/signal_status.json</code>. The signal engine will skip '
      + sid + ' on its next eval pass. Existing fired rows are preserved. '
      + 'This is reversible -- edit signal_status.json by hand to undeprecate.'
      + '</p>'
      + '<div class="flex justify-end gap-2">'
      + '<button id="depCancel" class="px-3 py-1.5 text-xs border border-slate-300 rounded text-slate-600 hover:bg-slate-50">Cancel</button>'
      + '<button id="depConfirm" class="px-3 py-1.5 text-xs bg-rose-600 text-white rounded hover:bg-rose-700">' + verb + ' signal</button>'
      + '</div></div>';
    document.body.appendChild(modal);
    document.getElementById('depCancel').onclick = closeModal;
    document.getElementById('depConfirm').onclick = function(){
      closeModal();
      const tile = btn.closest('[data-signal-tile]');
      btn.disabled = true;
      if (tile) { tile.classList.add('opacity-50','pointer-events-none'); }
      inflight = true;
      fetch('/api/deprecate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          signal_id: sid, action: action,
          deprecated_by: 'dashboard-ui',
          timestamp: new Date().toISOString()
        })
      }).then(function(resp){
        inflight = false;
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json();
      }).then(function(data){
        showToast('Signal ' + sid + ' ' + action + 'd.', true);
        if (tile) { tile.setAttribute('data-deprecated','1'); }
      }).catch(function(err){
        inflight = false;
        btn.disabled = false;
        if (tile) { tile.classList.remove('opacity-50','pointer-events-none'); }
        showToast('Could not ' + action + ' ' + sid + ': ' + err.message
          + '. Edit .data/signal_status.json manually or check server.', false);
      });
    };
  };
})();
</script>
"""

def _refresh_button_html() -> str:
    """Small Refresh pill rendered first in the page nav. Triggers the modal."""
    return (
        '<button id="refreshBtn" type="button" '
        'class="text-xs px-2.5 py-1 rounded border border-slate-300 '
        'text-slate-700 hover:bg-slate-100 hover:text-slate-900 '
        'flex items-center gap-1.5" '
        'title="Run the full refresh pipeline (scrape -> parse -> backfill '
        '-> eval -> rebuild). Slow (~15+ min) and uses LLM credits.">'
        '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" '
        'stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/>'
        '<path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/>'
        '<path d="M3 21v-5h5"/></svg>'
        '<span id="refreshBtnLabel">Refresh</span>'
        '</button>'
    )


def _refresh_modal_and_js() -> str:
    """Confirm modal + polling JS for the Refresh pipeline.

    The modal shows estimated cost+time and only fires the POST after
    explicit user confirmation. Polls /api/refresh-status every 5s while
    running. On done -> reload; on error -> show error and reset button.
    Auto-runs status poll on page load so a refresh started in another
    tab still shows progress here.
    """
    return """
<script>
(function(){
  const $ = function(id){ return document.getElementById(id); };
  let pollTimer = null;
  let lastStatus = 'idle';

  function setBtn(label, disabled){
    const btn = $('refreshBtn');
    if (!btn) return;
    const lab = $('refreshBtnLabel');
    if (lab) lab.textContent = label;
    btn.disabled = !!disabled;
    btn.classList.toggle('opacity-60', !!disabled);
    btn.classList.toggle('cursor-not-allowed', !!disabled);
  }
  function showToast(msg, ok){
    const t = document.createElement('div');
    t.className = (ok ? 'bg-emerald-600' : 'bg-rose-600')
      + ' fixed bottom-4 right-4 text-white text-xs px-4 py-2 rounded shadow-lg z-50';
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function(){ t.remove(); }, ok ? 4000 : 8000);
  }
  function startPolling(){
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, 5000);
    pollStatus();
  }
  function stopPolling(){
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
  }
  function pollStatus(){
    fetch('/api/refresh-status').then(function(r){ return r.json(); })
      .then(function(s){
        lastStatus = s.status || 'idle';
        if (lastStatus === 'running'){
          const step = s.step_label || s.step || 'Working...';
          setBtn('Refreshing - ' + step, true);
        } else if (lastStatus === 'done'){
          stopPolling();
          showToast('Refresh complete. Reloading...', true);
          setTimeout(function(){ window.location.reload(); }, 800);
        } else if (lastStatus === 'error'){
          stopPolling();
          setBtn('Refresh', false);
          showToast('Refresh failed: ' + (s.error || 'unknown'), false);
        } else {
          setBtn('Refresh', false);
        }
      }).catch(function(){ /* swallow */ });
  }
  function closeModal(){
    const m = $('refreshModal');
    if (m) m.remove();
  }
  function openConfirmModal(){
    const modal = document.createElement('div');
    modal.id = 'refreshModal';
    modal.className = 'fixed inset-0 bg-slate-900/40 flex items-center '
      + 'justify-center z-50';
    modal.innerHTML =
      '<div class="bg-white rounded-lg shadow-xl max-w-lg w-full p-6">'
      + '<h3 class="text-sm font-semibold text-slate-900 mb-2">'
      + 'Run full refresh pipeline?</h3>'
      + '<p class="text-xs text-slate-700 mb-3 leading-relaxed">'
      + 'This will run the 6-step pipeline end-to-end:</p>'
      + '<ol class="text-xs text-slate-700 mb-4 list-decimal pl-5 space-y-1">'
      + '<li>Scrape Investegate RNS (last 60 days)</li>'
      + '<li>Backfill share prices (Yahoo)</li>'
      + '<li>Update sector benchmarks</li>'
      + '<li>Recompute signal firings</li>'
      + '<li>Rebuild signals/dealings JSON</li>'
      + '<li>Regenerate dashboard HTML</li>'
      + '</ol>'
      + '<div class="bg-amber-50 border border-amber-200 rounded p-3 mb-4">'
      + '<p class="text-[11px] text-amber-800 font-medium mb-1">'
      + 'Heads up</p>'
      + '<p class="text-[11px] text-amber-700 leading-relaxed">'
      + 'Takes ~15-30 min. The scrape step uses Anthropic LLM credits '
      + '(budget cap ~$50/run, set in run_scrape.py). Yahoo Finance may '
      + 'rate-limit if run too often.</p>'
      + '</div>'
      + '<label class="flex items-center gap-2 text-xs text-slate-700 mb-4">'
      + '<input id="refreshNoLlm" type="checkbox" class="rounded">'
      + 'Skip LLM fallback (regex-only parsing)'
      + '</label>'
      + '<div class="flex justify-end gap-2">'
      + '<button id="refreshCancel" class="px-3 py-1.5 text-xs border '
      + 'border-slate-300 rounded text-slate-600 hover:bg-slate-50">'
      + 'Cancel</button>'
      + '<button id="refreshConfirm" class="px-3 py-1.5 text-xs '
      + 'bg-indigo-600 text-white rounded hover:bg-indigo-700">'
      + 'Run pipeline</button>'
      + '</div></div>';
    document.body.appendChild(modal);
    $('refreshCancel').onclick = closeModal;
    $('refreshConfirm').onclick = function(){
      const noLlm = $('refreshNoLlm').checked;
      closeModal();
      setBtn('Starting...', true);
      fetch('/api/refresh-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ no_llm: noLlm })
      }).then(function(resp){
        if (resp.status === 409){
          showToast('A refresh is already running.', false);
        } else if (!resp.ok){
          throw new Error('HTTP ' + resp.status);
        } else {
          showToast('Refresh started.', true);
        }
        startPolling();
      }).catch(function(err){
        setBtn('Refresh', false);
        var hint = err.message;
        if (/failed to fetch/i.test(hint)){
          hint = 'server unreachable. Run `python server.py` and visit '
            + 'http://localhost:5000 in your browser.';
        }
        showToast('Could not start refresh: ' + hint, false);
      });
    };
  }

  document.addEventListener('DOMContentLoaded', function(){
    const btn = $('refreshBtn');
    if (!btn) return;
    // If the page was opened via file://, no Flask server is reachable.
    // Disable the button and tell the user how to fix it -- silent
    // "Failed to fetch" errors are the #1 confusion point.
    if (window.location.protocol === 'file:'){
      setBtn('Refresh (start server)', true);
      btn.title = 'This page was opened from disk (file://). Run '
        + '`python server.py` and visit http://localhost:5000 to enable '
        + 'the Refresh button.';
      btn.addEventListener('click', function(){
        showToast('Open http://localhost:5000 (run python server.py first) '
          + 'to use Refresh.', false);
      });
      return;
    }
    btn.addEventListener('click', openConfirmModal);
    // On load, check status -- if running, attach to existing pipeline.
    fetch('/api/refresh-status').then(function(r){ return r.json(); })
      .then(function(s){
        if (s.status === 'running'){
          setBtn('Refreshing - ' + (s.step_label || 'Working...'), true);
          startPolling();
        }
      }).catch(function(err){
        // Server unreachable on a non-file:// page -- the server died
        // or is on a different host. Tell the user something useful.
        setBtn('Refresh (server down)', true);
        btn.title = 'Could not reach /api/refresh-status. Is the Flask '
          + 'server still running? Try restarting `python server.py`.';
      });
  });
})();
</script>
"""


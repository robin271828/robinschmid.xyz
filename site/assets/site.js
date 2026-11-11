// Shared helpers: theme toggle, top bar, escaping, dates.
(function () {
  const root = document.documentElement;
  const KEY = "rs-theme";

  function stored() {
    try { return localStorage.getItem(KEY); } catch { return null; }
  }
  function store(v) {
    try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch {}
  }
  function systemDark() {
    return window.matchMedia("(prefers-color-scheme: dark)").matches;
  }
  function isDark() {
    const t = root.getAttribute("data-theme");
    return t ? t === "dark" : systemDark();
  }

  const t = stored();
  if (t === "dark" || t === "light") root.setAttribute("data-theme", t);

  const SUN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>';
  const MOON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';

  function paint(btn) {
    const dark = isDark();
    btn.innerHTML = dark ? SUN : MOON;
    btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    btn.title = btn.getAttribute("aria-label");
  }

  document.addEventListener("DOMContentLoaded", () => {
    const btn = document.getElementById("theme");
    if (btn) {
      paint(btn);
      btn.addEventListener("click", () => {
        const next = isDark() ? "light" : "dark";
        // Going back to what the system prefers clears the override.
        if ((next === "dark") === systemDark()) { root.removeAttribute("data-theme"); store(null); }
        else { root.setAttribute("data-theme", next); store(next); }
        paint(btn);
      });
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => paint(btn));
    }
    const bar = document.querySelector(".topbar");
    if (bar) {
      const onScroll = () => bar.classList.toggle("scrolled", window.scrollY > 4);
      onScroll();
      window.addEventListener("scroll", onScroll, { passive: true });
    }
  });

  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ESC[c]);

  const parseDay = (iso) => new Date(iso + "T00:00:00Z");
  const fmt = (iso, opts) => parseDay(iso).toLocaleDateString("en-US", { timeZone: "UTC", ...opts });

  async function getJSON(url) {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) throw new Error(`${url}: ${r.status}`);
    return r.json();
  }

  // Keep filter state in the query string so a filtered view can be linked.
  function readParams(defaults) {
    const p = new URLSearchParams(location.search);
    const out = { ...defaults };
    for (const k of Object.keys(defaults)) if (p.has(k)) out[k] = p.get(k);
    return out;
  }
  function writeParams(state, defaults) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(state)) if (v && v !== defaults[k]) p.set(k, v);
    const qs = p.toString();
    history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
  }

  function debounce(fn, ms) {
    let t;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }

  window.Site = { esc, fmt, parseDay, getJSON, readParams, writeParams, debounce };
})();

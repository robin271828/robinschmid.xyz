// Paper digest page: daily arXiv picks and the per-venue view.
(() => {
  const { esc, fmt, getJSON, readParams, writeParams, debounce } = Site;
  const DATA = "../data/papers/";
  const DAYS_PER_PAGE = 7;
  const DEFAULTS = { view: "daily", tag: "", venue: "", q: "" };
  const state = { ...readParams(DEFAULTS), daily: [], venues: null, llm: false, days: DAYS_PER_PAGE };
  const $ = (id) => document.getElementById(id);

  // ------------------------------------------------------------ loading

  async function loadDaily() {
    let index;
    try { index = await getJSON(DATA + "index.json"); }
    catch { return; }
    const months = await Promise.all((index.months || []).map((m) =>
      getJSON(`${DATA}${m}.json`).then((d) => d.entries || []).catch(() => [])));
    state.daily = months.flat().sort((a, b) =>
      b.date.localeCompare(a.date) || (b.relevance ?? -1) - (a.relevance ?? -1) || b.kw_score - a.kw_score);
    state.llm = !!index.llm;
    if (index.updated) $("updated").textContent = `Updated ${fmt(index.updated, { month: "long", day: "numeric", year: "numeric" })}`;
  }

  async function loadVenues() {
    if (state.venues) return;
    try {
      const d = await getJSON(DATA + "venues.json");
      state.venues = d.venues || [];
      state.venuesSince = d.since;
    } catch { state.venues = []; }
  }

  // ------------------------------------------------------------ filtering

  function matches(e) {
    if (state.tag && !(e.tags || []).includes(state.tag) && !(e.llm_tags || []).includes(state.tag)) return false;
    const q = state.q.trim().toLowerCase();
    if (!q) return true;
    const hay = [e.id, e.title, e.summary, e.abstract, (e.authors || []).join(" "), e.venue?.label].join(" ").toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  }

  function currentPapers() {
    if (state.view === "daily") return state.daily;
    const vs = (state.venues || []).filter((v) => !state.venue || v.name === state.venue);
    return vs.flatMap((v) => v.papers);
  }

  // ------------------------------------------------------------ rendering

  function venueBadge(v) {
    if (!v) return "";
    if (v.status === "submitted") return `<span class="badge muted" title="Under review">submitted · ${esc(v.label)}</span>`;
    return `<span class="badge${v.workshop ? " muted" : ""}">${esc(v.label)}</span>`;
  }

  function authors(list) {
    if (!list?.length) return "";
    const shown = list.length > 5 ? `${list.slice(0, 4).join(", ")}, … ${list[list.length - 1]}` : list.join(", ");
    return `<p class="auth" title="${esc(list.join(", "))}">${esc(shown)}</p>`;
  }

  function card(e) {
    const meta = [
      `<span>${esc(e.id)}</span>`,
      e.primary ? `<span>${esc(e.primary)}</span>` : "",
      venueBadge(e.venue),
      e.relevance != null ? `<span class="rel" title="How relevant Claude rated this to my interests">${e.relevance}/10</span>` : "",
      e.watched_author ? '<span class="badge muted">followed author</span>' : "",
    ].join("");
    const tags = (e.tags || []).map((t) =>
      `<button class="tag${t === state.tag ? " on" : ""}" data-tag="${esc(t)}">${esc(t)}</button>`).join("");
    const links = [
      `<a class="primary" href="${esc(e.abs_url)}" target="_blank" rel="noopener">arXiv</a>`,
      `<a href="${esc(e.pdf_url)}" target="_blank" rel="noopener">PDF</a>`,
      e.code ? `<a href="${esc(e.code)}" target="_blank" rel="noopener">Code</a>` : "",
      e.project ? `<a href="${esc(e.project)}" target="_blank" rel="noopener">Project</a>` : "",
    ].join("");
    const showAbstract = e.abstract && e.abstract !== e.summary;
    return `<article class="card">
      <div class="meta mono">${meta}</div>
      <h3><a href="${esc(e.abs_url)}" target="_blank" rel="noopener">${esc(e.title)}</a></h3>
      <p class="summary">${esc(e.summary)}</p>
      ${authors(e.authors)}
      ${tags ? `<div class="tags">${tags}</div>` : ""}
      ${showAbstract ? `<details><summary>Abstract</summary><p>${esc(e.abstract)}</p></details>` : ""}
      <div class="plinks">${links}</div>
    </article>`;
  }

  function group(big, small, items) {
    return `<section class="group">
      <header><div class="big">${big}</div><div class="small mono">${small}</div></header>
      <div class="cards">${items.map(card).join("")}</div>
    </section>`;
  }

  function renderChips() {
    for (const b of document.querySelectorAll("[data-view]")) {
      const n = b.dataset.view === "daily" ? state.daily.length
        : (state.venues || []).reduce((s, v) => s + v.papers.length, 0);
      b.setAttribute("aria-pressed", String(b.dataset.view === state.view));
      b.textContent = `${b.dataset.view === "daily" ? "Daily" : "Venues"}${n ? ` · ${n}` : ""}`;
    }

    const vc = $("venuechips");
    vc.hidden = state.view !== "venues";
    if (state.view === "venues") {
      const vs = (state.venues || []).filter((v) => v.papers.length);
      vc.innerHTML = '<span class="label mono">Venue</span>' +
        `<button class="chip small" data-venue="" aria-pressed="${!state.venue}">All</button>` +
        vs.map((v) => `<button class="chip small" data-venue="${esc(v.name)}" aria-pressed="${state.venue === v.name}">${esc(v.name)} · ${v.papers.length}</button>`).join("");
    }

    const counts = new Map();
    for (const e of currentPapers()) for (const t of e.tags || []) counts.set(t, (counts.get(t) || 0) + 1);
    const tags = [...counts].sort((a, b) => b[1] - a[1]);
    $("tagchips").innerHTML = tags.length ? '<span class="label mono">Topic</span>' +
      `<button class="chip small" data-tagchip="" aria-pressed="${!state.tag}">All</button>` +
      tags.map(([t, n]) => `<button class="chip small" data-tagchip="${esc(t)}" aria-pressed="${state.tag === t}">${esc(t)} · ${n}</button>`).join("") : "";
  }

  function render() {
    writeParams({ view: state.view, tag: state.tag, venue: state.venue, q: state.q }, DEFAULTS);
    renderChips();
    const list = $("list");
    const more = $("more");
    more.hidden = true;

    if (state.view === "daily") {
      const papers = state.daily.filter(matches);
      const byDay = new Map();
      for (const e of papers) (byDay.get(e.date) || byDay.set(e.date, []).get(e.date)).push(e);
      const days = [...byDay.keys()];
      const shown = days.slice(0, state.days);
      $("count").textContent = `${papers.length} paper${papers.length === 1 ? "" : "s"} over ${days.length} day${days.length === 1 ? "" : "s"}` +
        (state.llm ? " · summaries and relevance by Claude" : "");
      list.innerHTML = shown.length ? shown.map((d) => group(
        esc(fmt(d, { month: "short", day: "numeric" })),
        esc(fmt(d, { weekday: "long" })),
        byDay.get(d))).join("")
        : `<p class="note">${state.daily.length ? "No papers match this filter." : "Nothing here yet. The first run fills this page."}</p>`;
      more.hidden = days.length <= state.days;
      return;
    }

    if (!state.venues) { list.innerHTML = '<p class="note">Loading…</p>'; return; }
    const vs = state.venues.filter((v) => !state.venue || v.name === state.venue)
      .map((v) => ({ ...v, papers: v.papers.filter(matches) })).filter((v) => v.papers.length);
    const n = vs.reduce((s, v) => s + v.papers.length, 0);
    $("count").textContent = `${n} paper${n === 1 ? "" : "s"} from ${vs.length} venue${vs.length === 1 ? "" : "s"}` +
      (state.venuesSince ? ` · submitted to arXiv since ${fmt(state.venuesSince, { month: "short", day: "numeric", year: "numeric" })}` : "");
    list.innerHTML = vs.length ? vs.map((v) => group(esc(v.name), `${v.papers.length} papers`, v.papers)).join("")
      : '<p class="note">No papers match this filter.</p>';
  }

  // ------------------------------------------------------------ events

  document.addEventListener("click", async (ev) => {
    const t = ev.target.closest("button");
    if (!t) return;
    if (t.dataset.view) {
      state.view = t.dataset.view;
      state.venue = "";
      state.tag = "";
      if (state.view === "venues") { render(); await loadVenues(); }
      render();
    } else if (t.dataset.venue !== undefined) {
      state.venue = t.dataset.venue; state.tag = ""; render();
    } else if (t.dataset.tagchip !== undefined) {
      state.tag = t.dataset.tagchip; state.days = DAYS_PER_PAGE; render();
    } else if (t.dataset.tag !== undefined) {
      state.tag = state.tag === t.dataset.tag ? "" : t.dataset.tag;
      state.days = DAYS_PER_PAGE;
      render();
      $("tagchips").scrollIntoView({ block: "nearest", behavior: "smooth" });
    } else if (t.id === "more") {
      state.days += DAYS_PER_PAGE; render();
    }
  });

  const q = $("q");
  q.value = state.q;
  q.addEventListener("input", debounce(() => { state.q = q.value; state.days = DAYS_PER_PAGE; render(); }, 150));

  (async () => {
    await loadDaily();
    if (state.view === "venues") await loadVenues();
    else loadVenues().then(renderChips); // fills in the Venues count without blocking
    render();
  })();
})();

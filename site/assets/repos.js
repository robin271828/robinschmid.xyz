// Repo radar page.
(() => {
  const { esc, fmt, getJSON, readParams, writeParams, debounce } = Site;
  const DEFAULTS = { sort: "rising", src: "", lang: "", q: "" };
  const state = { ...readParams(DEFAULTS), items: [] };
  const $ = (id) => document.getElementById(id);
  const LANG = {
    Python: "#3572A5", "C++": "#f34b7d", C: "#555555", Rust: "#dea584", TypeScript: "#3178c6",
    JavaScript: "#f1e05a", Jupyter: "#DA5B0B", "Jupyter Notebook": "#DA5B0B", Go: "#00ADD8",
    CUDA: "#3A4E3A", Julia: "#a270ba", Shell: "#89e051", Java: "#b07219", Swift: "#F05138", HTML: "#e34c26",
  };
  const nf = new Intl.NumberFormat("en-US");

  // A repo's sources collapse to filter labels: search labels, "paper code", or "watched".
  const labelsOf = (r) => [...new Set(r.sources.map((s) =>
    s.type === "paper" ? "paper code" : s.type === "watch" ? "watched labs" : s.label))];

  function matches(r) {
    if (state.src && !labelsOf(r).includes(state.src)) return false;
    if (state.lang && (r.language || "Other") !== state.lang) return false;
    const q = state.q.trim().toLowerCase();
    if (!q) return true;
    const hay = [r.full_name, r.description, (r.topics || []).join(" "), r.language].join(" ").toLowerCase();
    return q.split(/\s+/).every((w) => hay.includes(w));
  }

  const SORTS = {
    rising: (a, b) => b.stars_week - a.stars_week || b.stars - a.stars,
    new: (a, b) => b.created.localeCompare(a.created) || b.stars - a.stars,
    stars: (a, b) => b.stars - a.stars,
  };

  function card(r) {
    const [owner, name] = r.full_name.split("/");
    const paper = r.sources.find((s) => s.type === "paper");
    const gain = r.stars_week > 0
      ? `<span class="gain" title="${r.week_estimated ? "Estimated from the repo's age and first snapshots" : "Measured from daily snapshots"}">+${nf.format(r.stars_week)} this week${r.week_estimated ? "*" : ""}</span>` : "";
    const lang = r.language ? `<span class="lang" style="--lang:${LANG[r.language] || "var(--mute)"}">${esc(r.language)}</span>` : "";
    const topics = (r.topics || []).slice(0, 5).map((t) => `<span class="tag" style="cursor:default">${esc(t)}</span>`).join("");
    return `<article class="card repo">
      <div class="top">
        ${r.avatar ? `<img src="${esc(r.avatar)}&s=56" alt="" loading="lazy" width="28" height="28">` : ""}
        <h3><a href="${esc(r.url)}" target="_blank" rel="noopener"><span class="owner">${esc(owner)}/</span>${esc(name)}</a></h3>
      </div>
      ${r.description ? `<p class="summary">${esc(r.description)}</p>` : ""}
      ${paper ? `<p class="from">Code for <a href="https://arxiv.org/abs/${esc(paper.paper_id)}" target="_blank" rel="noopener">${esc(paper.paper_title)}</a></p>` : ""}
      <div class="stats mono">
        <span>★ ${nf.format(r.stars)}</span>${gain}${lang}
        <span title="Created">${esc(fmt(r.created, { month: "short", day: "numeric", year: "numeric" }))}</span>
      </div>
      ${topics ? `<div class="tags">${topics}</div>` : ""}
      <div class="plinks">
        <a class="primary" href="${esc(r.url)}" target="_blank" rel="noopener">GitHub</a>
        ${r.homepage ? `<a href="${esc(r.homepage)}" target="_blank" rel="noopener">Website</a>` : ""}
      </div>
    </article>`;
  }

  function chips(el, label, key, counts) {
    const sorted = [...counts].sort((a, b) => b[1] - a[1]);
    el.innerHTML = sorted.length < 2 ? "" : `<span class="label mono">${label}</span>` +
      `<button class="chip small" data-${key}="" aria-pressed="${!state[key]}">All</button>` +
      sorted.map(([v, n]) => `<button class="chip small" data-${key}="${esc(v)}" aria-pressed="${state[key] === v}">${esc(v)} · ${n}</button>`).join("");
  }

  function render() {
    writeParams({ sort: state.sort, src: state.src, lang: state.lang, q: state.q }, DEFAULTS);
    for (const b of document.querySelectorAll("[data-sort]")) b.setAttribute("aria-pressed", String(b.dataset.sort === state.sort));

    const src = new Map(), lang = new Map();
    for (const r of state.items) {
      for (const l of labelsOf(r)) src.set(l, (src.get(l) || 0) + 1);
      const L = r.language || "Other";
      lang.set(L, (lang.get(L) || 0) + 1);
    }
    chips($("srcchips"), "Source", "src", src);
    chips($("langchips"), "Language", "lang", new Map([...lang].filter(([, n]) => n >= 2)));

    const items = state.items.filter(matches).sort(SORTS[state.sort] || SORTS.rising);
    $("count").textContent = `${items.length} repo${items.length === 1 ? "" : "s"}`;
    $("list").innerHTML = items.length ? `<div class="cards">${items.map(card).join("")}</div>`
      : `<p class="note">${state.items.length ? "No repos match this filter." : "Nothing here yet. The first run fills this page."}</p>`;
  }

  document.addEventListener("click", (ev) => {
    const b = ev.target.closest("button");
    if (!b) return;
    for (const key of ["sort", "src", "lang"]) {
      if (b.dataset[key] !== undefined) { state[key] = b.dataset[key]; render(); return; }
    }
  });

  const q = $("q");
  q.value = state.q;
  q.addEventListener("input", debounce(() => { state.q = q.value; render(); }, 150));

  (async () => {
    try {
      const d = await getJSON("../data/repos/repos.json");
      state.items = d.items || [];
      if (d.updated) $("updated").textContent = `Updated ${fmt(d.updated, { month: "long", day: "numeric", year: "numeric" })}`;
    } catch {}
    render();
  })();
})();

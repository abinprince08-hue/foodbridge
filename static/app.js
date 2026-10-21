const state = {
  role: "Visitor",
  auth: null,
  page: "home",
  data: null,
  detailId: null,
  donationCache: null
};
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));
const fmtTime = value => {
  if (!value) return "Time not provided";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? String(value)
    : date.toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
};
const moneyless = value => Number(value || 0).toLocaleString();
const selectedProfile = () => state.auth?.profile || null;
const normalise = value => String(value ?? "").trim().toLocaleLowerCase();

// Only allow web URLs. HTML escaping alone does not make a URL safe for href/src.
function safeHttpUrl(value) {
  if (!value) return "";
  try {
    const url = new URL(String(value).trim(), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch {
    return "";
  }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  if (!headers.has("Accept")) headers.set("Accept", "application/json");

  let response;
  try {
    response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  } catch (cause) {
    const error = new Error("Could not reach the FoodBridge server. Check that Flask is running and try again.");
    error.cause = cause;
    throw error;
  }

  const raw = await response.text();
  let result = {};
  if (raw) {
    try { result = JSON.parse(raw); }
    catch { result = { message: raw.slice(0, 500) }; }
  }

  if (!response.ok) {
    const detail = result.error || result.message || result.detail || `Request failed (HTTP ${response.status}).`;
    const error = new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    error.status = response.status;
    error.details = result.details || result.response_body || null;
    throw error;
  }
  return result;
}
function toast(message, type = "success") {
  let region = $("#toast-region");
  if (!region) {
    region = document.createElement("div");
    region.id = "toast-region";
    region.setAttribute("aria-live", "polite");
    region.setAttribute("aria-atomic", "false");
    document.body.append(region);
  }
  const item = document.createElement("div");
  item.className = `toast ${type === "error" ? "error" : type === "info" ? "info" : ""}`.trim();
  item.setAttribute("role", type === "error" ? "alert" : "status");
  item.textContent = String(message || "Something went wrong. Please try again.");
  region.append(item);
  window.setTimeout(() => item.remove(), 4500);
}
function setPage(page) {
  const allowedPages = new Set(["home", "explore", "directory", "dashboard", "foodtrust", "profiles", "login", "admin", "contact", "create", "detail"]);
  if (!allowedPages.has(page)) page = "home";
  if (page === "dashboard" && !state.auth) page = "login";
  if (page === "admin" && state.role !== "Admin") page = "login";

  state.page = page;
  $$(".page-view").forEach(node => node.classList.add("hidden"));
  const view = $(`#view-${page}`);
  if (view) view.classList.remove("hidden");
  $$("[data-page]").forEach(link => link.classList.toggle("active", link.dataset.page === page));
  $("#mobile-nav")?.classList.remove("open");
  $("#menu-toggle")?.setAttribute("aria-expanded", "false");
  window.scrollTo({ top: 0, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });

  const renderers = {
    home: renderHome, explore: renderExplore, directory: renderDirectory,
    dashboard: renderDashboard, foodtrust: renderFoodTrust, profiles: renderProfiles,
    login: renderLogin, admin: renderAdmin, contact: renderContact, create: renderCreate
  };
  if (page === "detail" && state.detailId) renderDetail(state.detailId);
  else renderers[page]?.();
}
function goDetail(id) { state.detailId = id; setPage("detail"); }
function updateRoleControls() {
  const user = state.auth;
  const accountAction = $("#account-action");
  if (accountAction) {
    accountAction.textContent = user ? `${user.username || user.email || "Account"} · Log out` : "Log in";
    accountAction.dataset.page = user ? "dashboard" : "login";
    accountAction.dataset.logout = user ? "true" : "false";
  }
  const primaryAction = $("#primary-action");
  if (primaryAction) {
    primaryAction.innerHTML = 'Contact us <span aria-hidden="true">↗</span>';
    primaryAction.dataset.page = "contact";
  }
  const knownRole = ["Restaurant", "NGO", "Admin"].includes(state.role);
  $$(".dashboard-nav").forEach(link => link.classList.toggle("hidden", !user || !knownRole));
  $$(".admin-nav").forEach(link => link.classList.toggle("hidden", state.role !== "Admin"));
}
function selectInitialProfiles() {
  const role = normalise(state.auth?.role);
  if (role === "restaurant") state.role = "Restaurant";
  else if (role === "ngo") state.role = "NGO";
  else if (role === "admin") state.role = "Admin";
  else state.role = "Visitor";
}
async function refreshBootstrap() {
  state.data = await api("/api/bootstrap");
  state.donationCache = null; // Refresh listing data after any mutation/bootstrap refresh.
}
function statusBadge(status) {
  const slug = normalise(status).replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "unknown";
  return `<span class="status status-${slug}">${escapeHTML(status || "Unknown")}</span>`;
}
function donationCard(item) {
  const imageUrl = safeHttpUrl(item.image_url);
  const photo = imageUrl
    ? `<img src="${escapeHTML(imageUrl)}" alt="Food photo for ${escapeHTML(item.title || "donation")}" loading="lazy" decoding="async" onerror="this.parentElement.classList.add('image-missing');this.remove()">`
    : '<span class="food-placeholder" aria-label="No food photo provided">🥘</span>';
  const trust = item.trust_available ? '<span class="pill">FoodTrust evidence available</span>' : "";
  const deadlineTime = item.deadline ? Date.parse(item.deadline) : NaN;
  const hoursUntilDeadline = Number.isFinite(deadlineTime) ? (deadlineTime - Date.now()) / 3600000 : null;
  const deadlineTag = hoursUntilDeadline !== null && hoursUntilDeadline <= 0 && ["available", "requested"].includes(normalise(item.status))
    ? '<span class="pill pill-warning">Deadline passed · verify</span>'
    : hoursUntilDeadline !== null && hoursUntilDeadline > 0 && hoursUntilDeadline <= 6
      ? '<span class="pill pill-warning">Pickup soon</span>'
      : "";
  return `<article class="listing-card">
    <div class="food-thumb">${photo}</div>
    <div class="listing-content">
      <h3 class="listing-title">${escapeHTML(item.title || "Food donation")}</h3>
      <p class="listing-meta">${escapeHTML(item.restaurant_name || "Restaurant not listed")} · ${escapeHTML(item.location || "Location not provided")}<br>${escapeHTML(item.quantity ?? "—")} ${escapeHTML(item.unit || "units")} · ${escapeHTML(item.category || "Uncategorised")}<br><span class="pickup-label">Pickup by</span> ${escapeHTML(fmtTime(item.deadline))}</p>
      <div class="listing-tags">${statusBadge(item.status)}${trust}${deadlineTag}</div>
      <div class="card-footer"><span class="reference">${escapeHTML(item.ref || "No reference")}</span><button class="small-link text-link" data-detail="${escapeHTML(item.id)}" aria-label="View details for ${escapeHTML(item.title || "this donation")}">View details <span aria-hidden="true">→</span></button></div>
    </div>
  </article>`;
}
function heading(kicker, title, subtitle) {
  return `<div class="page-heading"><div class="eyebrow">${escapeHTML(kicker)}</div><h1>${escapeHTML(title)}</h1>${subtitle?`<p>${escapeHTML(subtitle)}</p>`:""}</div>`;
}
function renderHome() {
  const view = $("#view-home");
  if (!view) return;
  if (!state.data) { view.innerHTML = '<div class="loading">Loading FoodBridge…</div>'; return; }
  const stats = state.data.stats || {};
  const recentDonations = Array.isArray(state.data.donations) ? state.data.donations.slice(0, 3) : [];
  view.innerHTML = `<section class="hero"><div class="hero-content"><div class="eyebrow">Rescuing food. Building trust. Feeding communities.</div><h1>Don't waste surplus food. Give it another purpose.</h1><p>FoodBridge helps restaurants share surplus food with NGOs and community kitchens, while making donor information easier to investigate.</p><div class="hero-actions"><button class="button button-primary" data-page="contact">Contact us <span>↗</span></button><button class="button button-secondary" data-page="create">＋ List surplus food</button></div></div><div class="hero-art" aria-hidden="true">🌿</div></section>
  <section class="section"><div class="section-heading"><div><h2>Impact, recorded in FoodBridge</h2><p>Totals come from saved donation records.</p></div></div><div class="stats-grid"><div class="stat-card"><div class="stat-value">${moneyless(stats.posted)}</div><div class="stat-label">Donations posted</div></div><div class="stat-card"><div class="stat-value">${moneyless(stats.completed)}</div><div class="stat-label">Donations completed</div></div><div class="stat-card"><div class="stat-value">${moneyless(stats.meals)}</div><div class="stat-label">Meals recorded</div></div><div class="stat-card"><div class="stat-value">${moneyless(stats.organisations)}</div><div class="stat-label">Participating organisations</div></div></div><div class="stats-note">Includes fictional demonstration records. These are not real-world impact claims.</div></section>
  <section class="section"><div class="section-heading"><div><h2>Fresh from the community</h2><p>Recent donations available to browse.</p></div><button class="text-link button button-quiet" data-page="explore">View all donations →</button></div><div class="listing-grid">${recentDonations.map(donationCard).join("") || '<div class="empty-state" style="grid-column:1/-1"><strong>No donations posted yet</strong><p>When restaurants publish surplus food, the latest listings will appear here.</p><button class="button button-secondary" data-page="explore">Explore donations</button></div>'}</div></section>
  <section class="section"><div class="section-heading"><div><h2>How FoodBridge works</h2><p>Three clear steps, from surplus to shared meal.</p></div></div><div class="how-grid"><article class="how-card"><span class="how-icon">🍲</span><h3>Restaurants list surplus</h3><p>Post the food, quantity, collection time and handling details.</p></article><article class="how-card"><span class="how-icon">🔎</span><h3>NGOs inspect and request</h3><p>Review the listing and available donor evidence, then request what you can collect.</p></article><article class="how-card"><span class="how-icon">🤝</span><h3>Both sides coordinate</h3><p>Approve a recipient, record pickup and confirm the handover.</p></article></div></section>
  <section class="section"><div class="trust-strip"><div><h3>FoodTrust brings public evidence into view</h3><p>See accessible business details, reputation signals and published credentials with their sources. Research does not certify the restaurant or establish how a donation is handled today.</p></div><button class="button button-secondary" data-page="foodtrust">Explore FoodTrust ↗</button></div></section>`;
}
function renderExplore() {
  const view = $("#view-explore");
  if (!view) return;
  view.innerHTML = `${heading("THE COMMUNITY FEED", "Explore donations", "Find available food by type, collection area and pickup deadline.")}
    <div class="toolbar explore-toolbar">
      <div class="search-wrap"><label class="sr-only" for="search-input">Search donations</label><input id="search-input" class="control" type="search" placeholder="Search food, restaurant, area or reference…" aria-label="Search donations"></div>
      <select id="category-filter" class="control" aria-label="Food category"><option>All food</option><option>Vegetarian</option><option>Vegan</option><option>Bakery</option><option>Packaged</option><option>Other</option></select>
      <select id="status-filter" class="control" aria-label="Listing status"><option>All statuses</option><option>Available</option><option>Requested</option><option>Accepted</option><option>Picked up</option><option>Completed</option><option>Cancelled</option></select>
      <select id="sort-filter" class="control" aria-label="Sort listings"><option>Newest</option><option>Pickup deadline</option><option>Quantity</option></select>
    </div>
    <div class="filter-row">
      <label class="location-filter-label" for="location-filter">Location <input id="location-filter" class="control" placeholder="Any location"></label>
      <label class="checkbox-line"><input id="trust-filter" type="checkbox"> FoodTrust evidence available</label>
      <span id="result-count" class="result-count" aria-live="polite"></span>
      <button id="clear-filters" class="button button-quiet" type="button">Clear filters</button>
    </div>
    <div id="listing-results" class="listing-grid" aria-live="polite"><div class="loading">Loading donations…</div></div>`;

  let requestPromise = null;
  const results = $("#listing-results", view);
  const count = $("#result-count", view);
  const clearButton = $("#clear-filters", view);

  const loadDonations = () => {
    if (state.donationCache) return Promise.resolve(state.donationCache);
    if (!requestPromise) {
      requestPromise = api("/api/donations")
        .then(items => {
          state.donationCache = Array.isArray(items) ? items : [];
          return state.donationCache;
        })
        .finally(() => { requestPromise = null; });
    }
    return requestPromise;
  };

  const renderResults = async () => {
    if (state.donationCache === null) results.innerHTML = '<div class="loading">Loading donations…</div>';
    try {
      const source = await loadDonations();
      const q = normalise($("#search-input", view).value);
      const category = $("#category-filter", view).value;
      const status = $("#status-filter", view).value;
      const area = normalise($("#location-filter", view).value);
      const trustOnly = $("#trust-filter", view).checked;

      let items = source.filter(item => {
        const searchable = [item.title, item.description, item.restaurant_name, item.location, item.ref, item.category]
          .some(value => normalise(value).includes(q));
        const categoryMatches = category === "All food" || normalise(item.category) === normalise(category);
        const statusMatches = status === "All statuses" || normalise(item.status) === normalise(status);
        const areaMatches = !area || normalise(item.location).includes(area);
        return searchable && categoryMatches && statusMatches && areaMatches && (!trustOnly || Boolean(item.trust_available));
      });

      const sort = $("#sort-filter", view).value;
      if (sort === "Pickup deadline") {
        items.sort((a, b) => (Date.parse(a.deadline) || Infinity) - (Date.parse(b.deadline) || Infinity));
      } else if (sort === "Quantity") {
        items.sort((a, b) => Number(b.quantity || 0) - Number(a.quantity || 0));
      } else {
        items.sort((a, b) => (Date.parse(b.created_at || b.createdAt) || 0) - (Date.parse(a.created_at || a.createdAt) || 0));
      }

      count.textContent = `${items.length} ${items.length === 1 ? "listing" : "listings"} · distance is not estimated`;
      results.innerHTML = items.length
        ? items.map(donationCard).join("")
        : `<div class="empty-state explore-empty" style="grid-column:1/-1"><strong>No donations match these filters</strong><p>Try a different search or clear your filters to see all available records.</p></div>`;
    } catch (error) {
      results.innerHTML = `<div class="empty-state explore-empty" style="grid-column:1/-1"><strong>Donations could not load</strong><p>${escapeHTML(error.message)}</p><button id="retry-donations" class="button button-secondary" type="button">Try again</button></div>`;
      $("#retry-donations", view)?.addEventListener("click", () => { state.donationCache = null; renderResults(); });
    }
  };

  let searchTimer;
  $("#search-input", view).addEventListener("input", () => {
    window.clearTimeout(searchTimer);
    searchTimer = window.setTimeout(renderResults, 140);
  });
  ["category-filter", "status-filter", "sort-filter", "location-filter", "trust-filter"].forEach(id => {
    const input = $("#" + id, view);
    input.addEventListener(id === "location-filter" ? "input" : "change", () => {
      if (id === "location-filter") {
        window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(renderResults, 140);
      } else renderResults();
    });
  });
  clearButton.addEventListener("click", () => {
    $("#search-input", view).value = "";
    $("#category-filter", view).value = "All food";
    $("#status-filter", view).value = "All statuses";
    $("#sort-filter", view).value = "Newest";
    $("#location-filter", view).value = "";
    $("#trust-filter", view).checked = false;
    renderResults();
  });
  renderResults();
}
function trustReport(report, restaurantName) {
  if (!report) return `<div class="panel"><h2>FoodTrust · ${escapeHTML(restaurantName)}</h2><p>No research is saved yet.</p></div>`;
  const data=report.evidence||report; const observations=Array.isArray(data.observations)?data.observations:[];
  const featured=(Array.isArray(data.featured_reviews) ? data.featured_reviews : []).map(review=>{
    const url = safeHttpUrl(review.url);
    const link = url ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>` : "Source link unavailable";
    return `<blockquote class="review-quote"><p>“${escapeHTML(review.excerpt)}”</p><small>${escapeHTML(review.title||"Google Maps review result")} · ${link}</small></blockquote>`;
  }).join("");
  const basicInfo=data.basic_info?`<div class="notice"><b>Basic public information:</b> ${escapeHTML(data.basic_info)}</div>`:"";
  const evidence=observations.map(item=>`<div class="evidence-item"><strong>${escapeHTML(item.category||"Public information")}</strong><p>${escapeHTML(item.claim||"")}<br><span class="provenance">${escapeHTML(item.status||"Check the source")}</span></p></div>`).join("");
  const sources=(data.sources||[]).map(source=>{
    const url = safeHttpUrl(source.url);
    const title = escapeHTML(source.title||source.site_name||"Open source");
    const sourceTitle = url ? `<a href="${escapeHTML(url)}" target="_blank" rel="noopener noreferrer">${title} ↗</a>` : `<strong>${title}</strong>`;
    return `<div class="source-item">${sourceTitle}<small>${escapeHTML(source.site_name||"")} · ${escapeHTML(source.source_type||"Public source")} · ${escapeHTML(source.retrieved_at||"")}</small>${source.snippet?`<small>${escapeHTML(source.snippet)}</small>`:""}</div>`;
  }).join("");
  return `<div class="panel"><h2>FoodTrust · ${escapeHTML(restaurantName)}</h2><p>${escapeHTML(report.state||data.state||"Research complete")}${report.created_at?` · Retrieved ${escapeHTML(report.created_at)}`:""}</p><div class="trust-warning">${escapeHTML(data.note||"Open the linked source to check the latest information.")}</div>${featured?`<h3>Featured Google review search excerpts</h3>${featured}`:""}${basicInfo}${evidence?`<div class="evidence-grid">${evidence}</div>`:""}${sources?`<div class="source-list"><strong style="font-size:11px">Sources (${(data.sources||[]).length})</strong>${sources}</div>`:""}</div>`;
}
async function renderDetail(id) {
  const view=$("#view-detail"); view.innerHTML='<div class="loading">Opening donation…</div>';
  try {
    const item=await api(`/api/donations/${encodeURIComponent(id)}`); 
    const ownProfile=selectedProfile();
    const canChat =
      state.role === "NGO" ||
      (state.role === "Restaurant" &&
      ownProfile?.id === item.restaurant_id);

    const chatButton = canChat
      ? `<div class="panel">
         <button class="button button-secondary"
               data-open-chat
               data-donation-id="${escapeHTML(item.id)}">
           Chat with ${state.role === "NGO" ? "Restaurant" : "NGO"}
         </button>
       </div>`
     : "";
// FOODBRIDGE CHAT BUTTON: END
    const imageUrl = safeHttpUrl(item.image_url);
    const foodPhoto = imageUrl
      ? `<img src="${escapeHTML(imageUrl)}" alt="Food photo for ${escapeHTML(item.title)}" loading="lazy" decoding="async" onerror="this.parentElement.classList.add('image-missing');this.remove()">`
      : `<span class="food-placeholder" aria-label="Food photo not provided">🥘<small>Food photo not provided</small></span>`;
    const donorWebsiteUrl = safeHttpUrl(item.website);
    let actionPanel="";
    if(state.role==="NGO"&&["Available","Requested"].includes(item.status)){
      const existing=(item.requests || []).find(req=>req.ngo_id===ownProfile?.id&&["Pending","Accepted"].includes(req.status));
      actionPanel=existing?`<div class="notice">Your request is ${escapeHTML(existing.status.toLowerCase())}. The restaurant needs to approve it before the donation is reserved.</div>`:`<div class="panel"><h2>Request this donation</h2><form id="request-form"><div class="field"><label for="request-qty">Quantity (${escapeHTML(item.unit)})</label><input id="request-qty" type="number" min="1" max="${item.quantity}" value="${Math.min(Number(item.quantity),20)}" required></div><div class="field"><label for="pickup-at">Expected pickup time</label><input id="pickup-at" type="datetime-local" required></div><div class="field"><label for="pickup-contact">Pickup contact</label><input id="pickup-contact" value="${escapeHTML(ownProfile?.contact||"")}" required></div><div class="field"><label for="request-message">Message (optional)</label><textarea id="request-message"></textarea></div><button class="button button-primary" type="submit">Submit request</button></form></div>`;
    } else if(state.role==="Restaurant"&&ownProfile?.id===item.restaurant_id){
      const requests=(item.requests || []).map(req=>`<div class="request-row"><div class="request-info"><strong>${escapeHTML(req.ngo_name)} · ${escapeHTML(req.quantity)} ${escapeHTML(item.unit)}</strong><p>${escapeHTML(req.ngo_location)} · Pickup ${escapeHTML(fmtTime(req.pickup_at))} · ${escapeHTML(req.ngo_contact||"")}</p>${req.message?`<p>${escapeHTML(req.message)}</p>`:""}<span class="status status-${escapeHTML(req.status.toLowerCase())}">${escapeHTML(req.status)}</span></div>${req.status==="Pending"?`<div class="row-actions"><button class="button button-primary" data-request-action="accept" data-request-id="${escapeHTML(req.id)}">Accept</button><button class="button button-secondary" data-request-action="decline" data-request-id="${escapeHTML(req.id)}">Decline</button></div>`:""}</div>`).join("");
      actionPanel=`<div class="panel"><h2>Incoming requests</h2>${requests||"<p>No requests yet.</p>"}${item.status==="Accepted"?'<button class="button button-primary" data-status="Picked up">Mark picked up</button>':""}${item.status==="Picked up"?'<button class="button button-primary" data-status="Completed">Confirm handover complete</button>':""}${["Available","Requested","Accepted"].includes(item.status)?'<button class="button button-danger" style="margin-top:10px" data-status="Cancelled">Cancel donation</button>':""}</div>`;
    } else if(state.role==="NGO"){
      const mine=(item.requests || []).find(req=>req.ngo_id===ownProfile?.id&&req.status==="Accepted");
      if(mine&&item.status==="Accepted")actionPanel='<div class="panel"><h2>Collection</h2><p>Your request is accepted. Confirm when the food has been collected.</p><button class="button button-primary" data-status="Picked up">Confirm pickup</button></div>';
      if(mine&&item.status==="Picked up")actionPanel='<div class="panel"><h2>Handover</h2><p>Confirm the handover when the donation has been received.</p><button class="button button-primary" data-status="Completed">Confirm handover complete</button></div>';
    }
    view.innerHTML=`<button class="button button-quiet" data-page="explore">← Back to donations</button><div class="page-heading" style="margin-top:15px"><div class="eyebrow">${escapeHTML(item.ref)}</div><h1>${escapeHTML(item.title)}</h1><p>${escapeHTML(item.restaurant_name)} · ${escapeHTML(item.location)}</p></div><div class="content-grid"><div><div class="detail-photo">${foodPhoto}</div><h2 class="detail-title">${escapeHTML(item.quantity)} ${escapeHTML(item.unit)} · ${escapeHTML(item.category)}</h2><div class="listing-tags">${statusBadge(item.status)}${item.trust_available?'<span class="pill">FoodTrust evidence available</span>':""}</div><div class="detail-section"><h2>About this donation</h2><p>${escapeHTML(item.description||"No additional description provided.")}</p></div><div class="detail-section"><h2>Collection details</h2><p><b>Available from:</b> ${escapeHTML(fmtTime(item.available_from))}<br><b>Pickup deadline:</b> ${escapeHTML(fmtTime(item.deadline))}<br><b>Collection point:</b> ${escapeHTML(item.location)}<br><b>Storage and handling:</b> ${escapeHTML(item.handling||"Not provided")}<br><b>Packaging:</b> ${escapeHTML(item.packaging||"Not provided")}<br><b>Allergens:</b> ${escapeHTML(item.allergens||"Not provided")}${item.refrigeration?"<br><b>Requires refrigeration</b>":""}</p>${item.contact_instructions?`<p><b>Collection instructions:</b> ${escapeHTML(item.contact_instructions)}</p>`:""}<div class="notice warning">FoodBridge does not certify food safety. Confirm applicable handling and collection conditions before accepting.</div></div></div><aside><div class="panel"><h2>Donor</h2><div class="donor-name">${escapeHTML(item.restaurant_name)}</div><p>${escapeHTML(item.restaurant_location||"")}</p>${donorWebsiteUrl?`<a href="${escapeHTML(donorWebsiteUrl)}" target="_blank" rel="noopener noreferrer">Visit submitted website ↗</a>`:""}</div>${trustReport(item.foodtrust,item.restaurant_name)}${chatButton}${actionPanel}</aside></div>`;
    $("#request-form")?.addEventListener("submit", async event => {
      event.preventDefault();
      const submit = event.currentTarget.querySelector('button[type="submit"]');
      const quantity = Number($("#request-qty").value);
      const pickupAt = $("#pickup-at").value;
      if (!Number.isFinite(quantity) || quantity < 1 || quantity > Number(item.quantity)) {
        toast(`Enter a quantity between 1 and ${item.quantity}.`, "error"); return;
      }
      if (item.deadline && pickupAt && new Date(pickupAt) > new Date(item.deadline)) {
        toast("Choose a pickup time before the listed deadline.", "error"); return;
      }
      try {
        if (submit) { submit.disabled = true; submit.textContent = "Sending request…"; }
        await api(`/api/donations/${encodeURIComponent(item.id)}/requests`, { method: "POST", body: JSON.stringify({ ngo_id: ownProfile.id, quantity, pickup_at: pickupAt, pickup_contact: $("#pickup-contact").value.trim(), message: $("#request-message").value.trim() }) });
        toast("Request sent. The restaurant must approve it.");
        await refreshBootstrap();
        renderDetail(id);
      } catch (error) {
        if (submit) { submit.disabled = false; submit.textContent = "Submit request"; }
        toast(error.message, "error");
      }
    });
    $$('[data-request-action]',view).forEach(button=>button.addEventListener("click",async()=>{try{await api(`/api/requests/${button.dataset.requestId}/${button.dataset.requestAction}`,{method:"POST",body:"{}"});toast(`Request ${button.dataset.requestAction}ed.`);await refreshBootstrap();renderDetail(id);}catch(error){toast(error.message,"error");}}));
    $$('[data-status]',view).forEach(button=>button.addEventListener("click",async()=>{const status=button.dataset.status;if(status==="Cancelled"&&!confirm("Cancel this donation? It will no longer be available to NGOs."))return;try{await api(`/api/donations/${encodeURIComponent(item.id)}/status`,{method:"POST",body:JSON.stringify({status})});toast(`Donation marked ${status.toLowerCase()}.`);await refreshBootstrap();renderDetail(id);}catch(error){toast(error.message,"error");}}));
  } catch(error) { view.innerHTML=`<div class="empty-state"><strong>Could not open this donation</strong>${escapeHTML(error.message)}<br><button class="button button-secondary" data-page="explore" style="margin-top:12px">Back to donations</button></div>`; }
}
function localDateTime(hoursAhead = 0) { const d=new Date(Date.now()+hoursAhead*3600000);d.setMinutes(d.getMinutes()-d.getTimezoneOffset());return d.toISOString().slice(0,16); }
function renderCreate() {
  const view=$("#view-create");
  if(state.role!=="Restaurant"){view.innerHTML=`${heading("FOR RESTAURANTS","List surplus food","Switch to a restaurant demo profile in the header to create a donation.")}<div class="notice">The local demo uses role switching instead of production authentication.</div>`;return;}
  const profile=selectedProfile();
  view.innerHTML=`${heading("FOR RESTAURANTS","List surplus food","Add the details recipients need to make a quick, informed collection decision.")}<div class="form-shell"><form id="donation-form"><div class="form-card"><div class="form-step">Step 1 of 3 · Food details</div><div class="form-grid"><div class="field span-2"><label for="donation-title">Donation title *</label><input id="donation-title" placeholder="40 vegetarian meal boxes" required></div><div class="field"><label for="donation-quantity">Quantity *</label><input id="donation-quantity" type="number" min="1" value="20" required></div><div class="field"><label for="donation-unit">Unit *</label><select id="donation-unit"><option>meals</option><option>portions</option><option>packs</option><option>boxes</option><option>kg</option><option>items</option></select></div><div class="field span-2"><label for="donation-category">Food category *</label><select id="donation-category"><option>Vegetarian</option><option>Vegan</option><option>Bakery</option><option>Packaged</option><option>Other</option></select></div><div class="field span-2"><label for="donation-description">Food description *</label><textarea id="donation-description" required placeholder="What is included in the donation?"></textarea></div><div class="field span-2"><label for="donation-allergens">Allergen information</label><input id="donation-allergens" placeholder="List known allergens, or say not confirmed"></div></div></div>
  <div class="form-card"><div class="form-step">Step 2 of 3 · Time, location & collection</div><div class="form-grid"><div class="field"><label for="prepared-at">Preparation date and time</label><input id="prepared-at" type="datetime-local" value="${localDateTime()}"></div><div class="field"><label for="available-from">Available from *</label><input id="available-from" type="datetime-local" value="${localDateTime(.5)}" required></div><div class="field"><label for="pickup-deadline">Pickup deadline *</label><input id="pickup-deadline" type="datetime-local" value="${localDateTime(3)}" required></div><div class="field"><label for="collection-location">Collection point *</label><input id="collection-location" value="${escapeHTML(profile?.location||"")}" required></div><div class="field span-2"><label for="handling">Storage and handling information</label><textarea id="handling" placeholder="How has this food been stored?"></textarea></div><div class="field"><label for="packaging">Packaging details</label><input id="packaging" placeholder="Sealed boxes, paper bags…"></div><div class="field"><label for="contact-instructions">Collection instructions</label><input id="contact-instructions" placeholder="Use the side entrance"></div><div class="field span-2"><label for="food-photo">Food photo URL (optional)</label><input id="food-photo" type="url" placeholder="https://…"></div><label class="checkbox-line span-2"><input id="refrigeration" type="checkbox"> This donation requires refrigeration</label></div></div>
  <div class="form-card"><div class="form-step">Step 3 of 3 · Restaurant & FoodTrust</div><div class="notice">Donor profile: <b>${escapeHTML(profile?.name||"Restaurant")}</b>${profile?.website?` · ${escapeHTML(profile.website)}`:" · Add a website in your profile to use FoodTrust."} Details are shown as provided until checked.</div><div class="notice warning">Share allergen and handling information where known. Missing details remain visible to recipients. This form does not certify food as safe.</div><div class="form-actions"><button class="button button-primary" type="submit">Publish donation</button></div></div></form><div id="created-result"></div></div>`;
  $("#donation-form").addEventListener("submit",async event=>{event.preventDefault();const result=$("#created-result");const payload={restaurant_id:profile.id,title:$("#donation-title").value,quantity:Number($("#donation-quantity").value),unit:$("#donation-unit").value,category:$("#donation-category").value,description:$("#donation-description").value,allergens:$("#donation-allergens").value,prepared_at:$("#prepared-at").value,available_from:$("#available-from").value,deadline:$("#pickup-deadline").value,location:$("#collection-location").value,handling:$("#handling").value,packaging:$("#packaging").value,refrigeration:$("#refrigeration").checked,contact_instructions:$("#contact-instructions").value,image_url:$("#food-photo").value};
    if (payload.available_from && payload.deadline && new Date(payload.deadline) <= new Date(payload.available_from)) {
      toast("Pickup deadline must be later than the time food becomes available.", "error");
      return;
    }
    const submit = event.currentTarget.querySelector('button[type="submit"]');
    try {
      if (submit) { submit.disabled = true; submit.textContent = "Publishing…"; }
      const created = await api("/api/donations", { method: "POST", body: JSON.stringify(payload) });
      result.innerHTML = `<div class="notice"><b>Donation published · ${escapeHTML(created.ref || "Saved")}</b><br>Your listing is now available in Explore Donations.<br><button class="button button-secondary" style="margin-top:10px" data-detail="${escapeHTML(created.id)}">View your listing</button></div>`;
      toast("Donation published.");
      await refreshBootstrap();
    } catch (error) {
      result.innerHTML = `<div class="notice warning">${escapeHTML(error.message)}</div>`;
    } finally {
      if (submit) { submit.disabled = false; submit.textContent = "Publish donation"; }
    }
  });
}
function renderDirectory() {
  const view = $("#view-directory");
  const ngos = state.data?.ngos || [];
  if (!view) return;
  view.innerHTML = `${heading("COMMUNITY NETWORK", "NGO directory", "See the service areas, collection arrangements and current needs shared by community organisations.")}
    <div class="directory-grid">${ngos.map(org => {
      let cats = [];
      try { cats = Array.isArray(org.categories) ? org.categories : JSON.parse(org.categories || "[]"); } catch { cats = []; }
      const publicInfo = state.data?.ngo_reports?.[org.id]?.evidence?.basic_info;
      const website = safeHttpUrl(org.website);
      return `<article class="ngo-card"><div class="ngo-head"><div class="ngo-icon" aria-hidden="true">⌂</div><div><h2>${escapeHTML(org.name)}</h2><p>${escapeHTML(org.location)} · ${escapeHTML(org.service_area)}</p></div></div>
        <p>${escapeHTML(publicInfo || org.mission || "Community organisation")}</p>${publicInfo ? '<span class="pill">Source-linked public info</span>' : ""}
        <div class="listing-tags">${cats.map(cat => `<span class="pill">${escapeHTML(cat)}</span>`).join("")}</div>
        <p class="ngo-meta">Collection: ${escapeHTML(org.arrangements || "Details not provided")} · Capacity ${escapeHTML(org.capacity ?? "Not provided")} meals</p>
        <div class="ngo-need"><b>Current need</b><p>${escapeHTML(org.need || "No current requirement posted.")}</p></div>
        ${website ? `<p><a href="${escapeHTML(website)}" target="_blank" rel="noopener noreferrer">Organisation website ↗</a></p>` : ""}
        <div class="provenance">${escapeHTML(org.info_status || "Information provided")}</div>
        ${state.role === "Restaurant" ? `<p>Contact ${escapeHTML(org.contact || "not provided")} · ${escapeHTML(org.phone || "")}</p>` : ""}
      </article>`;
    }).join("") || '<div class="empty-state">No NGO profiles yet.</div>'}</div>`;
}
async function renderDashboard() {
  const view=$("#view-dashboard");
  if(!state.auth){setPage("login");return;}
  if(state.auth?.status==="pending") {view.innerHTML=`${heading("REGISTRATION RECEIVED","Awaiting admin approval",`Your ${state.auth?.role==="ngo"?"NGO":"restaurant"} registration is pending review.`)}<div class="notice warning">You can browse FoodBridge while we review your registration, but posting donations or submitting requests is enabled after approval.</div><button id="logout-pending" class="button button-secondary">Log out</button>`;$("#logout-pending").onclick=logout;return;}
  if(state.auth?.status==="rejected") {view.innerHTML=`${heading("REGISTRATION UPDATE","Registration not approved","Contact FoodBridge support if you need help with the review decision.")}<button id="logout-pending" class="button button-secondary">Log out</button>`;$("#logout-pending").onclick=logout;return;}
  if(state.role==="Admin"){view.innerHTML=`${heading("PLATFORM ADMIN","Registration review","Approve or reject new restaurant and NGO registrations.")}<button class="button button-primary" data-page="admin">Open admin review</button>`;return;}
  const role=state.role,profile=selectedProfile(); if(!profile){view.innerHTML='<div class="loading">Loading profile…</div>';return;}
  view.innerHTML='<div class="loading">Loading your dashboard…</div>';
  try{
    const data=await api(`/api/dashboard/${role.toLowerCase()}/${encodeURIComponent(profile.id)}`);
    if(role==="Restaurant"){
      const requestsList = Array.isArray(data.requests) ? data.requests : []; const donationsList = Array.isArray(data.donations) ? data.donations : []; const pending=requestsList.filter(r=>r.status==="Pending").length;const completed=donationsList.filter(d=>d.status==="Completed").length;
      const requests=requestsList.slice(0,6).map(req=>`<div class="request-row"><div class="request-info"><strong>${escapeHTML(req.ngo_name)} requested ${escapeHTML(req.quantity)} · ${escapeHTML(req.title)}</strong><p>${escapeHTML(req.ngo_location)} · ${escapeHTML(fmtTime(req.pickup_at))}</p><span class="status status-${escapeHTML(req.status.toLowerCase())}">${escapeHTML(req.status)}</span></div><button class="button button-secondary" data-detail="${escapeHTML(req.donation_id)}">Open request</button></div>`).join("");
      view.innerHTML=`<div class="dashboard-head">${heading("RESTAURANT WORKSPACE",`Welcome, ${profile.name}`,"Manage offers, incoming requests and pickup status.")}<button class="button button-primary" data-page="create">＋ List surplus food</button></div><div class="dashboard-kpis"><div class="dashboard-card"><h3>My listings</h3><strong>${donationsList.length}</strong></div><div class="dashboard-card"><h3>Pending requests</h3><strong>${pending}</strong></div><div class="dashboard-card"><h3>Completed handovers</h3><strong>${completed}</strong></div></div><section class="section"><div class="section-heading"><div><h2>My donations</h2></div></div><div class="listing-grid">${donationsList.map(donationCard).join("")||'<div class="empty-state" style="grid-column:1/-1">You have not listed a donation yet.</div>'}</div></section><section class="section"><div class="section-heading"><div><h2>Recent requests</h2></div></div><div class="panel">${requests||"<p>No incoming requests yet.</p>"}</div></section>`;
    } else {
      const currentNeed=profile.need||"Add a current requirement";
      const requests=data.requests.map(req=>`<div class="request-row"><div class="request-info"><strong>${escapeHTML(req.title)} · ${escapeHTML(req.restaurant_name)}</strong><p>${escapeHTML(req.ref)} · Request ${escapeHTML(req.status)} · Donation ${escapeHTML(req.donation_status)}</p></div><button class="button button-secondary" data-detail="${escapeHTML(req.donation_id)}">Open listing</button></div>`).join("");
      view.innerHTML=`<div class="dashboard-head">${heading("NGO WORKSPACE",`Welcome, ${profile.name}`,"Keep today's needs current and track each request through handover.")}<button class="button button-primary" data-page="explore">Browse donations</button></div><div class="panel"><h2>Today's requirement</h2><p>${escapeHTML(currentNeed)}</p><p>General capacity: ${escapeHTML(profile.capacity)} meals · Service area: ${escapeHTML(profile.service_area)}</p><button id="edit-need" class="button button-secondary">Update requirement</button></div><section class="section"><div class="section-heading"><div><h2>My requests</h2></div></div><div class="panel">${requests||'<p>You haven’t requested a donation yet. Browse the feed to find a match.</p>'}</div></section><div class="notice">${state.data.stats.posted} donations posted · ${state.data.stats.meals} meals recorded as completed. Demonstration records are included.</div>`;
      $("#edit-need").addEventListener("click",()=>openNeedModal(profile));
    }
  }catch(error){view.innerHTML=`<div class="empty-state"><strong>Dashboard could not load</strong>${escapeHTML(error.message)}</div>`;}
}
function openNeedModal(profile){
  const modal=document.createElement("div");modal.className="modal-backdrop";modal.innerHTML=`<div class="modal" role="dialog" aria-modal="true" aria-labelledby="need-title"><div class="modal-head"><div><h2 id="need-title">Update today's requirement</h2><p class="field-hint">Keep today's need separate from your usual capacity.</p></div><button class="modal-close" aria-label="Close">×</button></div><form id="need-form"><div class="field"><label for="need-text">What do you need?</label><input id="need-text" value="${escapeHTML(profile.need||"")}"></div><div class="field"><label for="need-area">Service area</label><input id="need-area" value="${escapeHTML(profile.service_area||"")}"></div><div class="field"><label for="need-capacity">Typical meal capacity</label><input id="need-capacity" type="number" min="1" value="${escapeHTML(profile.capacity||1)}"></div><div class="form-actions"><button class="button button-primary">Save requirement</button></div></form></div>`;
  document.body.append(modal);
  const closeModal = () => modal.remove();
  modal.querySelector(".modal-close").onclick = closeModal;
  modal.addEventListener("click", e => { if (e.target === modal) closeModal(); });
  $("#need-text", modal)?.focus();
  $("#need-form",modal).addEventListener("submit",async e=>{e.preventDefault();try{await api(`/api/ngos/${encodeURIComponent(profile.id)}/need`,{method:"PATCH",body:JSON.stringify({need:$("#need-text",modal).value,service_area:$("#need-area",modal).value,capacity:Number($("#need-capacity",modal).value)})});modal.remove();toast("Requirement updated.");await refreshBootstrap();renderDashboard();}catch(error){toast(error.message,"error");}});
}
async function renderFoodTrust() {
  const view=$("#view-foodtrust");
  let connection={configured:false,message:"Checking TinyFish connection…"};try{connection=await api("/api/foodtrust/status");}catch{}
  view.innerHTML=`${heading("PUBLIC INFORMATION","FoodTrust","Browse restaurant details and Google review search results.")}<div class="trust-strip"><div><h3>Python backend · TinyFish Search + Fetch</h3><p>${escapeHTML(connection.message)} Review excerpts can be incomplete; open Google Maps for the current reviews.</p></div><span class="pill">${connection.configured?"Live research configured":"API key not configured"}</span></div><div id="foodtrust-content" class="section"></div>`;
  const content=$("#foodtrust-content");
  const restaurants=state.data?.restaurants||[];
  const canAdmin=state.role==="Admin", ownRestaurant=selectedProfile();
  const canResearchRestaurant=restaurant=>canAdmin||(state.role==="Restaurant"&&ownRestaurant?.id===restaurant.id);
  const restaurantCards=restaurants.map(restaurant=>{const report=state.data?.reports?.[restaurant.id];return `<article class="panel"><div class="trust-org-head"><div><h2>${escapeHTML(restaurant.name)}</h2><p>${escapeHTML(restaurant.location)}</p></div>${canResearchRestaurant(restaurant)?`<button class="button button-secondary" data-research="restaurant" data-org-id="${escapeHTML(restaurant.id)}">${report?"Refresh Google reviews":"Search Google reviews"}</button>`:""}</div>${report?trustReport(report,restaurant.name):'<p class="field-hint">No Google review search saved yet.</p>'}</article>`;}).join("")||'<div class="empty-state">No restaurants yet.</div>';
  content.innerHTML=`<section class="section"><div class="section-heading"><div><h2>Restaurants · Google reviews</h2><p>Research returns up to three Google Maps search excerpts.</p></div></div><div class="directory-grid">${restaurantCards}</div></section>`;
  $$('[data-research]',content).forEach(button=>button.addEventListener("click",async()=>{
    const orgId=button.dataset.orgId;button.disabled=true;button.textContent="Searching…";
    try{
      const result=await api("/api/foodtrust",{method:"POST",body:JSON.stringify({restaurant_id:orgId})});
      await refreshBootstrap();toast(result.state,"info");renderFoodTrust();
    }catch(error){button.disabled=false;button.textContent="Try again";toast(error.message,"error");}
  }));
}
function renderProfiles() {
  const view=$("#view-profiles");
  view.innerHTML=`${heading("JOIN THE NETWORK","Register your organisation","First choose what you wish to register as. An admin reviews applications before donation actions are enabled.")}<div class="form-shell"><div id="register-choice" class="directory-grid"><button class="form-card register-choice" data-register-role="restaurant"><span class="form-step">I represent a</span><h2>Restaurant / food donor</h2><p class="field-hint">Share surplus food and manage incoming requests.</p><span class="button button-secondary">Continue as restaurant →</span></button><button class="form-card register-choice" data-register-role="ngo"><span class="form-step">I represent an</span><h2>NGO / community kitchen</h2><p class="field-hint">Publish your requirements and request donations.</p><span class="button button-secondary">Continue as NGO →</span></button></div><div id="register-form-holder"></div><div class="notice">Registrations remain pending until an administrator reviews them. Approval grants platform access; it is not an independent legal or food-safety certification.</div><p class="field-hint">Already registered? <button class="text-link" data-page="login">Log in</button></p></div>`;
  $$('[data-register-role]').forEach(button=>button.addEventListener("click",()=>renderRegistrationForm(button.dataset.registerRole)));
}
function renderRegistrationForm(role){
  const isRestaurant=role==="restaurant";const holder=$("#register-form-holder");
  $("#register-choice").classList.add("hidden");
  const fields=isRestaurant?`<div class="field"><label>Restaurant name *</label><input name="name" required></div><div class="field"><label>Contact person</label><input name="contact"></div><div class="form-grid"><div class="field"><label>Location *</label><input name="location" placeholder="Dwarka, New Delhi" required></div><div class="field"><label>Phone</label><input name="phone"></div></div><div class="field"><label>Restaurant website</label><input name="website" type="url" placeholder="https://…"></div>`:`<div class="field"><label>Organisation name *</label><input name="name" required></div><div class="field"><label>Contact person</label><input name="contact"></div><div class="form-grid"><div class="field"><label>Location *</label><input name="location" placeholder="K.G. Marg, New Delhi" required></div><div class="field"><label>Service area *</label><input name="service_area" placeholder="New Delhi" required></div></div><div class="field"><label>Organisation website</label><input name="website" type="url" placeholder="https://…"></div><div class="field"><label>Mission or purpose</label><textarea name="mission"></textarea></div><div class="field"><label>Food categories accepted</label><select name="categories" multiple size="5"><option>Vegetarian</option><option>Vegan</option><option>Bakery</option><option>Packaged</option><option>Other</option></select><span class="field-hint">Use Ctrl or ⌘ to select multiple options.</span></div><div class="field"><label>Typical meal capacity</label><input name="capacity" type="number" min="1" value="30"></div><div class="field"><label>Collection arrangements</label><input name="arrangements"></div><div class="field"><label>Current requirement</label><input name="need"></div>`;
  holder.innerHTML=`<div class="form-card"><button class="button button-quiet" id="register-back">← Choose another account type</button><div class="form-step" style="margin-top:18px">${isRestaurant?"Restaurant registration":"NGO registration"}</div><form id="selected-register-form"><div class="form-grid"><div class="field"><label>Username *</label><input name="username" minlength="3" maxlength="32" pattern="[A-Za-z0-9_.-]+" autocomplete="username" required></div><div class="field"><label>Email *</label><input name="email" type="email" autocomplete="email" required></div></div><div class="field"><label>Password * <span class="field-hint">At least 10 characters</span></label><input name="password" type="password" minlength="10" autocomplete="new-password" required></div>${fields}<button class="button button-primary">Submit ${isRestaurant?"restaurant":"NGO"} registration</button></form></div>`;
  $("#register-back").onclick=()=>{holder.innerHTML="";$("#register-choice").classList.remove("hidden");};
  $("#selected-register-form").addEventListener("submit",event=>createProfile(event,role));
}
async function createProfile(event,role){
  event.preventDefault();const form=event.currentTarget;const values=Object.fromEntries(new FormData(form).entries());values.role=role;
  if(role==="ngo"){values.categories=$$("[name=categories] option:checked",form).map(option=>option.value);values.capacity=Number(values.capacity||1);}
  const submit = form.querySelector('button[type="submit"], button:not([type])');
  try {
    if (submit) { submit.disabled = true; submit.textContent = "Submitting registration…"; }
    const result = await api("/api/auth/register", { method: "POST", body: JSON.stringify(values) });
    state.auth = result.user;
    selectInitialProfiles();
    await refreshBootstrap();
    updateRoleControls();
    toast(result.message || "Registration received for review.", "info");
    setPage("dashboard");
  } catch (error) {
    toast(error.message, "error");
  } finally {
    if (submit) { submit.disabled = false; submit.textContent = `Submit ${role === "restaurant" ? "restaurant" : "NGO"} registration`; }
  }
}
function renderLogin(){
  const view=$("#view-login");
  if(state.auth){
    view.innerHTML=`${heading("ACCOUNT",state.auth.profile?.name||state.auth.email,"You are signed in.")}<div class="panel"><p>Account: ${escapeHTML(state.auth.email)} · ${escapeHTML(state.auth.role)} · ${escapeHTML(state.auth.status)}</p>${state.auth.status==="pending"?'<div class="notice warning">Registration is waiting for admin approval. You can log out and sign in as the admin to review it.</div>':""}<button id="logout-button" class="button button-secondary">Log out</button></div>`;
    $("#logout-button").onclick=logout;return;
  }
  view.innerHTML=`${heading("WELCOME BACK","Log in to FoodBridge","Use your username and password to manage listings, requests or admin reviews.")}<div class="form-shell"><div class="form-card"><form id="login-form"><div class="field"><label for="login-username">Username</label><input id="login-username" autocomplete="username" required></div><div class="field"><label for="login-password">Password</label><input id="login-password" type="password" autocomplete="current-password" required></div><button class="button button-primary">Log in</button></form><div id="login-error"></div></div><p class="field-hint">New to FoodBridge? <button class="text-link" data-page="profiles">Register your restaurant or NGO</button></p></div>`;
  $("#login-form").addEventListener("submit",async event=>{event.preventDefault();try{const result=await api("/api/auth/login",{method:"POST",body:JSON.stringify({username:$("#login-username").value,password:$("#login-password").value})});state.auth=result.user;selectInitialProfiles();await refreshBootstrap();updateRoleControls();toast(state.auth.status==="approved"?"Logged in successfully.":"Logged in. Your registration is awaiting admin approval.",state.auth.status==="approved"?"success":"info");setPage(state.auth.status==="pending"?"dashboard":state.auth.role==="admin"?"admin":"dashboard");}catch(error){$("#login-error").innerHTML=`<div class="notice warning">${escapeHTML(error.message)}</div>`;}});
}
async function logout(){
  try{await api("/api/auth/logout",{method:"POST",body:"{}"});}catch{}
  state.auth=null;state.role="Visitor";updateRoleControls();toast("You are logged out.","info");setPage("home");
}
function renderContact(){
  const view=$("#view-contact");
  view.innerHTML=`${heading("GET IN TOUCH","Contact FoodBridge","Questions about surplus food, NGO registration or partnership? Send our team a message.")}<div class="form-shell"><div class="form-card"><form id="contact-form"><div class="form-grid"><div class="field"><label for="contact-name">Your name</label><input id="contact-name" value="${escapeHTML(state.auth?.profile?.name||"")}" required></div><div class="field"><label for="contact-email">Email</label><input id="contact-email" type="email" value="${escapeHTML(state.auth?.email||"")}" required></div></div><div class="field"><label for="contact-subject">Subject</label><select id="contact-subject"><option>General enquiry</option><option>Restaurant registration</option><option>NGO registration</option><option>Food donation</option><option>FoodTrust</option><option>Partnership</option></select></div><div class="field"><label for="contact-message">Message</label><textarea id="contact-message" maxlength="5000" required placeholder="How can we help?"></textarea></div><button class="button button-primary">Send message</button><div id="contact-result"></div></form></div></div>`;
  $("#contact-form").addEventListener("submit", async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const submit = form.querySelector('button[type="submit"], button:not([type])');
    try {
      if (submit) { submit.disabled = true; submit.textContent = "Sending…"; }
      const result = await api("/api/contact", { method: "POST", body: JSON.stringify({ name: $("#contact-name").value.trim(), email: $("#contact-email").value.trim(), subject: $("#contact-subject").value, message: $("#contact-message").value.trim() }) });
      $("#contact-result").innerHTML = `<div class="notice">${escapeHTML(result.message || "Your message was sent.")}</div>`;
      form.reset();
      toast("Message sent.");
    } catch (error) {
      $("#contact-result").innerHTML = `<div class="notice warning">${escapeHTML(error.message)}</div>`;
    } finally {
      if (submit) { submit.disabled = false; submit.textContent = "Send message"; }
    }
  });
}
async function renderAdmin(){
  const view=$("#view-admin");
  if(state.role!=="Admin"){view.innerHTML=`${heading("ADMIN REVIEW","Sign in as an administrator","Only the FoodBridge admin can approve registrations.")}<button class="button button-primary" data-page="login">Log in</button>`;return;}
  view.innerHTML=`${heading("PLATFORM ADMIN","Admin console","Review registrations and inspect the full restaurant and NGO database.")}<div class="admin-toolbar"><div class="admin-tabs"><button class="button button-secondary active" data-admin-tab="registrations">Registrations</button><button class="button button-secondary" data-admin-tab="restaurants">Restaurants</button><button class="button button-secondary" data-admin-tab="ngos">NGOs</button><button class="button button-secondary" data-admin-tab="messages">Contact messages</button></div><button id="refresh-admin" class="button button-primary">↻ Refresh database</button></div><div id="admin-content" class="loading">Loading admin data…</div>`;
  try{
    const [registrations,organizations,messages]=await Promise.all([api("/api/admin/registrations"),api("/api/admin/organizations"),api("/api/admin/contact-messages")]);
    const pending=registrations.pending||[],rejected=registrations.rejected||[];
    const regCard=user=>{const p=user.profile||{};const pWebsite = safeHttpUrl(p.website); const details=user.role==="ngo"?`<p><b>Service area:</b> ${escapeHTML(p.service_area)} · <b>Capacity:</b> ${escapeHTML(p.capacity)} meals</p><p><b>Mission:</b> ${escapeHTML(p.mission||"Not provided")}</p><p><b>Categories:</b> ${escapeHTML((()=>{try{return Array.isArray(p.categories)?p.categories.join(", "):JSON.parse(p.categories||"[]").join(", ")}catch{return ""}})())}</p><p><b>Collection:</b> ${escapeHTML(p.arrangements||"Not provided")}</p>`:`<p><b>Website:</b> ${pWebsite?`<a href="${escapeHTML(pWebsite)}" target="_blank" rel="noopener noreferrer">${escapeHTML(pWebsite)} ↗</a>`:"Not provided"}</p>`;return `<article class="panel"><div class="request-row" style="border:0;padding-top:0"><div><span class="pill">${escapeHTML(user.role.toUpperCase())}</span><h2 style="margin:8px 0 3px">${escapeHTML(p.name||"Organisation")}</h2><p>@${escapeHTML(user.username||"")} · ${escapeHTML(user.email)} · Submitted ${escapeHTML(user.created_at)}</p></div><span class="status status-requested">${escapeHTML(user.status)}</span></div><p><b>Contact:</b> ${escapeHTML(p.contact||"Not provided")} · ${escapeHTML(p.phone||"Not provided")}</p><p><b>Location:</b> ${escapeHTML(p.location||"Not provided")}</p>${details}<p><b>Current need:</b> ${escapeHTML(p.need||"Not provided")}</p><div class="notice">Approval enables FoodBridge access. It does not certify food safety or independently verify every claim.</div>${user.status==="pending"?`<div class="row-actions"><button class="button button-primary" data-review="approve" data-user-id="${escapeHTML(user.id)}">Approve registration</button><button class="button button-danger" data-review="reject" data-user-id="${escapeHTML(user.id)}">Reject registration</button></div>`:""}</article>`;};
    const orgCard=(org,kind)=>`<article class="panel"><div class="request-row" style="border:0;padding-top:0"><div><span class="pill">${kind.toUpperCase()}</span><h2 style="margin:8px 0 3px">${escapeHTML(org.name)}</h2><p>@${escapeHTML(org.username||"unclaimed")} · ${escapeHTML(org.email||"No contact email")}</p></div><span class="status ${org.account_status==="approved"?"status-available":"status-requested"}">${escapeHTML(org.account_status||"No login")}</span></div><p><b>Contact:</b> ${escapeHTML(org.contact||"Not listed")} · ${escapeHTML(org.phone||"Not listed")}</p><p><b>Location:</b> ${escapeHTML(org.location||"Not listed")}${kind==="ngo"?` · <b>Service area:</b> ${escapeHTML(org.service_area||"Not listed")}`:""}</p>${safeHttpUrl(org.website)?`<p><b>Website:</b> <a href="${escapeHTML(safeHttpUrl(org.website))}" target="_blank" rel="noopener noreferrer">${escapeHTML(org.website)} ↗</a></p>`:""}${kind==="ngo"?`<p><b>Mission:</b> ${escapeHTML(org.mission||"Not provided")}</p><p><b>Current need:</b> ${escapeHTML(org.need||"Not provided")} · ${escapeHTML(org.capacity)} meal capacity</p>`:""}<small class="provenance">Profile details are information provided, not platform certification.</small></article>`;
    const sections={
      registrations:`<section><div class="section-heading"><div><h2>Pending registrations (${pending.length})</h2></div></div>${pending.map(regCard).join("")||'<div class="empty-state"><strong>No registrations waiting</strong>New applications will appear here after refresh.</div>'}${rejected.length?`<div class="section-heading"><div><h2>Recently rejected</h2></div></div>${rejected.map(regCard).join("")}`:""}</section>`,
      restaurants:`<section><div class="section-heading"><div><h2>Restaurants (${organizations.restaurants.length})</h2><p>Full restaurant records and linked account status.</p></div></div><div class="directory-grid">${organizations.restaurants.map(org=>orgCard(org,"restaurant")).join("")||'<div class="empty-state">No restaurant records.</div>'}</div></section>`,
      ngos:`<section><div class="section-heading"><div><h2>NGOs and community kitchens (${organizations.ngos.length})</h2><p>Full organisation records and linked account status.</p></div></div><div class="directory-grid">${organizations.ngos.map(org=>orgCard(org,"ngo")).join("")||'<div class="empty-state">No NGO records.</div>'}</div></section>`,
      messages:`<section><div class="section-heading"><div><h2>Contact messages (${messages.length})</h2></div></div>${messages.map(message=>`<article class="panel"><div class="request-row"><div class="request-info"><strong>${escapeHTML(message.subject)} · ${escapeHTML(message.name)}</strong><p>${escapeHTML(message.email)} · ${escapeHTML(message.created_at)}</p></div><select class="control message-status" data-message-id="${escapeHTML(message.id)}"><option ${message.status==="New"?"selected":""}>New</option><option ${message.status==="Reviewed"?"selected":""}>Reviewed</option><option ${message.status==="Closed"?"selected":""}>Closed</option></select></div><p>${escapeHTML(message.message)}</p></article>`).join("")||'<div class="empty-state">No contact messages yet.</div>'}</section>`
    };
    const content=$("#admin-content");content.innerHTML=sections.registrations;
    $$('[data-admin-tab]').forEach(button=>button.addEventListener("click",()=>{$$('[data-admin-tab]').forEach(b=>b.classList.toggle("active",b===button));content.innerHTML=sections[button.dataset.adminTab];bindAdminSection();}));
    function bindAdminSection(){
      $$('[data-review]',content).forEach(button=>button.addEventListener("click",async()=>{const action=button.dataset.review;const who=button.closest("article").querySelector("h2").textContent;if(action==="reject"&&!confirm(`Reject registration for ${who}?`))return;try{await api(`/api/admin/registrations/${encodeURIComponent(button.dataset.userId)}`,{method:"POST",body:JSON.stringify({action})});toast(`Registration ${action==="approve"?"approved":"rejected"}.`);renderAdmin();}catch(error){toast(error.message,"error");}}));
      $$(".message-status",content).forEach(select=>select.addEventListener("change",async()=>{try{await api(`/api/admin/contact-messages/${encodeURIComponent(select.dataset.messageId)}`,{method:"PATCH",body:JSON.stringify({status:select.value})});toast("Contact message updated.");}catch(error){toast(error.message,"error");}}));
    }
    bindAdminSection();$("#refresh-admin").onclick=()=>renderAdmin();
  }catch(error){$("#admin-content").innerHTML=`<div class="notice warning">${escapeHTML(error.message)}</div>`;}
}

async function openPrivateChat(donationId) {
  if (!state.auth) {
    toast("Please log in to use chat.", "error");
    return;
  }

  const isNGO = state.role === "NGO";
  const isRestaurant = state.role === "Restaurant";

  if (!isNGO && !isRestaurant) {
    toast("Only restaurants and NGOs can use chat.", "error");
    return;
  }

  let modal = document.getElementById("foodbridge-chat-modal");

  if (!modal) {
    modal = document.createElement("div");
    modal.id = "foodbridge-chat-modal";

    Object.assign(modal.style, {
      position: "fixed",
      inset: "0",
      zIndex: "99999",
      background: "rgba(0,0,0,.55)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      padding: "16px"
    });

    document.body.appendChild(modal);
  }

  modal.innerHTML = `
    <section style="
      background:white;color:#183B4E;
      width:100%;max-width:520px;
      max-height:85vh;border-radius:14px;
      padding:20px;display:flex;flex-direction:column;
      gap:12px
    ">
      <header style="display:flex;justify-content:space-between">
        <strong>Donation discussion</strong>
        <button id="fb-chat-close" type="button">Close</button>
      </header>
      <div id="fb-chat-status" role="status">Opening chat...</div>
      <div id="fb-chat-list"
        style="overflow:auto;max-height:45vh"></div>
      <div id="fb-chat-messages"
        style="overflow:auto;max-height:45vh"></div>
      <form id="fb-chat-form" style="display:none;gap:8px">
        <input id="fb-chat-input" maxlength="2000"
          placeholder="Write a message..." required
          style="flex:1;min-width:0;padding:10px">
        <button type="submit">Send</button>
      </form>
    </section>
  `;

  modal.querySelector("#fb-chat-close").onclick = () => {
    modal.remove();
  };

  modal.onclick = event => {
    if (event.target === modal) modal.remove();
  };

  try {
    let conversations = [];

    if (isNGO) {
      const result = await api(
        `/api/donations/${encodeURIComponent(donationId)}/chat`,
        { method: "POST", body: "{}" }
      );

      conversations = [result.conversation];
    } else {
      const result = await api(
        `/api/donations/${encodeURIComponent(donationId)}/chats`
      );

      conversations = result.chats || [];
    }

    const list = modal.querySelector("#fb-chat-list");
    const status = modal.querySelector("#fb-chat-status");

    if (!conversations.length) {
      status.textContent =
        "No conversations yet. An NGO must start a chat from this donation.";
      return;
    }

    list.innerHTML = "";

    conversations.forEach(conversation => {
      const button = document.createElement("button");
      button.type = "button";
      button.style.cssText =
        "display:block;width:100%;padding:12px;margin:5px 0";

      button.textContent =
        isNGO
          ? `Chat about ${conversation.donation_title || "donation"}`
          : `${conversation.ngo_name || "NGO"} — ${conversation.last_message || "Open conversation"}`;

      button.onclick = () => openConversation(conversation.id);

      list.appendChild(button);
    });

    async function openConversation(conversationId) {
      status.textContent = "Loading messages...";
      list.style.display = "none";

      const form = modal.querySelector("#fb-chat-form");
      const messagesBox = modal.querySelector("#fb-chat-messages");

      form.style.display = "flex";

      async function loadMessages() {
        const result = await api(
          `/api/chats/${encodeURIComponent(conversationId)}`
        );

        messagesBox.replaceChildren();

        (result.messages || []).forEach(message => {
          const row = document.createElement("div");
          row.style.cssText =
            "padding:8px;margin:5px 0;background:#EAF4F4;border-radius:8px";

          const sender = document.createElement("strong");
          sender.textContent = message.sender_name + ": ";

          const body = document.createElement("span");
          body.textContent = message.body;

          const time = document.createElement("small");
          time.textContent = " · " + message.created_at;

          row.append(sender, body, time);
          messagesBox.appendChild(row);
        });

        status.textContent = result.messages?.length
          ? "Conversation opened"
          : "No messages yet. Start the conversation!";

        messagesBox.scrollTop = messagesBox.scrollHeight;
      }

      try {
        await loadMessages();
      } catch (error) {
        status.textContent = error.message;
        return;
      }

      form.onsubmit = async event => {
        event.preventDefault();

        const input = modal.querySelector("#fb-chat-input");
        const sendButton = form.querySelector("button");
        const body = input.value.trim();

        if (!body) return;

        sendButton.disabled = true;

        try {
          await api(
            `/api/chats/${encodeURIComponent(conversationId)}/messages`,
            {
              method: "POST",
              body: JSON.stringify({ body })
            }
          );

          input.value = "";
          await loadMessages();
        } catch (error) {
          status.textContent = error.message;
        } finally {
          sendButton.disabled = false;
        }
      };
    }
  } catch (error) {
    modal.querySelector("#fb-chat-status").textContent =
      "Could not open chat: " + error.message;
  }
}
let globalEventsBound = false;
function bindGlobalEvents(){
  if (globalEventsBound) return;
  globalEventsBound = true;
  document.addEventListener("click",event=>{
    const chatButton = event.target.closest("[data-open-chat]");
    if (chatButton) {
       event.preventDefault();
       const donationId = chatButton.dataset.donationId;
       if (donationId) {
         openPrivateChat(donationId);
       }
     return;
} 
    const accountButton = event.target.closest("#account-action");
    if (accountButton?.dataset.logout === "true") { event.preventDefault(); logout(); return; }
    const pageButton = event.target.closest("[data-page]");
    if (pageButton) { event.preventDefault(); setPage(pageButton.dataset.page); return; }
    const detailButton = event.target.closest("[data-detail]");
    if (detailButton) { event.preventDefault(); goDetail(detailButton.dataset.detail); return; }
  });
  $("#menu-toggle")?.addEventListener("click", () => {
    const nav = $("#mobile-nav");
    if (!nav) return;
    nav.classList.toggle("open");
    $("#menu-toggle")?.setAttribute("aria-expanded", String(nav.classList.contains("open")));
  });
}
async function init() {
  bindGlobalEvents();
  try {
    await refreshBootstrap();
    try {
      const auth = await api("/api/auth/me");
      state.auth = auth.user || null;
    } catch (error) {
      // An unauthenticated visitor may legitimately receive HTTP 401 from /me.
      if (error.status === 401) state.auth = null;
      else throw error;
    }
    selectInitialProfiles();
    updateRoleControls();
    setPage("home");
  } catch (error) {
    const view = $("#view-home");
    if (view) view.innerHTML = `<div class="empty-state backend-error"><strong>FoodBridge couldn't connect to its Python backend</strong><p>${escapeHTML(error.message)}</p><p>Start the server with <code>python app.py</code>, then reload this page.</p><button class="button button-secondary" id="retry-app" type="button">Retry connection</button></div>`;
    $("#retry-app")?.addEventListener("click", init);
  }
}
init();

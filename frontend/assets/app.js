const $ = (id) => document.getElementById(id);

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: opts.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = { raw: text }; }
  if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
  return data;
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((el) => el.classList.toggle("on", el.id === `tab-${name}`));
  document.querySelectorAll(".nav").forEach((el) => el.classList.toggle("on", el.dataset.tab === name));
  const titles = {
    dashboard: ["Dashboard", "Overnight browser apply loop"],
    profile: ["Profile", "Used to fill career-page forms"],
    companies: ["Companies", "Career pages the agent will open"],
    paste: ["Paste URLs", "ChatGPT / Naukri / LinkedIn job links"],
    jobs: ["Jobs", "Discovered from those pages"],
    apps: ["Applications", "What the browser actually did"],
    logs: ["Logs", "Cycle output"],
  };
  $("page-title").textContent = titles[name][0];
  $("page-sub").textContent = titles[name][1];
}

document.querySelectorAll(".nav").forEach((btn) => {
  btn.addEventListener("click", () => showTab(btn.dataset.tab));
});

function setPill(agent) {
  const pill = $("agent-pill");
  const phase = agent?.phase || "idle";
  const running = !!agent?.running;
  pill.textContent = running ? phase : phase;
  pill.classList.toggle("live", running);
}

async function loadDashboard() {
  const [settings, jobs, apps] = await Promise.all([
    api("/api/settings"),
    api("/api/jobs?limit=200"),
    api("/api/applications?limit=20"),
  ]);
  setPill(settings.agent);
  $("stat-overnight").textContent = settings.overnight_enabled ? "on" : "off";
  $("stat-mode").textContent = settings.dry_run ? "dry-run" : (settings.auto_submit ? "auto-submit" : "fill only");
  $("stat-queued").textContent = jobs.filter((j) => j.status === "queued" || j.status === "new").length;
  $("stat-tonight").textContent = settings.applied_tonight || 0;
  $("overnight").checked = !!settings.overnight_enabled;
  $("dryrun").checked = !!settings.dry_run;
  $("autosubmit").checked = !!settings.auto_submit;
  $("minscore").value = settings.min_score;
  $("maxnight").value = settings.max_per_night;
  $("interval").value = settings.interval_minutes;
  return { jobs, apps };
}

async function loadProfile() {
  const p = await api("/api/profile");
  ["full_name", "email", "phone", "linkedin", "github", "website", "location", "skills", "summary"].forEach((k) => {
    $(k).value = p[k] || "";
  });
  $("resume-name").textContent = p.resume_path ? p.resume_path.split("/").pop() : "No resume uploaded";
}

async function loadCompanies() {
  const rows = await api("/api/companies");
  $("co-body").innerHTML = rows.map((c) => `
    <tr>
      <td>${esc(c.name)}</td>
      <td><a href="${esc(c.career_url)}" target="_blank" rel="noreferrer">${esc(c.career_url)}</a></td>
      <td><input type="checkbox" data-cid="${c.id}" class="co-en" ${c.enabled ? "checked" : ""} /></td>
    </tr>
  `).join("") || `<tr><td colspan="3">No career pages yet.</td></tr>`;
  document.querySelectorAll(".co-en").forEach((box) => {
    box.addEventListener("change", async () => {
      await api(`/api/companies/${box.dataset.cid}`, {
        method: "PATCH",
        body: JSON.stringify({ enabled: box.checked }),
      });
    });
  });
}

async function loadJobs() {
  const rows = await api("/api/jobs");
  const useful = rows.filter((j) => j.company_name === "ChatGPT" || j.company_name === "Pasted" || j.status === "applied" || j.status === "submitted");
  const show = useful.length ? useful : rows.slice(0, 30);
  $("job-body").innerHTML = show.map((j) => `
    <tr>
      <td>${j.match_score}</td>
      <td><a href="${esc(j.url)}" target="_blank" rel="noreferrer">${esc(j.title)}</a></td>
      <td>${esc(j.company_name || "")}</td>
      <td>${esc(j.status)}</td>
    </tr>
  `).join("") || `<tr><td colspan="4">No ChatGPT jobs yet. Send a job URL to the ingest endpoint.</td></tr>`;
}

async function loadApps() {
  const rows = await api("/api/applications");
  $("app-body").innerHTML = rows.map((a) => `
    <tr>
      <td>${esc(a.started_at || "")}</td>
      <td>${esc(a.title || "")}<br><a href="${esc(a.url || "")}" target="_blank" rel="noreferrer">${esc(a.url || "")}</a></td>
      <td>${esc(a.status)}</td>
      <td>${esc(a.notes || "")}</td>
      <td>${a.screenshot_path ? `<a href="/api/screenshot/${a.job_id}" target="_blank">view</a>` : "none"}</td>
    </tr>
  `).join("") || `<tr><td colspan="5">No attempts yet.</td></tr>`;
}

async function loadLogs() {
  const rows = await api("/api/logs");
  $("log-box").textContent = rows.map((l) => `${l.ts} [${l.level}] ${l.message}`).join("\n");
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

$("save-settings").addEventListener("click", async () => {
  $("run-msg").textContent = "Saving...";
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        overnight_enabled: $("overnight").checked,
        dry_run: $("dryrun").checked,
        auto_submit: $("autosubmit").checked,
        min_score: Number($("minscore").value),
        max_per_night: Number($("maxnight").value),
        interval_minutes: Number($("interval").value),
      }),
    });
    await loadDashboard();
    $("run-msg").textContent = "Settings saved.";
  } catch (e) {
    $("run-msg").textContent = e.message;
  }
});

$("run-now").addEventListener("click", async () => {
  $("run-msg").textContent = "Running cycle in headless Chromium. This can take a few minutes...";
  try {
    const r = await api("/api/run", { method: "POST" });
    $("run-msg").textContent = r.ok
      ? "Cycle started in Chromium. Watch Applications and Logs."
      : (r.error || "Cycle failed");
    await refresh();
  } catch (e) {
    $("run-msg").textContent = e.message;
  }
});

$("stop-now").addEventListener("click", async () => {
  await api("/api/stop", { method: "POST" });
  $("run-msg").textContent = "Stop requested.";
  await loadDashboard();
});

$("save-profile").addEventListener("click", async () => {
  await api("/api/profile", {
    method: "POST",
    body: JSON.stringify({
      full_name: $("full_name").value,
      email: $("email").value,
      phone: $("phone").value,
      linkedin: $("linkedin").value,
      github: $("github").value,
      website: $("website").value,
      location: $("location").value,
      skills: $("skills").value,
      summary: $("summary").value,
    }),
  });
  alert("Profile saved");
});

$("resume").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  await fetch("/api/profile/resume", { method: "POST", body: fd });
  await loadProfile();
});

$("add-company").addEventListener("click", async () => {
  const name = $("co-name").value.trim();
  const career_url = $("co-url").value.trim();
  if (!name || !career_url) return;
  await api("/api/companies", { method: "POST", body: JSON.stringify({ name, career_url }) });
  $("co-name").value = "";
  $("co-url").value = "";
  await loadCompanies();
});

async function pasteJobs(applyNow) {
  const text = $("paste-urls").value.trim();
  if (!text) {
    $("paste-msg").textContent = "Paste at least one http(s) job URL.";
    return;
  }
  $("paste-msg").textContent = "Saving...";
  try {
    const r = await api("/api/jobs/paste", {
      method: "POST",
      body: JSON.stringify({ text, apply_now: applyNow }),
    });
    $("paste-msg").textContent = `Queued ${r.added.length} jobs` +
      (r.skipped.length ? `, skipped ${r.skipped.length} duplicates` : "") +
      (r.started ? ". Cycle started." : ".");
    $("paste-urls").value = "";
    await refresh();
  } catch (e) {
    $("paste-msg").textContent = e.message;
  }
}

$("paste-save").addEventListener("click", () => pasteJobs(false));
$("paste-run").addEventListener("click", () => pasteJobs(true));

function ingestBase() {
  return `${location.origin}`;
}

function chatgptPrompt(fullUrl) {
  const health = `${ingestBase()}/health`;
  return `You are sending jobs to an automated HTTP API. Do not open this as a webpage. Do not wait for JavaScript. Use a plain HTTPS GET.

Health check first:
GET ${health}

Whenever you find a job that fits Mayank Mittal (12+ years QA Automation / SDET, Java, Selenium, Playwright, Appium, REST Assured, NCR or remote India, 40+ LPA), immediately send the real application URL.

GET ${fullUrl}?url=URL_ENCODED_JOB_URL

Example:
${fullUrl}?url=https%3A%2F%2Fjobs.lever.co%2FSprinto%2F151dd41f-692b-4c2f-a599-f5301e53b84b

Use Greenhouse, Lever, Ashby, Workday, or company /jobs links. Do not use LinkedIn Easy Apply. Expect JSON: {"ok":true,"queued":1,"added":[...],"skipped":[],"started":true}`;
}

async function loadIngest() {
  const info = await api("/api/ingest");
  const full = ingestBase() + info.path;
  $("ingest-url").value = full;
  $("chatgpt-prompt").textContent = chatgptPrompt(full);
}

$("copy-ingest").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("ingest-url").value);
    $("ingest-msg").textContent = "URL copied.";
  } catch {
    $("ingest-url").select();
    $("ingest-msg").textContent = "Select and copy the URL.";
  }
});

$("copy-prompt").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("chatgpt-prompt").textContent);
    $("ingest-msg").textContent = "ChatGPT prompt copied.";
  } catch {
    $("ingest-msg").textContent = "Copy the prompt from the box below.";
  }
});

$("rotate-ingest").addEventListener("click", async () => {
  if (!confirm("Rotate token? The old ChatGPT URL will stop working.")) return;
  await api("/api/ingest/rotate", { method: "POST" });
  await loadIngest();
  $("ingest-msg").textContent = "Token rotated. Update ChatGPT with the new URL.";
});

async function refresh() {
  await Promise.all([loadDashboard(), loadProfile(), loadCompanies(), loadJobs(), loadApps(), loadLogs(), loadIngest()]);
}

refresh().catch((e) => { $("run-msg").textContent = e.message; });
setInterval(() => {
  loadDashboard().catch(() => {});
  loadLogs().catch(() => {});
}, 8000);

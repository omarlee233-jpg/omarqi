// ================================================================
// OMARQI — Frontend Logic
// ================================================================

let currentVideoId = null;
let currentMoments = [];
let currentSessionId = null;

// ---- Init ----
document.addEventListener("DOMContentLoaded", () => {
    const savedKey = localStorage.getItem("yt_api_key");
    if (savedKey) {
        document.getElementById("apiKeyInput").value = savedKey;
    }

    document.getElementById("urlInput").addEventListener("keydown", (e) => {
        if (e.key === "Enter") analyzeVideo();
    });
});

// ---- Config Modal ----
function toggleConfig() {
    const modal = document.getElementById("configModal");
    modal.classList.toggle("hidden");
}

function saveApiKey() {
    const key = document.getElementById("apiKeyInput").value.trim();
    const status = document.getElementById("apiKeyStatus");
    if (key) {
        localStorage.setItem("yt_api_key", key);
        status.textContent = "API key saved!";
    } else {
        localStorage.removeItem("yt_api_key");
        status.textContent = "API key cleared";
    }
    setTimeout(() => { status.textContent = ""; }, 3000);
}

// ---- Format Time ----
function formatTime(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, "0")}`;
}

// ---- Analyze ----
async function analyzeVideo() {
    const url = document.getElementById("urlInput").value.trim();
    if (!url) {
        showError("analyzeError", "analyzeErrorText", "Please enter a YouTube URL");
        return;
    }

    if (!url.includes("youtube.com") && !url.includes("youtu.be")) {
        showError("analyzeError", "analyzeErrorText", "Please enter a valid YouTube URL");
        return;
    }

    const apiKey = localStorage.getItem("yt_api_key") || "";

    setLoading("analyze", true);
    hideEl("analyzeError");
    hideEl("resultsSection");
    hideEl("downloadSection");
    hideEl("progressSection");

    try {
        const resp = await fetch("/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url, api_key: apiKey }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            showError("analyzeError", "analyzeErrorText", data.error || "Analysis failed");
            return;
        }

        currentVideoId = data.video_id;
        currentMoments = data.moments;
        renderResults(data);
    } catch (err) {
        showError("analyzeError", "analyzeErrorText", `Request failed: ${err.message}`);
    } finally {
        setLoading("analyze", false);
    }
}

// ---- Render Results ----
function renderResults(data) {
    const { metadata, moments } = data;

    // Metadata
    document.getElementById("metaThumb").src = metadata.thumbnail || "";
    document.getElementById("metaTitle").textContent = metadata.title || "Unknown Title";
    document.getElementById("metaChannel").textContent = metadata.channel || "";

    const viewsEl = document.getElementById("metaViews");
    if (metadata.views && metadata.views !== "N/A") {
        viewsEl.textContent = `${Number(metadata.views).toLocaleString()} views`;
    } else {
        viewsEl.textContent = "";
    }

    const warnEl = document.getElementById("metaWarning");
    if (metadata.warning) {
        warnEl.textContent = metadata.warning;
        warnEl.classList.remove("hidden");
    } else {
        warnEl.classList.add("hidden");
    }

    // Moments
    const container = document.getElementById("momentsContainer");
    container.innerHTML = "";

    moments.forEach((m, i) => {
        const scoreClass = m.score >= 60 ? "score-high" : m.score >= 30 ? "score-medium" : "score-low";
        const h = m.heuristics || {};

        const card = document.createElement("div");
        card.className = "moment-card";
        card.innerHTML = `
            <div class="moment-top">
                <input type="checkbox" class="moment-checkbox" data-index="${i}" checked />
                <div class="moment-number">${i + 1}</div>
                <div class="moment-info">
                    <div class="moment-label">${m.label}</div>
                    <div class="moment-time">${formatTime(m.start)} — ${formatTime(m.end)}  (${Math.round(m.duration)}s)</div>
                </div>
                <div class="score-pill ${scoreClass}">${m.score}%</div>
            </div>
            <div class="moment-body">
                <div class="moment-reason">${m.reason}</div>
                <div class="moment-excerpt">"${m.excerpt}"</div>
                <div class="heuristic-row">
                    ${hTag("KW", h.keywords, "h-fill-kw")}
                    ${hTag("Q", h.questions, "h-fill-q")}
                    ${hTag("EM", h.emotion, "h-fill-em")}
                    ${hTag("PC", h.pacing, "h-fill-pc")}
                    ${hTag("HK", h.hooks, "h-fill-hk")}
                </div>
            </div>
        `;
        container.appendChild(card);
    });

    showEl("resultsSection");

    // Smooth scroll to results
    document.getElementById("resultsSection").scrollIntoView({ behavior: "smooth", block: "start" });
}

function hTag(label, value, fillClass) {
    const pct = Math.round((value || 0) * 100);
    return `<div class="h-tag">
        <span>${label}</span>
        <div class="h-bar"><div class="h-fill ${fillClass}" style="width:${pct}%"></div></div>
    </div>`;
}

// ---- Select All ----
function toggleSelectAll() {
    const checked = document.getElementById("selectAllCheck").checked;
    document.querySelectorAll(".moment-checkbox").forEach(cb => cb.checked = checked);
}

// ---- Extract ----
async function extractClips() {
    const checkboxes = document.querySelectorAll(".moment-checkbox:checked");
    if (checkboxes.length === 0) {
        showError("extractError", "extractErrorText", "Select at least one clip to extract");
        return;
    }

    const selectedMoments = [];
    checkboxes.forEach(cb => {
        const idx = parseInt(cb.dataset.index);
        selectedMoments.push(currentMoments[idx]);
    });

    setLoading("extract", true);
    hideEl("extractError");
    showEl("progressSection");
    hideEl("downloadSection");

    // Animate processing steps
    setStep("stepDownload", "active");
    setStep("stepCutting", "");
    setStep("stepFinishing", "");

    try {
        const resp = await fetch("/extract", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ video_id: currentVideoId, moments: selectedMoments }),
        });

        const data = await resp.json();

        if (!resp.ok) {
            showError("extractError", "extractErrorText", data.error || "Extraction failed");
            hideEl("progressSection");
            return;
        }

        // Show completion
        setStep("stepDownload", "done");
        setStep("stepCutting", "done");
        setStep("stepFinishing", "done");

        setTimeout(() => {
            currentSessionId = data.session_id;
            renderDownloads(data);
            hideEl("progressSection");
        }, 500);

    } catch (err) {
        showError("extractError", "extractErrorText", `Extraction failed: ${err.message}`);
        hideEl("progressSection");
    } finally {
        setLoading("extract", false);
    }
}

function setStep(id, state) {
    const el = document.getElementById(id);
    el.className = "p-step";
    if (state) el.classList.add(state);
}

// ---- Render Downloads ----
function renderDownloads(data) {
    const container = document.getElementById("clipsContainer");
    container.innerHTML = "";

    data.clips.forEach(clip => {
        const url = `/download/${data.session_id}/${clip.filename}`;
        const card = document.createElement("div");
        card.className = "clip-card";
        card.innerHTML = `
            <div class="clip-info">
                <div class="clip-icon-box">&#127916;</div>
                <div>
                    <div class="clip-name">${clip.label}</div>
                    <div class="clip-time">${formatTime(clip.start)} — ${formatTime(clip.end)}</div>
                </div>
            </div>
            <a href="${url}" class="btn-download" download>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="7 10 12 15 17 10"></polyline>
                    <line x1="12" y1="15" x2="12" y2="3"></line>
                </svg>
                Download
            </a>
        `;
        container.appendChild(card);
    });

    showEl("downloadSection");
    document.getElementById("downloadSection").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---- Download All ----
function downloadAll() {
    const links = document.querySelectorAll("#clipsContainer .btn-download");
    links.forEach((link, i) => {
        setTimeout(() => link.click(), i * 500);
    });
}

// ---- Cleanup ----
async function cleanupSession() {
    if (!currentSessionId) return;
    try {
        await fetch(`/cleanup/${currentSessionId}`, { method: "POST" });
        hideEl("downloadSection");
        currentSessionId = null;
    } catch (err) {
        console.error("Cleanup failed:", err);
    }
}

// ---- UI Helpers ----
function setLoading(prefix, loading) {
    const btn = document.getElementById(`${prefix}Btn`);
    const text = document.getElementById(`${prefix}BtnText`);
    const spinner = document.getElementById(`${prefix}Spinner`);

    btn.disabled = loading;
    if (loading) {
        text.textContent = prefix === "analyze" ? "Analyzing..." : "Extracting...";
        spinner.classList.remove("hidden");
    } else {
        text.textContent = prefix === "analyze" ? "Generate Clips" : "Extract & Download Clips";
        spinner.classList.add("hidden");
    }
}

function showError(containerId, textId, msg) {
    document.getElementById(textId).textContent = msg;
    document.getElementById(containerId).classList.remove("hidden");
}

function showEl(id) { document.getElementById(id).classList.remove("hidden"); }
function hideEl(id) { document.getElementById(id).classList.add("hidden"); }

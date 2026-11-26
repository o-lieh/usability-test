// Participant-side runner: screen/mic recording with chunked upload, plus the task panel.
(() => {
  "use strict";

  const cfg = JSON.parse(document.getElementById("cfg").textContent);
  const $ = (id) => document.getElementById(id);
  const tasks = cfg.tasks;
  const results = cfg.results; // [{status, comment}] per task, mutated locally
  const total = tasks.length;
  const fa = (n) => Number(n).toLocaleString("fa-IR");
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  const md = navigator.mediaDevices;
  const screenSupported = !!(md && md.getDisplayMedia);
  const micSupported = !!(md && md.getUserMedia);
  const recorderSupported = typeof window.MediaRecorder !== "undefined";

  let idx = Math.min(cfg.currentTask || 0, total - 1);
  let inRun = false;
  let dirty = false; // unsaved comment text
  let saveTimer = null;
  const consent = { screen: false, audio: false };
  const rec = { recorder: null, display: null, mic: null, queue: Promise.resolve() };

  // ---------------------------------------------------------------- server calls
  let lastError = ""; // reason of the last failed call, shown to the participant
  async function postJson(path, body, retries = 3) {
    for (let i = 0; i <= retries; i++) {
      try {
        // Django routes end with "/"; without it Django redirects and browsers turn the POST into a GET.
        const r = await fetch(`/api/${cfg.token}/${path}/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body || {}),
        });
        if (r.ok) return await r.json();
        lastError = `HTTP ${r.status}`;
        if (r.status >= 400 && r.status < 500) return null; // not retryable
      } catch (_) {
        lastError = "network";
      }
      await sleep(500 * 2 ** i);
    }
    return null;
  }

  async function uploadChunk(part, seq, blob, mime) {
    // Retry with backoff (about 75 s total) so short network drops do not lose data.
    for (let attempt = 0; attempt < 8; attempt++) {
      try {
        const r = await fetch(`/api/${cfg.token}/recording/${part}/chunk/?seq=${seq}`, {
          method: "POST",
          headers: { "Content-Type": mime || "application/octet-stream" },
          body: blob,
        });
        if (r.ok) return true;
        if (r.status === 409 || r.status === 403 || r.status === 404) return false; // retrying cannot help
      } catch (_) {
        /* network error: retry below */
      }
      await sleep(Math.min(1000 * 2 ** attempt, 15000));
    }
    return false;
  }

  // Chunks are uploaded strictly one after another so the server can append them in order.
  function enqueueChunk(part, seq, blob, mime) {
    rec.queue = rec.queue.then(() => uploadChunk(part, seq, blob, mime));
  }

  // ---------------------------------------------------------------- recording
  function pickMime(hasVideo) {
    const candidates = hasVideo
      ? ["video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm", "video/mp4"]
      : ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"];
    return candidates.find((t) => recorderSupported && MediaRecorder.isTypeSupported(t)) || "";
  }

  // Must run inside a click handler: browsers require a user gesture for screen capture.
  async function acquireMedia() {
    let display = null;
    let mic = null;
    if (screenSupported && consent.screen) {
      display = await md.getDisplayMedia({
        video: { frameRate: { ideal: 10, max: 15 } },
        audio: false,
        preferCurrentTab: true, // Chrome hint; ignored elsewhere
        selfBrowserSurface: "include",
      });
    }
    if (consent.audio && micSupported) {
      if (rec.mic && rec.mic.getAudioTracks().some((t) => t.readyState === "live")) {
        mic = rec.mic; // reuse the microphone after a resume
      } else {
        try {
          mic = await md.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        } catch (_) {
          postJson("event", { type: "mic_denied" });
        }
      }
    }
    return { display, mic };
  }

  function stopStream(stream) {
    if (stream) stream.getTracks().forEach((t) => t.stop());
  }

  async function launchRecorder({ display, mic }) {
    const tracks = [...(display ? display.getVideoTracks() : []), ...(mic ? mic.getAudioTracks() : [])];
    if (!tracks.length || !recorderSupported) {
      stopStream(display);
      rec.mic = mic;
      return false; // nothing to record; the test still runs
    }
    const hasVideo = !!display;
    const mime = pickMime(hasVideo);
    const started = await postJson("recording/start", { mime, has_video: hasVideo, has_audio: !!mic });
    if (!started) {
      stopStream(display);
      throw new Error("server");
    }
    const part = started.part;
    let seq = 0;
    const recorder = new MediaRecorder(new MediaStream(tracks), {
      ...(mime ? { mimeType: mime } : {}),
      videoBitsPerSecond: 1000000,
      audioBitsPerSecond: 64000,
    });
    recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) enqueueChunk(part, seq++, e.data, mime);
    };
    // "Stop sharing" in the browser UI ends the video track.
    const watched = display ? display.getVideoTracks()[0] : mic.getAudioTracks()[0];
    watched.addEventListener("ended", () => {
      if (rec.recorder === recorder) onCaptureLost();
    });
    rec.recorder = recorder;
    rec.display = display;
    rec.mic = mic;
    recorder.start(10000); // one chunk every 10 s
    return true;
  }

  async function stopRecording({ keepMic = false } = {}) {
    const r = rec.recorder;
    rec.recorder = null;
    if (r && r.state !== "inactive") {
      // The final dataavailable event fires before "stop", so the last chunk is queued.
      await new Promise((resolve) => {
        r.addEventListener("stop", resolve, { once: true });
        r.stop();
      });
      postJson("event", { type: "recording_stop", task_index: idx });
    }
    stopStream(rec.display);
    rec.display = null;
    if (!keepMic) {
      stopStream(rec.mic);
      rec.mic = null;
    }
    await rec.queue; // wait until every chunk is uploaded
  }

  async function onCaptureLost() {
    postJson("event", { type: "capture_lost", task_index: idx });
    await stopRecording({ keepMic: true });
    if (inRun) $("overlay-lost").hidden = false;
  }

  $("btn-resume").addEventListener("click", async () => {
    const err = $("lost-error");
    err.hidden = true;
    try {
      const media = await acquireMedia();
      await launchRecorder(media);
      $("overlay-lost").hidden = true;
    } catch (e) {
      err.textContent = "اشتراک‌گذاری فعال نشد. دوباره تلاش کنید و گزینه‌ی همین زبانه را تأیید کنید.";
      err.hidden = false;
    }
  });

  // ---------------------------------------------------------------- welcome stage
  if (!screenSupported) {
    $("no-screen-note").hidden = false;
    $("consent-screen-row").hidden = true;
  }
  $("consent-screen").checked = !!cfg.consentScreen;
  $("consent-audio").checked = !!cfg.consentAudio;

  function showWelcomeError(msg) {
    const el = $("welcome-error");
    el.textContent = msg;
    el.hidden = !msg;
  }

  $("btn-start").addEventListener("click", async () => {
    const btn = $("btn-start");
    showWelcomeError("");
    consent.screen = screenSupported && $("consent-screen").checked;
    consent.audio = $("consent-audio").checked;
    if (screenSupported && !consent.screen) {
      showWelcomeError("برای شروع، باید با ضبط تصویر همین زبانه موافقت کنید.");
      return;
    }
    btn.disabled = true;
    let media;
    try {
      media = await acquireMedia(); // first await keeps the click's user gesture valid
    } catch (_) {
      showWelcomeError("اشتراک‌گذاری صفحه تأیید نشد. دوباره دکمه را بزنید و در پنجره‌ی مرورگر، همین زبانه را انتخاب و تأیید کنید.");
      btn.disabled = false;
      return;
    }
    const begun = await postJson("begin", { consent_screen: consent.screen, consent_audio: consent.audio });
    if (!begun) {
      stopStream(media.display);
      stopStream(media.mic);
      showWelcomeError(`ارتباط با سرور ناموفق بود (${lastError}). اینترنت را بررسی و دوباره تلاش کنید.`);
      btn.disabled = false;
      return;
    }
    try {
      await launchRecorder(media);
    } catch (_) {
      showWelcomeError("شروع ضبط ممکن نشد. دوباره تلاش کنید.");
      btn.disabled = false;
      return;
    }
    enterRun();
  });

  function enterRun() {
    $("stage-welcome").hidden = true;
    $("stage-run").hidden = false;
    $("app-frame").src = cfg.appUrl; // load the app only after recording has started
    inRun = true;
    buildDots();
    showTask(idx);
  }

  window.addEventListener("beforeunload", (e) => {
    if (inRun) {
      e.preventDefault();
      e.returnValue = "";
    }
  });

  // ---------------------------------------------------------------- task panel
  function buildDots() {
    const box = $("dots");
    box.innerHTML = "";
    for (let i = 0; i < total; i++) {
      const b = document.createElement("button");
      b.type = "button";
      b.title = `کار ${fa(i + 1)}`;
      b.setAttribute("aria-label", `رفتن به کار ${fa(i + 1)}`);
      b.addEventListener("click", () => goTo(i));
      box.appendChild(b);
    }
  }

  function render() {
    $("counter").textContent = `کار ${fa(idx + 1)} از ${fa(total)}`;
    $("task-text").textContent = tasks[idx];
    const r = results[idx];
    $("btn-done").classList.toggle("active", r.status === "done");
    $("btn-failed").classList.toggle("active", r.status === "failed");
    $("btn-prev").disabled = idx === 0;
    $("btn-next").textContent = idx === total - 1 ? "پایان آزمون" : "کار بعدی";
    if ($("comment").value !== r.comment) $("comment").value = r.comment;
    [...$("dots").children].forEach((b, i) => {
      b.className = `${results[i].status !== "pending" ? results[i].status : ""} ${i === idx ? "current" : ""}`.trim();
    });
  }

  function showTask(i) {
    idx = i;
    render();
    $("save-state").textContent = "";
    postJson("task", { index: idx, shown: true });
  }

  async function flushComment() {
    clearTimeout(saveTimer);
    saveTimer = null;
    if (!dirty) return;
    dirty = false;
    const i = idx; // captured synchronously, before any navigation changes idx
    const saved = await postJson("task", { index: i, comment: results[i].comment });
    $("save-state").textContent = saved ? "ذخیره شد ✓" : "ذخیره نشد؛ هنگام رفتن به کار بعدی دوباره تلاش می‌شود.";
    if (!saved) dirty = true;
  }

  $("comment").addEventListener("input", () => {
    results[idx].comment = $("comment").value;
    dirty = true;
    $("save-state").textContent = "…";
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flushComment, 700);
  });

  function setStatus(status) {
    const r = results[idx];
    r.status = r.status === status ? "pending" : status; // clicking again undoes it
    render();
    postJson("task", { index: idx, status: r.status });
  }
  $("btn-done").addEventListener("click", () => setStatus("done"));
  $("btn-failed").addEventListener("click", () => setStatus("failed"));

  async function goTo(i) {
    if (i === idx || i < 0 || i >= total) return;
    await flushComment();
    showTask(i);
  }
  $("btn-prev").addEventListener("click", () => goTo(idx - 1));
  $("btn-next").addEventListener("click", () => (idx === total - 1 ? finishRun() : goTo(idx + 1)));

  // ---------------------------------------------------------------- finish
  async function finishRun() {
    await flushComment();
    const pending = results.filter((r) => r.status === "pending").length;
    if (pending && !confirm(`${fa(pending)} کار هنوز علامت نخورده است. آیا می‌خواهید آزمون را پایان دهید؟`)) return;
    inRun = false;
    $("stage-run").hidden = true;
    $("overlay-lost").hidden = true;
    $("stage-sending").hidden = false;
    await stopRecording(); // flushes the last chunk and waits for uploads
    $("stage-sending").hidden = true;
    $("stage-finish").hidden = false;
  }

  let rating = null;
  $("rating").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-value]");
    if (!b) return;
    rating = Number(b.dataset.value);
    [...$("rating").children].forEach((x) => x.setAttribute("aria-checked", x === b ? "true" : "false"));
  });

  $("btn-submit").addEventListener("click", async () => {
    const err = $("finish-error");
    err.hidden = true;
    if (!rating) {
      err.textContent = "لطفاً یک امتیاز انتخاب کنید.";
      err.hidden = false;
      return;
    }
    const done = await postJson("finish", { rating, suggestions: $("suggestions").value });
    if (!done) {
      err.textContent = "ارسال نشد. اینترنت را بررسی و دوباره تلاش کنید.";
      err.hidden = false;
      return;
    }
    $("stage-finish").hidden = true;
    $("stage-thanks").hidden = false;
  });
})();

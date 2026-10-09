(() => {
  const menuToggle = document.getElementById("menuToggle");
  const sidebar = document.getElementById("sidebar");
  if (menuToggle && sidebar) menuToggle.addEventListener("click", () => sidebar.classList.toggle("open"));

  // Best-effort thumbnails generated from the first video frames by the browser.
  const items = Array.from(document.querySelectorAll("[data-thumbnail-src]"));
  let next = 0, active = 0;
  const limit = 2;
  const formatTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) return "";
    seconds = Math.floor(seconds);
    const h = Math.floor(seconds / 3600), m = Math.floor(seconds % 3600 / 60), s = seconds % 60;
    return h ? `${h}:${String(m).padStart(2,"0")}:${String(s).padStart(2,"0")}` : `${m}:${String(s).padStart(2,"0")}`;
  };
  function schedule() {
    while (active < limit && next < items.length) {
      const item = items[next++]; active++;
      thumb(item).finally(() => { active--; schedule(); });
    }
  }
  function thumb(item) {
    return new Promise(resolve => {
      const video = document.createElement("video");
      video.muted = true; video.playsInline = true; video.preload = "metadata";
      video.crossOrigin = "anonymous";
      video.style.cssText = "position:fixed;left:-10000px;top:0;width:320px;height:180px";
      document.body.appendChild(video);
      let done = false;
      const timer = setTimeout(finish, 9000);
      function finish() {
        if (done) return; done = true; clearTimeout(timer);
        video.removeAttribute("src"); try { video.load(); } catch (_) {}
        video.remove(); resolve();
      }
      function snapshot() {
        if (done) return;
        try {
          if (video.readyState < 2 || !video.videoWidth || !video.videoHeight) { finish(); return; }
          const canvas = document.createElement("canvas");
          canvas.width = 480; canvas.height = Math.max(1, Math.round(480 * video.videoHeight / video.videoWidth));
          canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
          const img = document.createElement("img"); img.alt = "Miniatura filmu"; img.loading = "lazy";
          img.src = canvas.toDataURL("image/jpeg", .8);
          const placeholder = item.querySelector(".thumb-placeholder"); if (placeholder) placeholder.remove();
          item.prepend(img);
        } catch (_) { /* CORS, kodek lub format mogą zablokować miniaturę. */ }
        finish();
      }
      video.addEventListener("loadedmetadata", () => {
        const duration = item.querySelector(".video-duration");
        if (duration && Number.isFinite(video.duration)) duration.textContent = formatTime(video.duration);
        try { video.currentTime = Number.isFinite(video.duration) && video.duration > 1 ? Math.min(1.2, video.duration / 5) : 0; }
        catch (_) { snapshot(); }
      }, {once:true});
      video.addEventListener("seeked", snapshot, {once:true});
      video.addEventListener("loadeddata", () => { if (video.currentTime === 0) snapshot(); }, {once:true});
      video.addEventListener("error", finish, {once:true});
      video.src = item.dataset.thumbnailSrc; video.load();
    });
  }
  schedule();
})();

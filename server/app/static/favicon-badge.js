(function () {
  "use strict";

  const SIZE = 64;
  const DESIGN = 64;
  const SCALE = SIZE / DESIGN;
  const BADGE_RANKED = [90, 158, 255];
  const BADGE_CASUAL = [87, 192, 125];

  let linkEl = null;
  let badges = { ranked: false, casual: false };
  let markImage = null;
  let markReady = false;
  const markWaiters = [];

  function rgb([r, g, b], alpha = 255) {
    return alpha === 255 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${alpha / 255})`;
  }

  function ensureLink() {
    if (linkEl) return linkEl;
    linkEl = document.querySelector('link[rel="icon"][data-asobby]');
    if (!linkEl) {
      linkEl = document.createElement("link");
      linkEl.rel = "icon";
      linkEl.type = "image/png";
      linkEl.setAttribute("data-asobby", "1");
      document.head.appendChild(linkEl);
    }
    return linkEl;
  }

  function whenMarkReady(fn) {
    if (markReady) {
      fn();
      return;
    }
    markWaiters.push(fn);
  }

  function loadMarkImage() {
    const img = new Image();
    img.onload = () => {
      markImage = img;
      markReady = true;
      const waiters = markWaiters.splice(0);
      for (const fn of waiters) fn();
      applyBadges(badges);
    };
    img.onerror = () => {
      markReady = true;
      applyBadges(badges);
    };
    img.src = "/static/favicon-64.png";
  }

  function drawMark(ctx) {
    if (markImage) {
      ctx.drawImage(markImage, 0, 0, SIZE, SIZE);
      return;
    }
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, SIZE, SIZE);
  }

  function drawBadgeDot(ctx, x, y, color) {
    const outerR = 6.75 * SCALE;
    const innerR = 5.25 * SCALE;
    const cx = x * SCALE;
    const cy = y * SCALE;

    ctx.fillStyle = rgb([0, 0, 0], 170);
    ctx.beginPath();
    ctx.arc(cx + 0.6 * SCALE, cy + 0.8 * SCALE, outerR + 0.35 * SCALE, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = rgb([255, 255, 255]);
    ctx.beginPath();
    ctx.arc(cx, cy, outerR, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = rgb(color);
    ctx.beginPath();
    ctx.arc(cx, cy, innerR, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawFavicon(next) {
    const canvas = document.createElement("canvas");
    canvas.width = SIZE;
    canvas.height = SIZE;
    const ctx = canvas.getContext("2d");
    if (!ctx) return "";

    drawMark(ctx);
    if (next.casual) drawBadgeDot(ctx, 6.5, 6.5, BADGE_CASUAL);
    if (next.ranked) drawBadgeDot(ctx, 25.5, 6.5, BADGE_RANKED);
    return canvas.toDataURL("image/png");
  }

  function sameBadges(a, b) {
    return !!a.ranked === !!b.ranked && !!a.casual === !!b.casual;
  }

  function applyBadges(next) {
    badges = {
      ranked: !!next.ranked,
      casual: !!next.casual,
    };
    if (!markReady) return;
    const link = ensureLink();
    link.type = "image/png";
    link.sizes = "64x64";
    link.href = drawFavicon(badges);
  }

  window.AsobbyFavicon = {
    setBadges(next) {
      whenMarkReady(() => applyBadges(next || {}));
    },
    setPosted(active) {
      whenMarkReady(() => applyBadges({ ranked: !!active, casual: !!active }));
    },
    initFromAuth(options) {
      if (!window.AsobbyNotify) {
        whenMarkReady(() => applyBadges({ ranked: false, casual: false }));
        return;
      }
      void AsobbyNotify.initBackgroundNotify(options);
    },
  };

  loadMarkImage();
})();

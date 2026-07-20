(function () {
  "use strict";

  const SIZE = 32;
  const BG = [23, 28, 36];
  const FRAME = [54, 69, 89];
  const ACCENT = [106, 176, 243];
  const CORE = [230, 237, 245];
  const BADGE_RANKED = [90, 158, 255];
  const BADGE_CASUAL = [87, 192, 125];

  let linkEl = null;
  let badges = { ranked: false, casual: false };

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

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawMark(ctx) {
    roundRect(ctx, 0, 0, SIZE, SIZE, 7);
    ctx.fillStyle = rgb(BG);
    ctx.fill();

    ctx.strokeStyle = rgb(FRAME);
    ctx.lineWidth = 1.25;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(16, 5);
    ctx.lineTo(25.5, 10.5);
    ctx.lineTo(25.5, 21.5);
    ctx.lineTo(16, 27);
    ctx.lineTo(6.5, 21.5);
    ctx.lineTo(6.5, 10.5);
    ctx.closePath();
    ctx.stroke();

    ctx.fillStyle = rgb(ACCENT);
    ctx.beginPath();
    ctx.moveTo(10, 16);
    ctx.lineTo(14, 12.75);
    ctx.lineTo(14, 19.25);
    ctx.closePath();
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(22, 16);
    ctx.lineTo(18, 12.75);
    ctx.lineTo(18, 19.25);
    ctx.closePath();
    ctx.fill();

    ctx.save();
    ctx.translate(16, 16);
    ctx.rotate(Math.PI / 4);
    roundRect(ctx, -1.8, -1.8, 3.6, 3.6, 0.45);
    ctx.fillStyle = rgb(CORE);
    ctx.fill();
    ctx.restore();
  }

  function drawBadgeDot(ctx, x, y, color) {
    const outerR = 6.75;
    const innerR = 5.25;

    ctx.fillStyle = rgb([0, 0, 0], 170);
    ctx.beginPath();
    ctx.arc(x + 0.6, y + 0.8, outerR + 0.35, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = rgb([255, 255, 255]);
    ctx.beginPath();
    ctx.arc(x, y, outerR, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = rgb(color);
    ctx.beginPath();
    ctx.arc(x, y, innerR, 0, Math.PI * 2);
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
    const normalized = {
      ranked: !!next.ranked,
      casual: !!next.casual,
    };
    if (sameBadges(badges, normalized)) return;
    badges = normalized;
    const link = ensureLink();
    link.type = "image/png";
    link.href = drawFavicon(normalized);
  }

  window.AsobbyFavicon = {
    setBadges(next) {
      applyBadges(next || {});
    },
    setPosted(active) {
      applyBadges({ ranked: !!active, casual: !!active });
    },
    initFromAuth(options) {
      if (!window.AsobbyNotify) {
        applyBadges({ ranked: false, casual: false });
        return;
      }
      void AsobbyNotify.initBackgroundNotify(options);
    },
  };
})();

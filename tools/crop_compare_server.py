#!/usr/bin/env python3
"""Side-by-side scanned-page crop comparison viewer.

This local app renders two page images next to each other and overlays the
current crop model on top of each page. The crop window can be shifted with
numeric controls so the shared offset model can be tuned interactively.

The viewer is read-only. It does not write crop state.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import cv2


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PDF Crop Compare</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0c1015;
      --panel: #141922;
      --panel-2: #10151c;
      --line: #28313b;
      --text: #eef2f7;
      --muted: #9aa8b8;
      --accent: #7dd3fc;
      --crop: #facc15;
      --crop-fill: rgba(250, 204, 21, 0.14);
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      height: 100%;
      overflow: hidden;
      background:
        radial-gradient(circle at top, #151b25 0%, #0c1015 48%, #07090c 100%);
      color: var(--text);
      font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }
    button, select, input {
      font: inherit;
      color: inherit;
    }
    .app {
      display: grid;
      grid-template-columns: 320px 1fr;
      height: 100vh;
      min-height: 0;
    }
    .sidebar {
      border-right: 1px solid var(--line);
      background: rgba(12, 16, 21, 0.96);
      padding: 14px;
      overflow: auto;
    }
    .brand {
      font-size: 20px;
      font-weight: 800;
      margin-bottom: 8px;
      letter-spacing: 0.02em;
    }
    .muted { color: var(--muted); }
    .small { font-size: 12px; }
    .stack { display: grid; gap: 10px; }
    .card {
      border: 1px solid var(--line);
      background: rgba(20, 25, 34, 0.88);
      border-radius: 12px;
      padding: 10px;
    }
    .row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .row > * { flex: 0 0 auto; }
    .grow { flex: 1 1 auto; min-width: 0; }
    .btn, select, input {
      border: 1px solid var(--line);
      background: #171d26;
      border-radius: 10px;
      padding: 8px 10px;
    }
    .btn {
      cursor: pointer;
    }
    .btn:hover, select:hover, input:hover { border-color: #415062; }
    .btn.active {
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }
    .split {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
    .field {
      display: grid;
      gap: 4px;
    }
    .field label {
      color: var(--muted);
      font-size: 12px;
    }
    .main {
      display: grid;
      grid-template-rows: auto 1fr;
      min-width: 0;
      min-height: 0;
    }
    .toolbar {
      border-bottom: 1px solid var(--line);
      background: rgba(10, 14, 19, 0.92);
      padding: 12px 14px;
      display: grid;
      gap: 10px;
    }
    .statusline {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .pill {
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.09);
      background: rgba(255,255,255,0.05);
      font-size: 12px;
    }
    .workspace {
      overflow: auto;
      padding: 14px;
      min-width: 0;
      min-height: 0;
    }
    .compare-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      align-items: start;
    }
    .pane {
      min-width: 0;
      display: grid;
      gap: 8px;
    }
    .pane-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .canvas-wrap {
      position: relative;
      display: inline-block;
      background: #0b0d10;
      border: 1px solid #2a313b;
      box-shadow: 0 24px 70px rgba(0,0,0,0.45);
      overflow: auto;
      max-height: calc(100vh - 180px);
    }
    canvas {
      display: block;
      image-rendering: auto;
    }
    .overlay {
      position: absolute;
      left: 0;
      top: 0;
      pointer-events: none;
    }
    .image {
      position: relative;
      left: 0;
      top: 0;
    }
    .meta-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
      font-size: 12px;
    }
    .meta-grid div {
      padding: 6px 8px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: rgba(255,255,255,0.03);
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
    }
    .kbd {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      padding: 1px 5px;
      border: 1px solid var(--line);
      border-bottom-width: 2px;
      border-radius: 6px;
      background: rgba(255,255,255,0.05);
      color: var(--text);
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">PDF Crop Compare</div>
      <div class="muted small" id="rootInfo"></div>
      <div class="sep" style="height:1px;background:var(--line);margin:8px 0;"></div>
      <div class="stack">
        <div class="card">
          <div class="field">
            <label>PDF</label>
            <select id="pdfSelect"></select>
          </div>
          <div class="split" style="margin-top:8px;">
            <div class="field"><label>Page offset</label><input id="pageOffset" type="number" step="1"></div>
            <div class="field"><label>Zoom</label><input id="zoomValue" type="number" step="0.05" min="0.1" max="8"></div>
          </div>
          <div class="split" style="margin-top:8px;">
            <div class="field"><label>Scale X</label><input id="scaleX" type="number" step="0.01" min="0.5" max="1.5"></div>
            <div class="field"><label>Scale Y</label><input id="scaleY" type="number" step="0.01" min="0.5" max="1.5"></div>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="btn" id="resetSizeBtn">Reset size</button>
          </div>
          <div class="split" style="margin-top:8px;">
            <div class="field"><label>Left page</label><input id="leftPage" type="number" min="1" step="1"></div>
            <div class="field"><label>Right page</label><input id="rightPage" type="number" min="1" step="1"></div>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="btn" id="prevPairBtn">Prev pair</button>
            <button class="btn" id="nextPairBtn">Next pair</button>
          </div>
        </div>
        <div class="card">
          <div class="split">
            <div class="field"><label>X shift</label><input id="xShift" type="number" step="1"></div>
            <div class="field"><label>Y shift</label><input id="yShift" type="number" step="1"></div>
            <div class="field"><label>W delta</label><input id="wDelta" type="number" step="1"></div>
            <div class="field"><label>H delta</label><input id="hDelta" type="number" step="1"></div>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="btn" id="fitBtn">Fit</button>
            <button class="btn" id="zoomOutBtn">Zoom -</button>
            <button class="btn" id="zoomInBtn">Zoom +</button>
            <span class="pill" id="zoomLabel">fit</span>
          </div>
        </div>
        <div class="card">
          <div class="meta-grid">
            <div><strong>Base</strong><br><span id="baseState">n/a</span></div>
            <div><strong>Model</strong><br><span id="modelState">n/a</span></div>
            <div><strong>Left</strong><br><span id="leftState">n/a</span></div>
            <div><strong>Right</strong><br><span id="rightState">n/a</span></div>
          </div>
        </div>
        <div class="card">
          <div class="hint">
            The crop overlay is drawn from the current shared model and then shifted by the fields above.
            Use the two pages to compare spine and outer margins directly.
          </div>
          <div class="hint" style="margin-top:8px;">
            Shortcuts: <span class="kbd">←</span>/<span class="kbd">→</span> move page pair, <span class="kbd">f</span> fit, <span class="kbd">-</span>/<span class="kbd">+</span> zoom.
          </div>
        </div>
      </div>
    </aside>
    <main class="main">
      <div class="toolbar">
        <div class="statusline">
          <span class="pill" id="pageLabel">Loading...</span>
          <span class="pill" id="infoLabel">comparison view</span>
        </div>
      </div>
      <div class="workspace">
        <div class="compare-grid">
          <div class="pane">
            <div class="pane-header">
              <span id="leftTitle">Left page</span>
              <span id="leftDims"></span>
            </div>
            <div class="canvas-wrap" id="leftWrap">
              <canvas id="leftCanvas"></canvas>
              <canvas id="leftOverlay" class="overlay"></canvas>
            </div>
            <div class="meta-grid">
              <div><strong>Crop</strong><br><span id="leftCrop">n/a</span></div>
              <div><strong>Margins</strong><br><span id="leftMargins">n/a</span></div>
            </div>
          </div>
          <div class="pane">
            <div class="pane-header">
              <span id="rightTitle">Right page</span>
              <span id="rightDims"></span>
            </div>
            <div class="canvas-wrap" id="rightWrap">
              <canvas id="rightCanvas"></canvas>
              <canvas id="rightOverlay" class="overlay"></canvas>
            </div>
            <div class="meta-grid">
              <div><strong>Crop</strong><br><span id="rightCrop">n/a</span></div>
              <div><strong>Margins</strong><br><span id="rightMargins">n/a</span></div>
            </div>
          </div>
        </div>
      </div>
    </main>
  </div>
  <script>
    const state = {
      root: null,
      pdfs: [],
      currentPdfIndex: 0,
      leftPageNumber: 16,
      rightPageNumber: 17,
      pageNumberOffset: 1,
      scaleX: 1,
      scaleY: 1,
      model: null,
      shifts: { x: 0, y: 0, w: 0, h: 0 },
      zoomMode: 'manual',
      zoomScale: 1.25,
      leftImage: null,
      rightImage: null,
      leftInfo: null,
      rightInfo: null,
    };

    const els = {
      rootInfo: document.getElementById('rootInfo'),
      pdfSelect: document.getElementById('pdfSelect'),
      pageOffset: document.getElementById('pageOffset'),
      zoomValue: document.getElementById('zoomValue'),
      scaleX: document.getElementById('scaleX'),
      scaleY: document.getElementById('scaleY'),
      resetSizeBtn: document.getElementById('resetSizeBtn'),
      leftPage: document.getElementById('leftPage'),
      rightPage: document.getElementById('rightPage'),
      prevPairBtn: document.getElementById('prevPairBtn'),
      nextPairBtn: document.getElementById('nextPairBtn'),
      xShift: document.getElementById('xShift'),
      yShift: document.getElementById('yShift'),
      wDelta: document.getElementById('wDelta'),
      hDelta: document.getElementById('hDelta'),
      fitBtn: document.getElementById('fitBtn'),
      zoomOutBtn: document.getElementById('zoomOutBtn'),
      zoomInBtn: document.getElementById('zoomInBtn'),
      zoomLabel: document.getElementById('zoomLabel'),
      pageLabel: document.getElementById('pageLabel'),
      baseState: document.getElementById('baseState'),
      modelState: document.getElementById('modelState'),
      leftState: document.getElementById('leftState'),
      rightState: document.getElementById('rightState'),
      leftTitle: document.getElementById('leftTitle'),
      rightTitle: document.getElementById('rightTitle'),
      leftDims: document.getElementById('leftDims'),
      rightDims: document.getElementById('rightDims'),
      leftCrop: document.getElementById('leftCrop'),
      rightCrop: document.getElementById('rightCrop'),
      leftMargins: document.getElementById('leftMargins'),
      rightMargins: document.getElementById('rightMargins'),
      leftCanvas: document.getElementById('leftCanvas'),
      rightCanvas: document.getElementById('rightCanvas'),
      leftOverlay: document.getElementById('leftOverlay'),
      rightOverlay: document.getElementById('rightOverlay'),
      leftWrap: document.getElementById('leftWrap'),
      rightWrap: document.getElementById('rightWrap'),
    };

    const leftCtx = els.leftCanvas.getContext('2d');
    const rightCtx = els.rightCanvas.getContext('2d');
    const leftOverlayCtx = els.leftOverlay.getContext('2d');
    const rightOverlayCtx = els.rightOverlay.getContext('2d');

    function qs(name, fallback = null) {
      const url = new URL(window.location.href);
      return url.searchParams.get(name) ?? fallback;
    }

    async function api(path) {
      const res = await fetch(path);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res;
    }

    function pageSortKey(page) {
      return Number(page.number) || 0;
    }

    function folioNumber(pageNumber) {
      return Number(pageNumber) + Number(state.pageNumberOffset || 0);
    }

    function currentPdf() {
      return state.pdfs[state.currentPdfIndex];
    }

    function currentPages() {
      const pdf = currentPdf();
      if (!pdf) return [null, null];
      const left = pdf.pages.find((p) => Number(p.number) === state.leftPageNumber) || pdf.pages[0];
      const right = pdf.pages.find((p) => Number(p.number) === state.rightPageNumber) || pdf.pages[Math.min(1, pdf.pages.length - 1)];
      return [left || null, right || null];
    }

    function pageSide(pageNumber) {
      return folioNumber(pageNumber) % 2 === 1 ? 'right' : 'left';
    }

    function computeCrop(page, model, shifts) {
      const pageW = page.width;
      const pageH = page.height;
      const scaleX = Math.max(0.1, Math.min(8, state.scaleX || 1));
      const scaleY = Math.max(0.1, Math.min(8, state.scaleY || 1));
      const frameW = Math.max(1, Math.round((model.frame_w + shifts.w) * scaleX));
      const frameH = Math.max(1, Math.round((model.frame_h + shifts.h) * scaleY));
      const bottom = Math.round(model.bottom);
      const edge = Math.round(model.edge_margin);
      const xShift = Math.round(shifts.x);
      const yShift = Math.round(shifts.y);
      const top = Math.max(0, pageH - bottom - frameH + yShift);
      let left;
      if (pageSide(page.number) === 'right') {
        left = Math.max(0, edge + xShift);
      } else {
        left = Math.max(0, pageW - frameW - edge + xShift);
      }
      const crop = {
        x: left,
        y: top,
        w: frameW,
        h: frameH,
      };
      return crop;
    }

    function marginsForCrop(page, crop) {
      return {
        top: Math.round(crop.y),
        bottom: Math.round(page.height - crop.y - crop.h),
        left: Math.round(crop.x),
        right: Math.round(page.width - crop.x - crop.w),
      };
    }

    function updateControls() {
      const pdf = currentPdf();
      const [leftPage, rightPage] = currentPages();
      els.zoomLabel.textContent = state.zoomMode === 'fit' ? 'fit' : `${Math.round(state.zoomScale * 100)}%`;
      els.pageLabel.textContent = pdf ? `${pdf.name}` : 'Loading...';
      els.pageOffset.value = String(state.pageNumberOffset);
      els.zoomValue.value = String(state.zoomScale.toFixed(2));
      els.scaleX.value = String(state.scaleX.toFixed(2));
      els.scaleY.value = String(state.scaleY.toFixed(2));
      els.leftPage.value = String(state.leftPageNumber);
      els.rightPage.value = String(state.rightPageNumber);
      els.pdfSelect.value = String(state.currentPdfIndex);
      els.baseState.textContent = state.model
        ? `top ${Math.round(state.model.top)} / bottom ${Math.round(state.model.bottom)} / edge ${Math.round(state.model.edge_margin)}`
        : 'n/a';
      els.modelState.textContent = state.model
        ? `w ${Math.round(state.model.frame_w)} / h ${Math.round(state.model.frame_h)}`
        : 'n/a';
      if (leftPage) {
        els.leftTitle.textContent = `Left scan ${leftPage.number} / folio ${folioNumber(leftPage.number)}`;
        els.leftDims.textContent = `${leftPage.width} × ${leftPage.height}`;
      }
      if (rightPage) {
        els.rightTitle.textContent = `Right scan ${rightPage.number} / folio ${folioNumber(rightPage.number)}`;
        els.rightDims.textContent = `${rightPage.width} × ${rightPage.height}`;
      }
    }

    function setHash() {
      const url = new URL(window.location.href);
      url.searchParams.set('pdf', String(state.currentPdfIndex));
      url.searchParams.set('left', String(state.leftPageNumber));
      url.searchParams.set('right', String(state.rightPageNumber));
      url.searchParams.set('offset', String(state.pageNumberOffset));
      url.searchParams.set('scaleX', String(state.scaleX));
      url.searchParams.set('scaleY', String(state.scaleY));
      history.replaceState(null, '', url);
    }

    function fitScaleFor(w, h, wrap) {
      const pad = 24;
      const availW = Math.max(100, wrap.clientWidth - pad);
      const availH = Math.max(100, wrap.clientHeight - pad);
      return Math.min(1, availW / w, availH / h);
    }

    function setZoom(scale, fitMode = false) {
      state.zoomMode = fitMode ? 'fit' : 'manual';
      state.zoomScale = Math.max(0.1, Math.min(8, scale));
      render();
    }

    function drawPane(img, page, canvas, overlay, ctx, overlayCtx, wrap, crop, titleEl) {
      if (!img || !page) return;
      const scale = state.zoomMode === 'fit'
        ? fitScaleFor(page.width, page.height, wrap)
        : state.zoomScale;
      canvas.width = Math.max(1, Math.round(page.width * scale));
      canvas.height = Math.max(1, Math.round(page.height * scale));
      canvas.style.width = `${canvas.width}px`;
      canvas.style.height = `${canvas.height}px`;
      overlay.width = canvas.width;
      overlay.height = canvas.height;
      overlay.style.width = canvas.style.width;
      overlay.style.height = canvas.style.height;
      ctx.setTransform(scale, 0, 0, scale, 0, 0);
      ctx.clearRect(0, 0, page.width, page.height);
      ctx.drawImage(img, 0, 0, page.width, page.height);

      const c = crop;
      ctx.save();
      ctx.globalAlpha = 0.16;
      ctx.fillStyle = 'rgba(250, 204, 21, 0.12)';
      ctx.fillRect(c.x, c.y, c.w, c.h);
      ctx.restore();

      overlayCtx.clearRect(0, 0, overlay.width, overlay.height);
      overlayCtx.save();
      overlayCtx.scale(scale, scale);
      overlayCtx.fillStyle = varToRgba('--crop-fill', 0.12);
      overlayCtx.strokeStyle = getComputedStyle(document.documentElement).getPropertyValue('--crop').trim() || '#facc15';
      overlayCtx.lineWidth = 2;
      overlayCtx.fillRect(c.x, c.y, c.w, c.h);
      overlayCtx.strokeRect(c.x, c.y, c.w, c.h);
      overlayCtx.restore();
    }

    function varToRgba(name, alpha) {
      const style = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
      return style || `rgba(250, 204, 21, ${alpha})`;
    }

    function updatePaneMeta(page, crop, cropEl, marginsEl) {
      cropEl.textContent = `${Math.round(crop.x)}, ${Math.round(crop.y)} / ${Math.round(crop.w)} × ${Math.round(crop.h)}`;
      const m = marginsForCrop(page, crop);
      marginsEl.textContent = `t${m.top} b${m.bottom} l${m.left} r${m.right}`;
    }

    async function loadImage(url) {
      return await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error(`Could not load image: ${url}`));
        img.src = url;
      });
    }

    async function render() {
      const pdf = currentPdf();
      if (!pdf || !state.model) return;
      const [leftPage, rightPage] = currentPages();
      if (!leftPage || !rightPage) return;

      const leftUrl = leftPage.url;
      const rightUrl = rightPage.url;
      const [leftImg, rightImg] = await Promise.all([loadImage(leftUrl), loadImage(rightUrl)]);
      state.leftImage = leftImg;
      state.rightImage = rightImg;
      const leftCrop = computeCrop(leftPage, state.model, state.shifts);
      const rightCrop = computeCrop(rightPage, state.model, state.shifts);
      state.leftInfo = { page: leftPage, crop: leftCrop };
      state.rightInfo = { page: rightPage, crop: rightCrop };
      drawPane(leftImg, leftPage, els.leftCanvas, els.leftOverlay, leftCtx, leftOverlayCtx, els.leftWrap, leftCrop, els.leftTitle);
      drawPane(rightImg, rightPage, els.rightCanvas, els.rightOverlay, rightCtx, rightOverlayCtx, els.rightWrap, rightCrop, els.rightTitle);
      updatePaneMeta(leftPage, leftCrop, els.leftCrop, els.leftMargins);
      updatePaneMeta(rightPage, rightCrop, els.rightCrop, els.rightMargins);
      updateControls();
      setHash();
    }

    function parseIntOr(value, fallback) {
      const n = Number(value);
      return Number.isFinite(n) ? n : fallback;
    }

    function loadFromUrl() {
      const pdf = parseIntOr(qs('pdf', '0'), 0);
      state.currentPdfIndex = Math.max(0, pdf);
      state.leftPageNumber = parseIntOr(qs('left', '16'), 16);
      state.rightPageNumber = parseIntOr(qs('right', '17'), 17);
      state.pageNumberOffset = parseIntOr(qs('offset', '1'), 1);
      state.scaleX = parseFloat(qs('scaleX', '1')) || 1;
      state.scaleY = parseFloat(qs('scaleY', '1')) || 1;
    }

    function bindInputs() {
      const onShift = () => {
        state.shifts.x = Number(els.xShift.value) || 0;
        state.shifts.y = Number(els.yShift.value) || 0;
        state.shifts.w = Number(els.wDelta.value) || 0;
        state.shifts.h = Number(els.hDelta.value) || 0;
        render();
      };
      const onOffset = () => {
        state.pageNumberOffset = Number(els.pageOffset.value) || 0;
        render();
      };
      const onScaleX = () => {
        state.scaleX = Math.max(0.1, Math.min(8, Number(els.scaleX.value) || 1));
        render();
      };
      const onScaleY = () => {
        state.scaleY = Math.max(0.1, Math.min(8, Number(els.scaleY.value) || 1));
        render();
      };
      [els.xShift, els.yShift, els.wDelta, els.hDelta].forEach((el) => {
        el.addEventListener('input', onShift);
      });
      els.pageOffset.addEventListener('input', onOffset);
      els.scaleX.addEventListener('input', onScaleX);
      els.scaleY.addEventListener('input', onScaleY);
      els.resetSizeBtn.addEventListener('click', () => {
        state.scaleX = 1;
        state.scaleY = 1;
        els.scaleX.value = '1.00';
        els.scaleY.value = '1.00';
        render();
      });
      els.pdfSelect.addEventListener('change', () => {
        state.currentPdfIndex = Number(els.pdfSelect.value) || 0;
        render();
      });
      els.leftPage.addEventListener('change', () => {
        state.leftPageNumber = Number(els.leftPage.value) || state.leftPageNumber;
        render();
      });
      els.rightPage.addEventListener('change', () => {
        state.rightPageNumber = Number(els.rightPage.value) || state.rightPageNumber;
        render();
      });
      els.prevPairBtn.addEventListener('click', () => {
        state.leftPageNumber = Math.max(1, state.leftPageNumber - 1);
        state.rightPageNumber = Math.max(1, state.rightPageNumber - 1);
        render();
      });
      els.nextPairBtn.addEventListener('click', () => {
        state.leftPageNumber += 1;
        state.rightPageNumber += 1;
        render();
      });
      els.fitBtn.addEventListener('click', () => setZoom(1, true));
      els.zoomOutBtn.addEventListener('click', () => setZoom(state.zoomScale / 1.15, false));
      els.zoomInBtn.addEventListener('click', () => setZoom(state.zoomScale * 1.15, false));
      els.zoomValue.addEventListener('change', () => setZoom(Number(els.zoomValue.value) || 1, false));
      window.addEventListener('keydown', (evt) => {
        if (evt.key === 'ArrowLeft') {
          evt.preventDefault();
          els.prevPairBtn.click();
        } else if (evt.key === 'ArrowRight') {
          evt.preventDefault();
          els.nextPairBtn.click();
        } else if (evt.key === 'f') {
          evt.preventDefault();
          setZoom(1, true);
        } else if (evt.key === '-') {
          evt.preventDefault();
          setZoom(state.zoomScale / 1.15, false);
        } else if (evt.key === '+') {
          evt.preventDefault();
          setZoom(state.zoomScale * 1.15, false);
        }
      });
      window.addEventListener('resize', () => {
        if (state.zoomMode === 'fit') render();
      });
    }

    async function loadIndex() {
      const res = await api('/api/index');
      const data = await res.json();
      state.root = data.root;
      state.pdfs = data.pdfs;
      state.model = data.model;
      els.rootInfo.textContent = `Root: ${data.root}`;
      els.pdfSelect.innerHTML = '';
      for (let i = 0; i < state.pdfs.length; i++) {
        const pdf = state.pdfs[i];
        const option = document.createElement('option');
        option.value = String(i);
        option.textContent = `${pdf.name} (${pdf.pages.length})`;
        els.pdfSelect.appendChild(option);
      }
      const urlPdf = parseIntOr(qs('pdf', '0'), 0);
      state.currentPdfIndex = Math.max(0, Math.min(state.pdfs.length - 1, urlPdf));
      if (!state.model) {
        els.baseState.textContent = 'n/a';
        els.modelState.textContent = 'n/a';
      }
      loadFromUrl();
      const pdf = currentPdf();
      if (pdf && pdf.pages.length) {
        state.leftPageNumber = Math.max(1, state.leftPageNumber);
        state.rightPageNumber = Math.max(1, state.rightPageNumber);
      }
      state.currentPdfIndex = Math.max(0, Math.min(state.pdfs.length - 1, state.currentPdfIndex));
      els.xShift.value = '0';
      els.yShift.value = '0';
      els.wDelta.value = '0';
      els.hDelta.value = '0';
      els.pageOffset.value = String(state.pageNumberOffset);
      els.zoomValue.value = String(state.zoomScale.toFixed(2));
      els.scaleX.value = String(state.scaleX.toFixed(2));
      els.scaleY.value = String(state.scaleY.toFixed(2));
      bindInputs();
      updateControls();
      await render();
    }

    loadIndex().catch((err) => {
      els.pageLabel.textContent = `Error: ${err.message}`;
    });
  </script>
</body>
</html>
"""


def page_sort_key(path: Path) -> tuple[int, str]:
    suffix = path.stem.rsplit("-", 1)[-1]
    try:
        return int(suffix), path.stem
    except ValueError:
        return 10**9, path.stem


def med(values: List[float]) -> Optional[float]:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2:
        return float(values[mid])
    return float((values[mid - 1] + values[mid]) / 2.0)


def load_model(annotation_root: Path) -> Optional[Dict[str, float]]:
    states: list[Dict[str, Any]] = []
    for state_path in annotation_root.glob("*/state/page-*.json"):
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("crop") and data.get("margins"):
            states.append(data)
    if not states:
        return None
    top = med([float(s["margins"]["top"]) for s in states if s["margins"].get("top") is not None])
    bottom = med([float(s["margins"]["bottom"]) for s in states if s["margins"].get("bottom") is not None])
    left = med([float(s["margins"]["left"]) for s in states if s["margins"].get("left") is not None])
    right = med([float(s["margins"]["right"]) for s in states if s["margins"].get("right") is not None])
    frame_w = med([float(s["crop"]["w"]) for s in states if s["crop"].get("w") is not None])
    frame_h = med([float(s["crop"]["h"]) for s in states if s["crop"].get("h") is not None])
    source_w = med(
        [
            float(s["margins"]["left"]) + float(s["crop"]["w"]) + float(s["margins"]["right"])
            for s in states
        ]
    )
    source_h = med(
        [
            float(s["margins"]["top"]) + float(s["crop"]["h"]) + float(s["margins"]["bottom"])
            for s in states
        ]
    )
    if None in (top, bottom, left, right, frame_w, frame_h, source_w, source_h):
        return None
    return {
        "top": float(top),
        "bottom": float(bottom),
        "left": float(left),
        "right": float(right),
        "edge_margin": float(left if left is not None else 0.0),
        "frame_w": float(frame_w),
        "frame_h": float(frame_h),
        "source_w": float(source_w),
        "source_h": float(source_h),
    }


def list_pages(root: Path) -> list[dict[str, Any]]:
    pdfs = []
    for pdf_dir in sorted(root.iterdir()):
        if not pdf_dir.is_dir():
            continue
        pages_dir = pdf_dir / "pages"
        if not pages_dir.exists():
            continue
        pages = []
        for page_path in sorted(pages_dir.glob("*.png"), key=page_sort_key):
            img = cv2.imread(str(page_path), cv2.IMREAD_GRAYSCALE)
            height, width = (int(img.shape[0]), int(img.shape[1])) if img is not None else (None, None)
            pages.append(
                {
                    "number": int(page_path.stem.rsplit("-", 1)[-1]),
                    "width": width,
                    "height": height,
                    "url": f"/files/{pdf_dir.name}/pages/{page_path.name}",
                }
            )
        if pages:
            pdfs.append({"name": pdf_dir.name, "pages": pages})
    return pdfs


class Handler(BaseHTTPRequestHandler):
    root_dir: Path
    model_root: Path

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_html(self, text: str) -> None:
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(str(path))
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self.send_html(HTML)
            return
        if parsed.path == "/api/index":
            self.send_json(
                {
                    "root": str(self.root_dir),
                    "pdfs": list_pages(self.root_dir),
                    "model": load_model(self.model_root),
                }
            )
            return
        if parsed.path.startswith("/files/"):
            rel = parsed.path[len("/files/") :]
            target = self.root_dir / rel
            self.serve_file(target)
            return
        self.send_error(404)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("deskewed-pages"),
        help="Rendered page root directory",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("crop-annotations"),
        help="Saved crop annotation root used to seed the baseline model",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8005, help="Bind port")
    args = parser.parse_args()

    if not args.root.exists():
        raise SystemExit(f"Missing root directory: {args.root}")
    if not args.model_root.exists():
        raise SystemExit(f"Missing model root: {args.model_root}")

    Handler.root_dir = args.root.resolve()
    Handler.model_root = args.model_root.resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving {Handler.root_dir} at http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())

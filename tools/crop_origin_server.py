#!/usr/bin/env python3
"""Interactive per-page crop-origin and deskew-exception editor.

This app uses a shared crop model:
- one crop size for all pages
- one margin model for all pages
- a per-page origin
- a per-page left/right side

The defaults are seeded from the existing cropped output so the dataset starts
prepopulated. After that, the app only loads and saves data. There is no page
detection in the editor itself.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
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
  <title>PDF Crop Origin Editor</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0c1015;
      --panel: #141922;
      --line: #28313b;
      --text: #eef2f7;
      --muted: #9aa8b8;
      --accent: #7dd3fc;
      --crop: #facc15;
      --inner: #60a5fa;
      --good: #86efac;
      --warn: #f59e0b;
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
    button, select, input { font: inherit; color: inherit; }
    .app {
      display: grid;
      grid-template-columns: 330px 1fr;
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
      background: rgba(20, 25, 34, 0.9);
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
    .btn, select, input {
      border: 1px solid var(--line);
      background: #171d26;
      border-radius: 10px;
      padding: 8px 10px;
    }
    .btn { cursor: pointer; }
    .btn:hover, select:hover, input:hover { border-color: #415062; }
    .btn.active {
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }
    .pill {
      padding: 3px 8px;
      border-radius: 999px;
      border: 1px solid rgba(255,255,255,0.09);
      background: rgba(255,255,255,0.05);
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
    .workspace {
      overflow: auto;
      padding: 14px;
      min-width: 0;
      min-height: 0;
    }
    .canvas-wrap {
      position: relative;
      display: inline-block;
      background: #0b0d10;
      border: 1px solid #2a313b;
      box-shadow: 0 24px 70px rgba(0,0,0,0.45);
    }
    canvas { display: block; }
    #overlayCanvas {
      position: absolute;
      left: 0;
      top: 0;
      pointer-events: auto;
      touch-action: none;
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
    .page-list {
      display: grid;
      gap: 4px;
      max-height: 40vh;
      overflow: auto;
      padding-right: 4px;
    }
    .page-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      padding: 7px 8px;
      border: 1px solid transparent;
      border-radius: 8px;
      cursor: pointer;
      color: var(--muted);
    }
    .page-item:hover { background: rgba(255,255,255,0.04); }
    .page-item.active {
      color: var(--text);
      border-color: var(--accent);
      background: rgba(125, 211, 252, 0.08);
    }
    .page-item.saved::after {
      content: "saved";
      color: var(--good);
      font-size: 11px;
    }
    .page-item.dirty::after {
      content: "dirty";
      color: var(--warn);
      font-size: 11px;
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
      <div class="brand">PDF Crop Origin</div>
      <div class="muted small" id="rootInfo"></div>
      <div class="sep" style="height:1px;background:var(--line);margin:8px 0;"></div>
      <div class="stack">
        <div class="card">
          <div class="row">
            <button class="btn" id="prevBtn">Prev</button>
            <button class="btn" id="nextBtn">Next</button>
            <span class="pill" id="pageCounter"></span>
          </div>
          <div class="row" style="margin-top:8px;">
            <div class="field grow">
              <label>PDF</label>
              <select id="pageSelect"></select>
            </div>
          </div>
          <div class="row" style="margin-top:8px;">
            <label class="small"><input type="checkbox" id="showAllPages"> show all pages</label>
          </div>
        </div>
        <div class="card">
          <div class="row" style="margin-bottom:8px;">
            <span class="pill">Shared model</span>
          </div>
          <div class="split">
            <div class="field"><label>Crop W</label><input id="cropW" type="number" min="1" step="1"></div>
            <div class="field"><label>Crop H</label><input id="cropH" type="number" min="1" step="1"></div>
          </div>
          <div class="split" style="margin-top:8px;">
            <div class="field"><label>Top</label><input id="marginTop" type="number" min="0" step="1"></div>
            <div class="field"><label>Bottom</label><input id="marginBottom" type="number" min="0" step="1"></div>
            <div class="field"><label>Inner</label><input id="marginInner" type="number" min="0" step="1"></div>
            <div class="field"><label>Outer</label><input id="marginOuter" type="number" min="0" step="1"></div>
          </div>
        </div>
        <div class="card">
          <div class="row" style="margin-bottom:8px;">
            <span class="pill">Current page</span>
          </div>
          <div class="split">
            <div class="field"><label>Origin X</label><input id="originX" type="number" step="1"></div>
            <div class="field"><label>Origin Y</label><input id="originY" type="number" step="1"></div>
          </div>
          <div class="field" style="margin-top:8px;">
            <label>Side</label>
            <select id="pageSide">
              <option value="left">Left</option>
              <option value="right">Right</option>
            </select>
          </div>
          <div class="row" style="margin-top:8px;">
            <label class="small"><input type="checkbox" id="reacquire"> full reacquisition</label>
          </div>
          <div class="field" style="margin-top:8px;">
            <label>Reacquire from</label>
            <select id="reacquireSource">
              <option value="source-pdf">Source PDF</option>
              <option value="extracted-image">Original extracted image</option>
            </select>
          </div>
          <div class="row" style="margin-top:8px;">
            <label class="small"><input type="checkbox" id="passthrough"> bypass crop / cleanup</label>
          </div>
          <div class="row" style="margin-top:8px;">
            <label class="small"><input type="checkbox" id="deskew"> deskew</label>
          </div>
          <div class="split" style="margin-top:8px;">
            <div class="field"><label>Deskew Deg</label><input id="deskewDeg" type="number" step="0.1"></div>
            <div class="field"><label>Deskew X</label><input id="deskewX" type="number" step="1"></div>
            <div class="field"><label>Deskew Y</label><input id="deskewY" type="number" step="1"></div>
            <div class="field"><label>Grid Step</label><input id="deskewStep" type="number" step="1" min="1"></div>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="btn" id="fitBtn">Fit</button>
            <button class="btn" id="zoomOutBtn">Zoom Out</button>
            <button class="btn" id="zoomInBtn">Zoom In</button>
            <span class="pill" id="zoomLabel">fit</span>
          </div>
        </div>
        <div class="card">
          <div class="meta-grid">
            <div><strong>Page</strong><br><span id="pageLabel">n/a</span></div>
            <div><strong>Saved</strong><br><span id="savedState">no</span></div>
            <div><strong>Dirty</strong><br><span id="dirtyState">no</span></div>
            <div><strong>Mode</strong><br><span id="modeState">n/a</span></div>
          </div>
        </div>
        <div class="card">
          <div class="hint">
            Outer box = shared crop window.
            Inner box = content area after top / bottom / inner / outer margins.
            Drag inside the outer box to move the current page origin.
            Drag the bottom-right handle to resize the shared crop window.
            Enable deskew to show the rotatable grid and its movable origin.
          </div>
          <div class="hint" style="margin-top:8px;">
            Shortcuts: <span class="kbd">←</span>/<span class="kbd">→</span> page nav, <span class="kbd">f</span> fit.
          </div>
        </div>
      </div>
      <div class="sep" style="height:1px;background:var(--line);margin:8px 0;"></div>
      <div class="small muted">Pages</div>
      <div id="pageList" class="page-list"></div>
    </aside>
    <main class="main">
      <div class="toolbar">
        <div class="statusline">
          <span class="pill" id="pageTitle">Loading...</span>
          <span class="pill" id="infoLabel">origin model</span>
          <span class="pill" id="offsetLabel">offsets: n/a</span>
        </div>
        <div class="hint">
          No detection is used here. The app only loads and saves the origin/side dataset.
        </div>
      </div>
      <div class="workspace" id="workspace">
        <div class="canvas-wrap" id="canvasWrap">
          <canvas id="displayCanvas"></canvas>
          <canvas id="overlayCanvas"></canvas>
        </div>
      </div>
    </main>
  </div>
  <script>
    const state = {
      root: null,
      pdfs: [],
      currentPdfIndex: 0,
      currentPageIndex: 0,
      model: null,
      pageData: null,
      pageImage: null,
      pageImageSize: { width: 0, height: 0 },
      displayScale: 1,
      zoomScale: 1,
      fitToPage: true,
      dirty: false,
      saveTimer: null,
      modelSaveTimer: null,
      crop: null,
      innerCrop: null,
      reacquire: false,
      reacquireSource: 'source-pdf',
      passthrough: false,
      deskewEnabled: false,
      deskewDeg: 0,
      deskewOrigin: { x: 0, y: 0 },
      deskewStep: 100,
      deskewDragging: false,
      drawing: false,
      moving: false,
      resizing: false,
      dragStart: null,
      moveStart: null,
      moveOrigin: null,
      resizeOrigin: null,
      resizeHandle: null,
      overlayRaf: 0,
      cursorMode: 'default',
      showAllPages: false,
    };

    const els = {
      rootInfo: document.getElementById('rootInfo'),
      prevBtn: document.getElementById('prevBtn'),
      nextBtn: document.getElementById('nextBtn'),
      pageSelect: document.getElementById('pageSelect'),
      showAllPages: document.getElementById('showAllPages'),
      cropW: document.getElementById('cropW'),
      cropH: document.getElementById('cropH'),
      marginTop: document.getElementById('marginTop'),
      marginBottom: document.getElementById('marginBottom'),
      marginInner: document.getElementById('marginInner'),
      marginOuter: document.getElementById('marginOuter'),
      originX: document.getElementById('originX'),
      originY: document.getElementById('originY'),
      pageSide: document.getElementById('pageSide'),
      reacquire: document.getElementById('reacquire'),
      reacquireSource: document.getElementById('reacquireSource'),
      passthrough: document.getElementById('passthrough'),
      deskew: document.getElementById('deskew'),
      deskewDeg: document.getElementById('deskewDeg'),
      deskewX: document.getElementById('deskewX'),
      deskewY: document.getElementById('deskewY'),
      deskewStep: document.getElementById('deskewStep'),
      fitBtn: document.getElementById('fitBtn'),
      zoomOutBtn: document.getElementById('zoomOutBtn'),
      zoomInBtn: document.getElementById('zoomInBtn'),
      zoomLabel: document.getElementById('zoomLabel'),
      pageCounter: document.getElementById('pageCounter'),
      pageTitle: document.getElementById('pageTitle'),
      pageLabel: document.getElementById('pageLabel'),
      savedState: document.getElementById('savedState'),
      dirtyState: document.getElementById('dirtyState'),
      modeState: document.getElementById('modeState'),
      offsetLabel: document.getElementById('offsetLabel'),
      pageList: document.getElementById('pageList'),
      workspace: document.getElementById('workspace'),
      canvasWrap: document.getElementById('canvasWrap'),
      displayCanvas: document.getElementById('displayCanvas'),
      overlayCanvas: document.getElementById('overlayCanvas'),
    };

    const displayCtx = els.displayCanvas.getContext('2d');
    const overlayCtx = els.overlayCanvas.getContext('2d');

    function qs(name, fallback = null) {
      const url = new URL(window.location.href);
      return url.searchParams.get(name) ?? fallback;
    }

    async function api(path, options) {
      const res = await fetch(path, options);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      return res;
    }

    function parseIntOr(value, fallback) {
      const n = Number(value);
      return Number.isFinite(n) ? n : fallback;
    }

    function pageSortKey(page) {
      return Number(page.number) || 0;
    }

    function currentPdf() {
      return state.pdfs[state.currentPdfIndex];
    }

    function currentPages() {
      const pdf = currentPdf();
      if (!pdf) return [null, null];
      const current = pdf.pages[state.currentPageIndex] || pdf.pages[0] || null;
      return [current, current];
    }

    function currentDeskewOrigin() {
      const crop = modelCropRect() || state.crop;
      const fallbackX = crop ? Math.round(crop.x + crop.w / 2) : 0;
      const fallbackY = crop ? Math.round(crop.y + crop.h / 2) : 0;
      const origin = state.pageData?.deskew_origin || {};
      return {
        x: Number(origin.x ?? fallbackX),
        y: Number(origin.y ?? fallbackY),
      };
    }

    function isDeskewEnabled() {
      return Boolean(state.pageData?.deskew_enabled);
    }

    function rotatePoint(pt, origin, angleDeg) {
      const rad = (angleDeg * Math.PI) / 180;
      const cos = Math.cos(rad);
      const sin = Math.sin(rad);
      const dx = pt.x - origin.x;
      const dy = pt.y - origin.y;
      return {
        x: origin.x + dx * cos - dy * sin,
        y: origin.y + dx * sin + dy * cos,
      };
    }

    function getDeskewAngle() {
      return isDeskewEnabled() ? (Number(state.pageData?.deskew_deg ?? 0) || 0) : 0;
    }

    function applyViewTransform(ctx) {
      ctx.setTransform(state.displayScale, 0, 0, state.displayScale, 0, 0);
      if (!isDeskewEnabled()) return;
      const origin = currentDeskewOrigin();
      ctx.translate(origin.x, origin.y);
      ctx.rotate((getDeskewAngle() * Math.PI) / 180);
      ctx.translate(-origin.x, -origin.y);
    }

    function invertViewPoint(pt) {
      let x = pt.x / Math.max(0.0001, state.displayScale);
      let y = pt.y / Math.max(0.0001, state.displayScale);
      if (!isDeskewEnabled()) {
        return { x, y };
      }
      const origin = currentDeskewOrigin();
      const angle = -getDeskewAngle();
      const rad = (angle * Math.PI) / 180;
      const cos = Math.cos(rad);
      const sin = Math.sin(rad);
      const dx = x - origin.x;
      const dy = y - origin.y;
      return {
        x: origin.x + dx * cos - dy * sin,
        y: origin.y + dx * sin + dy * cos,
      };
    }

    function setHash() {
      const url = new URL(window.location.href);
      url.searchParams.set('pdf', String(state.currentPdfIndex));
      url.searchParams.set('page', String(state.currentPageIndex + 1));
      history.replaceState(null, '', url);
    }

    function fitScaleFor(width, height) {
      const availableWidth = Math.max(100, els.workspace.clientWidth - 32);
      const availableHeight = Math.max(100, els.workspace.clientHeight - 32);
      return Math.min(1, availableWidth / width, availableHeight / height);
    }

    function setScale(scale, fitPage = false, anchor = null) {
      if (!state.pageImageSize.width || !state.pageImageSize.height) return;
      const oldScale = state.displayScale || 1;
      const oldRect = els.overlayCanvas.getBoundingClientRect();
      const workspaceRect = els.workspace.getBoundingClientRect();
      let contentX = null;
      let contentY = null;
      let anchorViewX = null;
      let anchorViewY = null;
      if (anchor) {
        anchorViewX = anchor.clientX - workspaceRect.left;
        anchorViewY = anchor.clientY - workspaceRect.top;
        contentX = (anchor.clientX - oldRect.left) / oldScale;
        contentY = (anchor.clientY - oldRect.top) / oldScale;
      }
      state.fitToPage = fitPage;
      state.zoomScale = scale;
      state.displayScale = fitPage ? fitScaleFor(state.pageImageSize.width, state.pageImageSize.height) : scale;
      ensureCanvasSize(state.pageImageSize.width, state.pageImageSize.height);
      redrawPage();
      if (anchor && contentX !== null && contentY !== null && anchorViewX !== null && anchorViewY !== null) {
        const targetLeft = (contentX * state.displayScale) - anchorViewX;
        const targetTop = (contentY * state.displayScale) - anchorViewY;
        els.workspace.scrollLeft = Math.max(0, targetLeft);
        els.workspace.scrollTop = Math.max(0, targetTop);
      }
      updateControls();
    }

    function setFit() {
      setScale(state.zoomScale || 1, true);
    }

    function zoomBy(factor, anchor = null) {
      const current = state.fitToPage ? state.displayScale : state.zoomScale;
      const next = Math.max(0.1, Math.min(8, current * factor));
      setScale(next, false, anchor);
    }

    function ensureCanvasSize(width, height) {
      els.displayCanvas.width = Math.max(1, Math.round(width * state.displayScale));
      els.displayCanvas.height = Math.max(1, Math.round(height * state.displayScale));
      els.displayCanvas.style.width = `${els.displayCanvas.width}px`;
      els.displayCanvas.style.height = `${els.displayCanvas.height}px`;
      els.canvasWrap.style.width = els.displayCanvas.style.width;
      els.canvasWrap.style.height = els.displayCanvas.style.height;
      els.overlayCanvas.width = els.displayCanvas.width;
      els.overlayCanvas.height = els.displayCanvas.height;
      els.overlayCanvas.style.width = els.displayCanvas.style.width;
      els.overlayCanvas.style.height = els.displayCanvas.style.height;
    }

    function modelCropRect() {
      if (!state.model || !state.pageData) return null;
      const w = Number(state.model.crop?.w || state.model.frame_w || 0);
      const h = Number(state.model.crop?.h || state.model.frame_h || 0);
      return {
        x: Number(state.pageData.origin?.x ?? state.pageData.x ?? 0),
        y: Number(state.pageData.origin?.y ?? state.pageData.y ?? 0),
        w,
        h,
      };
    }

    function innerCropRect(crop, model, side) {
      if (!crop || !model) return null;
      const top = Number(model.margins?.top ?? 0);
      const bottom = Number(model.margins?.bottom ?? 0);
      const inner = Number(model.margins?.inner ?? 0);
      const outer = Number(model.margins?.outer ?? 0);
      const width = Math.max(1, crop.w - inner - outer);
      const height = Math.max(1, crop.h - top - bottom);
      const x = side === 'right' ? crop.x + inner : crop.x + outer;
      return { x, y: crop.y + top, w: width, h: height };
    }

    function redrawPage() {
      const img = state.pageImage;
      if (!img) return;
      const w = state.pageImageSize.width;
      const h = state.pageImageSize.height;
      const crop = modelCropRect();
      const inner = innerCropRect(crop, state.model, state.pageData?.side || 'left');
      state.crop = crop;
      state.innerCrop = inner;
      displayCtx.save();
      displayCtx.clearRect(0, 0, els.displayCanvas.width, els.displayCanvas.height);
      displayCtx.scale(state.displayScale, state.displayScale);
      displayCtx.drawImage(img, 0, 0, w, h);
      displayCtx.restore();
      drawOverlay();
    }

    function scheduleOverlayRedraw() {
      if (state.overlayRaf) return;
      state.overlayRaf = requestAnimationFrame(() => {
        state.overlayRaf = 0;
        drawOverlay();
      });
    }

    function cropHandles(crop) {
      if (!crop) return [];
      const s = 14;
      const hs = s / 2;
      const x2 = crop.x + crop.w;
      const y2 = crop.y + crop.h;
      return [
        { name: 'se', x: x2 - hs, y: y2 - hs, w: s, h: s },
      ];
    }

    function drawDeskewGrid() {
      if (!state.crop || !state.model || !isDeskewEnabled()) return;
      const crop = state.crop;
      const origin = currentDeskewOrigin();
      const angle = Number(state.pageData?.deskew_deg ?? 0) || 0;
      const spacing = Math.max(1, Number(state.pageData?.deskew_step ?? 100) || 100);
      const bounds = {
        x: crop.x,
        y: crop.y,
        w: crop.w,
        h: crop.h,
      };
      const maxRadius = Math.hypot(bounds.w, bounds.h) + spacing * 4;
      const minX = Math.floor((bounds.x - origin.x - maxRadius) / spacing) * spacing;
      const maxX = Math.ceil((bounds.x + bounds.w - origin.x + maxRadius) / spacing) * spacing;
      const minY = Math.floor((bounds.y - origin.y - maxRadius) / spacing) * spacing;
      const maxY = Math.ceil((bounds.y + bounds.h - origin.y + maxRadius) / spacing) * spacing;
      const majorEvery = Math.max(100, spacing * 4);
      const drawSegment = (x1, y1, x2, y2, major) => {
        const p1 = rotatePoint({ x: x1, y: y1 }, origin, angle);
        const p2 = rotatePoint({ x: x2, y: y2 }, origin, angle);
        overlayCtx.beginPath();
        overlayCtx.moveTo(p1.x, p1.y);
        overlayCtx.lineTo(p2.x, p2.y);
        overlayCtx.strokeStyle = major ? 'rgba(125, 211, 252, 0.55)' : 'rgba(125, 211, 252, 0.20)';
        overlayCtx.lineWidth = major ? 1.5 : 1;
        overlayCtx.stroke();
      };
      overlayCtx.save();
      overlayCtx.setLineDash([]);
      overlayCtx.beginPath();
      overlayCtx.rect(bounds.x, bounds.y, bounds.w, bounds.h);
      overlayCtx.clip();
      for (let x = minX; x <= maxX; x += spacing) {
        drawSegment(x, bounds.y - maxRadius, x, bounds.y + bounds.h + maxRadius, x % majorEvery === 0);
      }
      for (let y = minY; y <= maxY; y += spacing) {
        drawSegment(bounds.x - maxRadius, y, bounds.x + bounds.w + maxRadius, y, y % majorEvery === 0);
      }
      overlayCtx.fillStyle = 'rgba(239, 68, 68, 0.9)';
      overlayCtx.strokeStyle = 'rgba(255,255,255,0.95)';
      overlayCtx.lineWidth = 1.5;
      overlayCtx.beginPath();
      overlayCtx.arc(origin.x, origin.y, 6, 0, Math.PI * 2);
      overlayCtx.fill();
      overlayCtx.stroke();
      overlayCtx.beginPath();
      overlayCtx.moveTo(origin.x - 14, origin.y);
      overlayCtx.lineTo(origin.x + 14, origin.y);
      overlayCtx.moveTo(origin.x, origin.y - 14);
      overlayCtx.lineTo(origin.x, origin.y + 14);
      overlayCtx.stroke();
      overlayCtx.restore();
    }

    function hitTestHandle(pt, crop) {
      if (!crop) return null;
      for (const handle of cropHandles(crop)) {
        if (pt.x >= handle.x && pt.x <= handle.x + handle.w && pt.y >= handle.y && pt.y <= handle.y + handle.h) {
          return handle.name;
        }
      }
      return null;
    }

    function hitTestDeskewOrigin(pt) {
      if (!isDeskewEnabled()) return false;
      const origin = currentDeskewOrigin();
      const radius = Math.max(10, 18 / Math.max(0.1, state.displayScale));
      return Math.hypot(pt.x - origin.x, pt.y - origin.y) <= radius;
    }

    function pointInRect(pt, rect) {
      if (!rect) return false;
      return pt.x >= rect.x && pt.x <= rect.x + rect.w && pt.y >= rect.y && pt.y <= rect.y + rect.h;
    }

    function clampModelSize(w, h) {
      return {
        w: Math.max(1, Math.round(w)),
        h: Math.max(1, Math.round(h)),
      };
    }

    function resizeCrop(handle, origin, p) {
      if (!state.model) return;
      if (handle !== 'se') return;
      const next = clampModelSize(p.x - origin.x, p.y - origin.y);
      state.model.crop = state.model.crop || {};
      state.model.crop.w = next.w;
      state.model.crop.h = next.h;
      state.model.frame_w = next.w;
      state.model.frame_h = next.h;
      updateModelInputs();
      setDirty(true);
      scheduleModelSave();
      redrawPage();
    }

    function pointerDown(evt) {
      evt.preventDefault();
      if (!state.pageData || !state.model) return;
      const p = screenToCanvas(evt);
      if (isDeskewEnabled() && hitTestDeskewOrigin(p)) {
        state.deskewDragging = true;
        setCursor('grabbing');
        els.overlayCanvas.setPointerCapture(evt.pointerId);
        return;
      }
      const crop = state.crop;
      const handle = crop ? hitTestHandle(p, crop) : null;
      if (handle && crop) {
        state.resizing = true;
        state.resizeHandle = handle;
        state.resizeOrigin = { ...crop };
      } else if (crop && pointInRect(p, crop)) {
        state.moving = true;
        state.moveStart = p;
        state.moveOrigin = { ...state.pageData.origin };
      } else {
        state.moving = true;
        state.moveStart = p;
        state.moveOrigin = { ...state.pageData.origin };
      }
      setCursor('grabbing');
      els.overlayCanvas.setPointerCapture(evt.pointerId);
    }

    function pointerMove(evt) {
      if (!state.pageData || !state.model) return;
      const p = screenToCanvas(evt);
      const crop = state.crop;
      if (state.deskewDragging) {
        state.pageData.deskew_origin = state.pageData.deskew_origin || { x: 0, y: 0 };
        state.pageData.deskew_origin.x = Math.round(p.x);
        state.pageData.deskew_origin.y = Math.round(p.y);
        updatePageInputs();
        scheduleOverlayRedraw();
      } else if (state.moving && state.moveStart && state.moveOrigin) {
        const dx = p.x - state.moveStart.x;
        const dy = p.y - state.moveStart.y;
        state.pageData.origin.x = Math.round(state.moveOrigin.x + dx);
        state.pageData.origin.y = Math.round(state.moveOrigin.y + dy);
        updatePageInputs();
        scheduleOverlayRedraw();
      } else if (state.resizing && state.resizeHandle && state.resizeOrigin) {
        resizeCrop(state.resizeHandle, state.resizeOrigin, p);
      } else if (!state.resizing && !state.moving) {
        const handle = crop ? hitTestHandle(p, crop) : null;
        if (isDeskewEnabled() && hitTestDeskewOrigin(p)) {
          setCursor('grab');
        } else if (handle) {
          setCursor('grab');
        } else if (crop && pointInRect(p, crop)) {
          setCursor('grab');
        } else {
          setCursor('default');
        }
      }
    }

    function pointerUp(evt) {
      if (state.deskewDragging || state.moving || state.resizing) {
        state.deskewDragging = false;
        state.moving = false;
        state.resizing = false;
        state.moveStart = null;
        state.moveOrigin = null;
        state.resizeOrigin = null;
        state.resizeHandle = null;
        updatePageInputs();
        setDirty(true);
        scheduleSave();
        setCursor('default');
      }
      if (els.overlayCanvas.hasPointerCapture(evt.pointerId)) {
        els.overlayCanvas.releasePointerCapture(evt.pointerId);
      }
      redrawPage();
    }

    function setCursor(mode) {
      if (state.cursorMode === mode) return;
      state.cursorMode = mode;
      els.overlayCanvas.style.cursor = mode;
    }

    function screenToCanvas(evt) {
      const rect = els.overlayCanvas.getBoundingClientRect();
      const scaleX = state.pageImageSize.width / rect.width;
      const scaleY = state.pageImageSize.height / rect.height;
      return {
        x: (evt.clientX - rect.left) * scaleX,
        y: (evt.clientY - rect.top) * scaleY,
      };
    }

    function updateModelInputs() {
      if (!state.model) return;
      els.cropW.value = String(Math.round(state.model.crop?.w ?? state.model.frame_w ?? 0));
      els.cropH.value = String(Math.round(state.model.crop?.h ?? state.model.frame_h ?? 0));
      els.marginTop.value = String(Math.round(state.model.margins?.top ?? 0));
      els.marginBottom.value = String(Math.round(state.model.margins?.bottom ?? 0));
      els.marginInner.value = String(Math.round(state.model.margins?.inner ?? 0));
      els.marginOuter.value = String(Math.round(state.model.margins?.outer ?? 0));
    }

    function updatePageInputs() {
      if (!state.pageData) return;
      els.originX.value = String(Math.round(state.pageData.origin?.x ?? state.pageData.x ?? 0));
      els.originY.value = String(Math.round(state.pageData.origin?.y ?? state.pageData.y ?? 0));
      els.pageSide.value = state.pageData.side || state.pageData.page_side || 'left';
      els.reacquire.checked = Boolean(state.pageData.reacquire);
      els.reacquireSource.value = state.pageData.reacquire_source || 'source-pdf';
      els.passthrough.checked = Boolean(state.pageData.passthrough);
      els.deskew.checked = Boolean(state.pageData.deskew_enabled);
      const origin = currentDeskewOrigin();
      els.deskewDeg.value = String(Number(state.pageData.deskew_deg ?? 0) || 0);
      els.deskewX.value = String(Math.round(origin.x));
      els.deskewY.value = String(Math.round(origin.y));
      els.deskewStep.value = String(Math.max(1, Number(state.pageData.deskew_step ?? 100) || 100));
      const enabled = Boolean(state.pageData.deskew_enabled);
      els.deskewDeg.disabled = !enabled;
      els.deskewX.disabled = !enabled;
      els.deskewY.disabled = !enabled;
      els.deskewStep.disabled = !enabled;
    }

    function updateControls() {
      const pdf = currentPdf();
      const [page] = currentPages();
      els.zoomLabel.textContent = state.fitToPage ? 'fit' : `${Math.round(state.displayScale * 100)}%`;
      els.savedState.textContent = state.pageData?.saved ? 'yes' : 'no';
      els.dirtyState.textContent = state.dirty ? 'yes' : 'no';
      const modeBits = [];
      if (state.pageData?.reacquire) modeBits.push(`reacquire/${state.pageData.reacquire_source || 'source-pdf'}`);
      if (state.pageData?.passthrough) modeBits.push('passthrough');
      if (state.pageData?.deskew_enabled) modeBits.push(`deskew ${Number(state.pageData?.deskew_deg ?? 0).toFixed(1)}°`);
      els.modeState.textContent = modeBits.length ? modeBits.join(' / ') : (state.pageData?.side || 'n/a');
      els.pageTitle.textContent = pdf ? `${pdf.name}` : 'Loading...';
      els.pageLabel.textContent = page ? `${page.number}` : 'n/a';
      els.pageCounter.textContent = pdf ? `${state.currentPageIndex + 1} / ${pdf.pages.length}` : '';
      els.offsetLabel.textContent = state.crop
        ? `crop ${Math.round(state.crop.x)},${Math.round(state.crop.y)} / ${Math.round(state.crop.w)}×${Math.round(state.crop.h)}`
        : 'offsets: n/a';
      updateModelInputs();
      updatePageInputs();
      els.showAllPages.checked = state.showAllPages;
    }

    function setDirty(dirty) {
      state.dirty = dirty;
      updateControls();
      renderPageList();
    }

    function setPage(pageIndex, pdfIndex = state.currentPdfIndex) {
      const pdf = state.pdfs[pdfIndex];
      if (!pdf || !pdf.pages.length) return;
      state.currentPdfIndex = pdfIndex;
      state.currentPageIndex = Math.max(0, Math.min(pdf.pages.length - 1, pageIndex));
      loadCurrentPage().catch((err) => {
        els.pageTitle.textContent = `Error: ${err.message}`;
      });
    }

    function prevPage() {
      const pdf = currentPdf();
      if (!pdf) return;
      if (state.currentPageIndex > 0) {
        setPage(state.currentPageIndex - 1);
      }
    }

    function nextPage() {
      const pdf = currentPdf();
      if (!pdf) return;
      if (state.currentPageIndex < pdf.pages.length - 1) {
        setPage(state.currentPageIndex + 1);
      }
    }

    function renderPageList() {
      els.pageList.innerHTML = '';
      const pdf = currentPdf();
      if (!pdf) return;
      const pages = state.showAllPages ? pdf.pages : pdf.pages.filter((page) => page.saved || page.dirty);
      pages.forEach((page) => {
        const item = document.createElement('div');
        item.className = 'page-item';
        const reacq = page.reacquire ? ` reacq:${page.reacquire_source || 'source-pdf'}` : '';
        const deskew = page.deskew_enabled ? ` deskew:${Number(page.deskew_deg || 0).toFixed(1)}°` : '';
        item.innerHTML = `<span>${page.number}</span><span class="muted">${page.side || page.page_side || 'n/a'}${reacq}${deskew}</span>`;
        if (page.index === state.currentPageIndex) item.classList.add('active');
        if (page.saved) item.classList.add('saved');
        if (page.dirty) item.classList.add('dirty');
        item.addEventListener('click', () => setPage(page.index, state.currentPdfIndex));
        els.pageList.appendChild(item);
      });
    }

    function saveLabel(text) {
      els.savedState.textContent = text;
    }

    function scheduleSave() {
      clearTimeout(state.saveTimer);
      state.saveTimer = setTimeout(() => {
        saveCurrentPage();
      }, 500);
    }

    function scheduleModelSave() {
      clearTimeout(state.modelSaveTimer);
      state.modelSaveTimer = setTimeout(() => {
        saveModel();
      }, 500);
    }

    async function saveModel() {
      if (!state.model) return;
      const payload = {
        model: state.model,
      };
      await api('/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: 'model', ...payload }),
      });
      setDirty(false);
    }

    async function saveCurrentPage() {
      if (!state.pageData) return;
      const payload = {
        pdf: state.pageData.pdf,
        page: state.pageData.page,
        origin: state.pageData.origin,
        side: state.pageData.side,
        reacquire: Boolean(state.pageData.reacquire),
        reacquire_source: state.pageData.reacquire_source || 'source-pdf',
        passthrough: Boolean(state.pageData.passthrough),
        deskew_enabled: Boolean(state.pageData.deskew_enabled),
        deskew_deg: Number(state.pageData.deskew_deg ?? 0) || 0,
        deskew_origin: state.pageData.deskew_origin || currentDeskewOrigin(),
        deskew_step: Math.max(1, Number(state.pageData.deskew_step ?? 100) || 100),
      };
      await api('/api/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: 'page', ...payload }),
      });
      state.pageData.saved = true;
      setDirty(false);
      renderPageList();
    }

    function bindInputs() {
      const onModelChange = () => {
        if (!state.model) return;
        state.model.crop = state.model.crop || {};
        state.model.crop.w = Math.max(1, Number(els.cropW.value) || 1);
        state.model.crop.h = Math.max(1, Number(els.cropH.value) || 1);
        state.model.frame_w = state.model.crop.w;
        state.model.frame_h = state.model.crop.h;
        state.model.margins = state.model.margins || {};
        state.model.margins.top = Math.max(0, Number(els.marginTop.value) || 0);
        state.model.margins.bottom = Math.max(0, Number(els.marginBottom.value) || 0);
        state.model.margins.inner = Math.max(0, Number(els.marginInner.value) || 0);
        state.model.margins.outer = Math.max(0, Number(els.marginOuter.value) || 0);
        redrawPage();
        setDirty(true);
        scheduleModelSave();
      };
      const onPageChange = () => {
        if (!state.pageData) return;
        state.pageData.origin = state.pageData.origin || { x: 0, y: 0 };
        state.pageData.origin.x = Number(els.originX.value) || 0;
        state.pageData.origin.y = Number(els.originY.value) || 0;
        state.pageData.side = els.pageSide.value || 'left';
        state.pageData.page_side = state.pageData.side;
        state.pageData.reacquire = Boolean(els.reacquire.checked);
        state.pageData.reacquire_source = els.reacquireSource.value || 'source-pdf';
        state.pageData.passthrough = Boolean(els.passthrough.checked);
        state.pageData.deskew_enabled = Boolean(els.deskew.checked);
        state.pageData.deskew_deg = state.pageData.deskew_enabled ? (Number(els.deskewDeg.value) || 0) : 0;
        state.pageData.deskew_origin = state.pageData.deskew_origin || currentDeskewOrigin();
        const deskewX = Number(els.deskewX.value);
        const deskewY = Number(els.deskewY.value);
        if (Number.isFinite(deskewX)) state.pageData.deskew_origin.x = deskewX;
        if (Number.isFinite(deskewY)) state.pageData.deskew_origin.y = deskewY;
        state.pageData.deskew_step = Math.max(1, Number(els.deskewStep.value) || 100);
        redrawPage();
        setDirty(true);
        scheduleSave();
      };
      [els.cropW, els.cropH, els.marginTop, els.marginBottom, els.marginInner, els.marginOuter].forEach((el) => {
        el.addEventListener('input', onModelChange);
      });
      [els.originX, els.originY, els.pageSide].forEach((el) => {
        el.addEventListener('input', onPageChange);
        el.addEventListener('change', onPageChange);
      });
      [els.reacquire, els.reacquireSource].forEach((el) => {
        el.addEventListener('input', onPageChange);
        el.addEventListener('change', onPageChange);
      });
      [els.passthrough].forEach((el) => {
        el.addEventListener('input', onPageChange);
        el.addEventListener('change', onPageChange);
      });
      [els.deskew, els.deskewDeg, els.deskewX, els.deskewY, els.deskewStep].forEach((el) => {
        el.addEventListener('input', onPageChange);
        el.addEventListener('change', onPageChange);
      });
      els.showAllPages.addEventListener('change', () => {
        state.showAllPages = els.showAllPages.checked;
        renderPageList();
      });
      els.prevBtn.addEventListener('click', prevPage);
      els.nextBtn.addEventListener('click', nextPage);
      els.pageSelect.addEventListener('change', () => {
        state.currentPdfIndex = Number(els.pageSelect.value) || 0;
        state.currentPageIndex = 0;
        loadCurrentPage().catch((err) => {
          els.pageTitle.textContent = `Error: ${err.message}`;
        });
      });
      els.fitBtn.addEventListener('click', () => setScale(1, true));
      els.zoomOutBtn.addEventListener('click', () => setScale(state.zoomScale / 1.15, false));
      els.zoomInBtn.addEventListener('click', () => setScale(state.zoomScale * 1.15, false));
      window.addEventListener('resize', () => {
        if (state.fitToPage) redrawPage();
      });
    }

    function drawOverlay() {
      overlayCtx.clearRect(0, 0, els.overlayCanvas.width, els.overlayCanvas.height);
      if (!state.crop || !state.model) return;
      overlayCtx.save();
      overlayCtx.scale(state.displayScale, state.displayScale);
      overlayCtx.strokeStyle = '#facc15';
      overlayCtx.fillStyle = 'rgba(250, 204, 21, 0.10)';
      overlayCtx.lineWidth = 2;
      overlayCtx.fillRect(state.crop.x, state.crop.y, state.crop.w, state.crop.h);
      overlayCtx.strokeRect(state.crop.x, state.crop.y, state.crop.w, state.crop.h);
      const inner = state.innerCrop;
      if (inner) {
        overlayCtx.strokeStyle = '#60a5fa';
        overlayCtx.fillStyle = 'rgba(96, 165, 250, 0.08)';
        overlayCtx.setLineDash([8, 6]);
        overlayCtx.fillRect(inner.x, inner.y, inner.w, inner.h);
        overlayCtx.strokeRect(inner.x, inner.y, inner.w, inner.h);
        overlayCtx.setLineDash([]);
      }
      for (const handle of cropHandles(state.crop)) {
        overlayCtx.fillStyle = '#fde68a';
        overlayCtx.strokeStyle = '#7c5b00';
        overlayCtx.fillRect(handle.x, handle.y, handle.w, handle.h);
        overlayCtx.strokeRect(handle.x, handle.y, handle.w, handle.h);
      }
      drawDeskewGrid();
      overlayCtx.restore();
    }

    async function loadImage(url) {
      return await new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => reject(new Error(`Could not load image: ${url}`));
        img.src = url;
      });
    }

    async function loadCurrentPage() {
      const pdf = currentPdf();
      if (!pdf) return;
      const page = pdf.pages[state.currentPageIndex];
      if (!page) return;
      const res = await api(`/api/page?pdf=${encodeURIComponent(pdf.name)}&page=${encodeURIComponent(page.number)}`);
      const data = await res.json();
      state.pageData = data.pageData;
      state.model = data.model;
      state.pageImageSize = { width: data.pageImageSize.width, height: data.pageImageSize.height };
      state.crop = modelCropRect();
      state.innerCrop = innerCropRect(state.crop, state.model, state.pageData?.side || 'left');
      state.reacquire = Boolean(state.pageData?.reacquire);
      state.reacquireSource = state.pageData?.reacquire_source || 'source-pdf';
      state.passthrough = Boolean(state.pageData?.passthrough);
      state.deskewEnabled = Boolean(state.pageData?.deskew_enabled);
      state.deskewDeg = Number(state.pageData?.deskew_deg ?? 0) || 0;
      state.deskewOrigin = state.pageData?.deskew_origin || currentDeskewOrigin();
      state.deskewStep = Math.max(1, Number(state.pageData?.deskew_step ?? 100) || 100);
      state.pageImage = await loadImage(data.pageImageUrl);
      if (!state.fitToPage) {
        setScale(state.zoomScale, false);
      } else {
        setScale(state.zoomScale, true);
      }
      updateControls();
      renderPageList();
      setHash();
    }

    async function loadIndex() {
      const res = await api('/api/index');
      const data = await res.json();
      state.root = data.root;
      state.pdfs = data.pdfs;
      state.model = data.model;
      els.rootInfo.textContent = `Root: ${data.root}`;
      els.pageSelect.innerHTML = '';
      for (let i = 0; i < state.pdfs.length; i++) {
        const pdf = state.pdfs[i];
        pdf.pages.sort((a, b) => pageSortKey(a) - pageSortKey(b));
        const option = document.createElement('option');
        option.value = String(i);
        option.textContent = `${pdf.name} (${pdf.pages.length})`;
        els.pageSelect.appendChild(option);
      }
      const urlPdf = parseIntOr(qs('pdf', '0'), 0);
      const urlPage = parseIntOr(qs('page', '1'), 1) - 1;
      state.currentPdfIndex = Math.max(0, Math.min(state.pdfs.length - 1, urlPdf));
      const pdf = currentPdf();
      state.currentPageIndex = pdf ? Math.max(0, Math.min(pdf.pages.length - 1, urlPage)) : 0;
      els.pageSelect.value = String(state.currentPdfIndex);
      bindInputs();
      updateControls();
      renderPageList();
      await loadCurrentPage();
    }

    loadIndex().catch((err) => {
      els.pageTitle.textContent = `Error: ${err.message}`;
    });

    function setPageDirtySideEffects() {
      renderPageList();
      updateControls();
    }
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


def infer_side(page_number: int, offset: int) -> str:
    return "right" if (page_number + offset) % 2 == 1 else "left"


def load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class PageInfo:
    index: int
    number: int
    path: Path
    width: int
    height: int
    origin: Dict[str, int]
    side: str
    reacquire: bool = False
    reacquire_source: str = "source-pdf"
    passthrough: bool = False
    needs_rotation: bool = False
    deskew_enabled: bool = False
    deskew_deg: float = 0.0
    deskew_origin: Optional[Dict[str, int]] = None
    deskew_step: int = 100
    saved: bool = False
    dirty: bool = False


@dataclass
class PDFDoc:
    name: str
    pages: List[PageInfo]


class CropOriginStore:
    def __init__(self, source_root: Path, dataset_root: Path, seed_root: Path, page_number_offset: int = 1) -> None:
        self.source_root = source_root
        self.dataset_root = dataset_root
        self.seed_root = seed_root
        self.page_number_offset = page_number_offset
        self.lock = threading.Lock()
        self.model_path = self.dataset_root / "model.json"
        self.docs: List[PDFDoc] = []
        self.model: Dict[str, Any] = {}
        self.ensure_seeded()
        self.reload()

    def source_pdf_dirs(self) -> List[Path]:
        return [p for p in sorted(self.source_root.iterdir()) if p.is_dir()]

    def source_page_paths(self, pdf_dir: Path) -> List[Path]:
        pages_dir = pdf_dir / "pages"
        if not pages_dir.exists():
            return []
        return sorted(pages_dir.glob("*.png"), key=page_sort_key)

    def page_state_path(self, pdf_name: str, page_number: int) -> Path:
        return self.dataset_root / pdf_name / "state" / f"page-{page_number:03d}.json"

    def seed_state_path(self, pdf_name: str, page_number: int) -> Path:
        return self.seed_root / pdf_name / "state" / f"page-{page_number:03d}.json"

    def load_seed_states(self) -> List[Dict[str, Any]]:
        states: List[Dict[str, Any]] = []
        for path in self.seed_root.glob("*/*/*.json"):
            if path.parent.name != "state":
                continue
            try:
                states.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return states

    def ensure_seeded(self) -> None:
        self.dataset_root.mkdir(parents=True, exist_ok=True)
        seed_model = self.build_model_from_seed()
        if not self.model_path.exists():
            write_json(self.model_path, seed_model)
        seed_index: Dict[tuple[str, int], Dict[str, Any]] = {}
        for state in self.load_seed_states():
            pdf_name = str(state.get("pdf") or "")
            page_num = int(state.get("page") or 0)
            if pdf_name and page_num:
                seed_index[(pdf_name, page_num)] = state
        for pdf_dir in self.source_pdf_dirs():
            for page_path in self.source_page_paths(pdf_dir):
                page_num = int(page_path.stem.rsplit("-", 1)[-1])
                out_path = self.page_state_path(pdf_dir.name, page_num)
                if out_path.exists():
                    continue
                seed_state = seed_index.get((pdf_dir.name, page_num), {})
                img = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
                if img is None:
                    continue
                if seed_state.get("crop"):
                    crop = seed_state["crop"]
                    origin = {
                        "x": int(round(float(crop.get("x", 0)))),
                        "y": int(round(float(crop.get("y", 0)))),
                    }
                else:
                    origin = {"x": 0, "y": 0}
                side = str(seed_state.get("page_side") or seed_state.get("side_hint") or infer_side(page_num, self.page_number_offset))
                write_json(
                    out_path,
                    {
                        "pdf": pdf_dir.name,
                        "page": page_num,
                        "origin": origin,
                        "side": side,
                        "page_side": side,
                        "reacquire": bool(seed_state.get("reacquire", False)),
                        "reacquire_source": str(seed_state.get("reacquire_source") or "source-pdf"),
                        "passthrough": bool(seed_state.get("passthrough", False)),
                        "needs_rotation": bool(seed_state.get("deskew_enabled", seed_state.get("needs_rotation", False))),
                        "deskew_enabled": bool(seed_state.get("deskew_enabled", False)),
                        "deskew_deg": float(seed_state.get("deskew_deg", 0) or 0),
                        "deskew_origin": seed_state.get("deskew_origin") or {
                            "x": origin["x"],
                            "y": origin["y"],
                        },
                        "deskew_step": int(seed_state.get("deskew_step", 100) or 100),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

    def build_model_from_seed(self) -> Dict[str, Any]:
        crops: List[Dict[str, Any]] = []
        for state in self.load_seed_states():
            if state and state.get("crop") and state.get("margins"):
                crops.append(state)
        if not crops:
            # Fallback to a conservative model.
            return {
                "page_number_offset": self.page_number_offset,
                "crop": {"w": 2200, "h": 3400},
                "margins": {"top": 8, "bottom": 100, "inner": 80, "outer": 80},
                "seeded_from": str(self.seed_root),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        widths = [float(s["crop"]["w"]) for s in crops if s["crop"].get("w") is not None]
        heights = [float(s["crop"]["h"]) for s in crops if s["crop"].get("h") is not None]
        top = med([float(s["margins"]["top"]) for s in crops if s["margins"].get("top") is not None]) or 0.0
        bottom = med([float(s["margins"]["bottom"]) for s in crops if s["margins"].get("bottom") is not None]) or 0.0
        inner_vals: List[float] = []
        outer_vals: List[float] = []
        for s in crops:
            m = s.get("margins") or {}
            side = str(s.get("page_side") or s.get("side_hint") or "left")
            if side == "right":
                inner_vals.append(float(m.get("left", 0)))
                outer_vals.append(float(m.get("right", 0)))
            else:
                inner_vals.append(float(m.get("right", 0)))
                outer_vals.append(float(m.get("left", 0)))
        model = {
            "page_number_offset": self.page_number_offset,
            "crop": {
                "w": int(round(med(widths) or 2200)),
                "h": int(round(med(heights) or 3400)),
            },
            "margins": {
                "top": int(round(top)),
                "bottom": int(round(bottom)),
                "inner": int(round(med(inner_vals) or 80)),
                "outer": int(round(med(outer_vals) or 80)),
            },
            "seeded_from": str(self.seed_root),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return model

    def reload(self) -> None:
        self.model = load_json(self.model_path) or self.build_model_from_seed()
        self.docs = []
        for pdf_dir in self.source_pdf_dirs():
            pages: List[PageInfo] = []
            for idx, page_path in enumerate(self.source_page_paths(pdf_dir), start=1):
                img = cv2.imread(str(page_path), cv2.IMREAD_GRAYSCALE)
                if img is None:
                    continue
                h, w = img.shape[:2]
                state = load_json(self.page_state_path(pdf_dir.name, idx)) or {}
                origin = state.get("origin") or {"x": 0, "y": 0}
                side = str(state.get("side") or state.get("page_side") or infer_side(idx, int(self.model.get("page_number_offset", self.page_number_offset))))
                reacquire = bool(state.get("reacquire"))
                reacquire_source = str(state.get("reacquire_source") or "source-pdf")
                passthrough = bool(state.get("passthrough"))
                deskew_enabled = bool(state.get("deskew_enabled"))
                deskew_deg = float(state.get("deskew_deg") or 0)
                deskew_origin = state.get("deskew_origin") or {"x": int(origin.get("x", 0)), "y": int(origin.get("y", 0))}
                deskew_step = int(state.get("deskew_step") or 100)
                pages.append(
                    PageInfo(
                        index=idx - 1,
                        number=idx,
                        path=page_path,
                        width=w,
                        height=h,
                        origin={"x": int(origin.get("x", 0)), "y": int(origin.get("y", 0))},
                        side=side,
                        reacquire=reacquire,
                        reacquire_source=reacquire_source,
                        passthrough=passthrough,
                        needs_rotation=deskew_enabled,
                        deskew_enabled=deskew_enabled,
                        deskew_deg=deskew_deg,
                        deskew_origin={"x": int(deskew_origin.get("x", 0)), "y": int(deskew_origin.get("y", 0))},
                        deskew_step=deskew_step,
                        saved=bool(state.get("updated_at")),
                        dirty=False,
                    )
                )
            if pages:
                self.docs.append(PDFDoc(name=pdf_dir.name, pages=pages))

    def get_doc(self, pdf_name: str) -> PDFDoc:
        for doc in self.docs:
            if doc.name == pdf_name:
                return doc
        raise KeyError(pdf_name)

    def index(self) -> Dict[str, Any]:
        return {
            "root": str(self.source_root),
            "datasetRoot": str(self.dataset_root),
            "pageCount": sum(len(doc.pages) for doc in self.docs),
            "model": self.model,
            "pdfs": [
                {
                    "name": doc.name,
                    "pages": [
                        {
                            "index": p.index,
                            "number": p.number,
                            "width": p.width,
                            "height": p.height,
                            "origin": p.origin,
                            "side": p.side,
                            "reacquire": p.reacquire,
                            "reacquire_source": p.reacquire_source,
                            "passthrough": p.passthrough,
                            "needs_rotation": p.deskew_enabled,
                            "deskew_enabled": p.deskew_enabled,
                            "deskew_deg": p.deskew_deg,
                            "deskew_origin": p.deskew_origin,
                            "deskew_step": p.deskew_step,
                            "saved": p.saved,
                            "dirty": p.dirty,
                        }
                        for p in doc.pages
                    ],
                }
                for doc in self.docs
            ],
        }

    def page_state(self, pdf_name: str, page_number: int) -> Dict[str, Any]:
        return load_json(self.page_state_path(pdf_name, page_number)) or {
            "pdf": pdf_name,
            "page": page_number,
            "origin": {"x": 0, "y": 0},
            "side": infer_side(page_number, int(self.model.get("page_number_offset", self.page_number_offset))),
            "reacquire": False,
            "reacquire_source": "source-pdf",
            "passthrough": False,
            "needs_rotation": False,
            "deskew_enabled": False,
            "deskew_deg": 0.0,
            "deskew_origin": {"x": 0, "y": 0},
            "deskew_step": 100,
        }

    def save_model(self, model: Dict[str, Any]) -> None:
        model = dict(model)
        model["page_number_offset"] = int(model.get("page_number_offset", self.page_number_offset))
        model["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(self.model_path, model)
        self.model = model

    def save_page(self, payload: Dict[str, Any]) -> None:
        pdf = str(payload["pdf"])
        page = int(payload["page"])
        origin = payload.get("origin") or {}
        deskew_origin = payload.get("deskew_origin") or origin
        side = str(payload.get("side") or payload.get("page_side") or infer_side(page, int(self.model.get("page_number_offset", self.page_number_offset))))
        state = {
            "pdf": pdf,
            "page": page,
            "origin": {
                "x": int(round(float(origin.get("x", 0)))),
                "y": int(round(float(origin.get("y", 0)))),
            },
            "side": side,
            "page_side": side,
            "reacquire": bool(payload.get("reacquire")),
            "reacquire_source": str(payload.get("reacquire_source") or "source-pdf"),
            "passthrough": bool(payload.get("passthrough")),
            "needs_rotation": bool(payload.get("deskew_enabled") or payload.get("needs_rotation")),
            "deskew_enabled": bool(payload.get("deskew_enabled")),
            "deskew_deg": float(payload.get("deskew_deg") or 0),
            "deskew_origin": {
                "x": int(round(float(deskew_origin.get("x", origin.get("x", 0))))),
                "y": int(round(float(deskew_origin.get("y", origin.get("y", 0))))),
            },
            "deskew_step": int(payload.get("deskew_step") or 100),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(self.page_state_path(pdf, page), state)
        self.reload()


class Handler(BaseHTTPRequestHandler):
    store: CropOriginStore

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
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
            self.send_json(self.store.index())
            return
        if parsed.path == "/api/page":
            query = parse_qs(parsed.query)
            pdf = query.get("pdf", [None])[0]
            page = int(query.get("page", ["1"])[0])
            if not pdf:
                self.send_json({"error": "missing pdf"}, status=400)
                return
            doc = self.store.get_doc(pdf)
            page_info = doc.pages[page - 1]
            page_state = self.store.page_state(pdf, page)
            model = self.store.model
            body = {
                "pdf": pdf,
                "page": page,
                "pageImageUrl": f"/files/{pdf}/pages/{page_info.path.name}",
                "pageImageSize": {"width": page_info.width, "height": page_info.height},
                "pageData": {
                    "pdf": pdf,
                    "page": page,
                    "origin": page_state.get("origin") or {"x": 0, "y": 0},
                    "side": page_state.get("side") or page_state.get("page_side") or infer_side(page, int(model.get("page_number_offset", self.store.page_number_offset))),
                    "reacquire": bool(page_state.get("reacquire")),
                    "reacquire_source": str(page_state.get("reacquire_source") or "source-pdf"),
                    "passthrough": bool(page_state.get("passthrough")),
                    "needs_rotation": bool(page_state.get("deskew_enabled") or page_state.get("needs_rotation")),
                    "deskew_enabled": bool(page_state.get("deskew_enabled")),
                    "deskew_deg": float(page_state.get("deskew_deg") or 0),
                    "deskew_origin": page_state.get("deskew_origin") or {"x": page_state.get("origin", {}).get("x", 0), "y": page_state.get("origin", {}).get("y", 0)},
                    "deskew_step": int(page_state.get("deskew_step") or 100),
                    "saved": bool(page_state.get("updated_at")),
                },
                "model": model,
            }
            self.send_json(body)
            return
        if parsed.path.startswith("/files/"):
            rel = parsed.path.removeprefix("/files/")
            target = self.store.source_root / rel
            self.serve_file(target)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/save":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        kind = payload.get("kind")
        if kind == "model":
            self.store.save_model(payload.get("model") or {})
        elif kind == "page":
            self.store.save_page(payload)
        else:
            self.send_json({"error": "unknown kind"}, status=400)
            return
        self.send_json({"ok": True})


def seed_dataset_if_needed(store: CropOriginStore) -> None:
    # The store already seeds itself on init. This hook exists to make the
    # intent explicit at the entry point.
    _ = store


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path("deskewed-pages"))
    parser.add_argument("--dataset-root", type=Path, default=Path("crop-origin-annotations"))
    parser.add_argument("--seed-root", type=Path, default=Path("cropped-pages"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8006)
    parser.add_argument(
        "--page-number-offset",
        type=int,
        default=1,
        help="Offset applied to the scan index before determining left/right side.",
    )
    args = parser.parse_args()

    if not args.source_root.exists():
      raise SystemExit(f"Missing source root: {args.source_root}")
    if not args.seed_root.exists():
      raise SystemExit(f"Missing seed root: {args.seed_root}")

    store = CropOriginStore(args.source_root.resolve(), args.dataset_root.resolve(), args.seed_root.resolve(), args.page_number_offset)
    seed_dataset_if_needed(store)
    Handler.store = store
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}")
    print(f"Source root: {store.source_root}")
    print(f"Dataset root: {store.dataset_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

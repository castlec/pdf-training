#!/usr/bin/env python3
"""Interactive model-building viewer for scanned page crops.

This app is intentionally narrower than the full annotation editor:
- it shows a small set of pages that appear close to upright
- it lets you declare a proposed frame per page
- it stores the frame together with header/footer margin offsets

The crop annotations are written to:
  crop-annotations/<document>/state/page-XXX.json

The rendered source pages are read from:
  pages/<document>/pages/<document>-XX.png
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np

PAGE_NUMBER_OFFSET = 1


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PDF Anchor Model Tool</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0e1116;
      --panel: #151a20;
      --panel-2: #0f1318;
      --line: #2b323b;
      --text: #eef2f7;
      --muted: #97a6b5;
      --accent: #f5b301;
      --accent-2: #7dd3fc;
      --crop: #facc15;
      --good: #86efac;
      --warn: #f59e0b;
    }
    * { box-sizing: border-box; }
    html, body {
      margin: 0;
      height: 100%;
      overflow: hidden;
      background: radial-gradient(circle at top, #151922 0%, #0b0e12 60%, #07090c 100%);
      color: var(--text);
      font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    }
    button, input, select {
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
      background: rgba(12, 16, 21, 0.96);
      border-right: 1px solid var(--line);
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
      background: rgba(21, 26, 32, 0.82);
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
    .btn {
      border: 1px solid var(--line);
      background: #1a2028;
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
    }
    .btn:hover { border-color: #44515f; }
    .btn.active {
      outline: 2px solid var(--accent);
      outline-offset: 1px;
    }
    .btn.primary { background: #242014; border-color: #5a4a12; }
    .btn.tiny { padding: 5px 8px; font-size: 12px; }
    .field {
      display: grid;
      gap: 4px;
    }
    .field label {
      color: var(--muted);
      font-size: 12px;
    }
    input[type="number"], select, input[type="text"] {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #10151b;
      padding: 7px 8px;
    }
    input[type="checkbox"] { transform: translateY(1px); }
    .page-list {
      display: grid;
      gap: 4px;
      max-height: 42vh;
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
      border-color: var(--accent-2);
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
    .main {
      display: grid;
      grid-template-rows: auto 1fr;
      min-width: 0;
      min-height: 0;
    }
    .toolbar {
      border-bottom: 1px solid var(--line);
      background: rgba(11, 15, 19, 0.92);
      padding: 12px 14px;
      display: grid;
      gap: 10px;
    }
    .statusline {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
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
      position: relative;
      overflow: auto;
      padding: 14px;
      min-width: 0;
      min-height: 0;
    }
    .canvas-wrap {
      position: relative;
      display: inline-block;
      background: #0a0d11;
      border: 1px solid #2a3039;
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
    .hint {
      color: var(--muted);
      font-size: 12px;
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
    .kbd {
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      padding: 1px 5px;
      border: 1px solid var(--line);
      border-bottom-width: 2px;
      border-radius: 6px;
      background: rgba(255,255,255,0.05);
      color: var(--text);
    }
    .split {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }
  </style>
</head>
<body>
  <div class="app">
    <aside class="sidebar">
      <div class="brand">PDF Anchor Model Tool</div>
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
            <label class="small"><input type="checkbox" id="showAllPages"> show all pages</label>
          </div>
        </div>
        <div class="card">
          <div class="field">
            <label>Anchor edge</label>
            <select id="anchorEdge">
              <option value="footer">Footer</option>
              <option value="header">Header</option>
            </select>
          </div>
          <div class="field" style="margin-top:8px;">
            <label>Page side hint</label>
            <input id="pageSideHint" type="text" readonly />
          </div>
          <div class="split" style="margin-top:8px;">
            <div class="field"><label>X</label><input id="cropX" type="number" min="0" step="1"></div>
            <div class="field"><label>Y</label><input id="cropY" type="number" min="0" step="1"></div>
            <div class="field"><label>W</label><input id="cropW" type="number" min="1" step="1"></div>
            <div class="field"><label>H</label><input id="cropH" type="number" min="1" step="1"></div>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="btn tiny" id="useFullBtn">Use full page</button>
            <button class="btn tiny" id="clearBtn">Clear frame</button>
            <button class="btn tiny" id="fitBtn">Fit</button>
            <button class="btn tiny" id="zoomOutBtn">Zoom -</button>
            <button class="btn tiny" id="zoomInBtn">Zoom +</button>
          </div>
          <div class="row" style="margin-top:8px;">
            <span class="pill" id="zoomLabel">fit</span>
            <span class="pill" id="saveLabel">idle</span>
          </div>
        </div>
        <div class="card">
          <div class="meta-grid">
            <div><strong>Frame</strong><br><span id="cropState">unset</span></div>
            <div><strong>Saved</strong><br><span id="savedState">no</span></div>
            <div><strong>Dirty</strong><br><span id="dirtyState">no</span></div>
            <div><strong>Angle</strong><br><span id="angleState">n/a</span></div>
          </div>
        </div>
        <div class="card">
          <div class="hint">
            Drag on the page to set a proposed frame.
            Drag inside an existing frame to move it.
            The page stays at fit-to-page by default.
          </div>
          <div class="hint" style="margin-top:8px;">
            Shortcuts:
            <div><span class="kbd">←</span>/<span class="kbd">→</span> page nav</div>
            <div><span class="kbd">f</span> fit, <span class="kbd">-</span>/<span class="kbd">+</span> zoom</div>
            <div><span class="kbd">u</span> toggle crop / clear crop</div>
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
          <span class="pill" id="pageLabel">Loading...</span>
          <span class="pill" id="pageHint">candidate pages</span>
          <span class="pill" id="offsetLabel">offsets: n/a</span>
        </div>
        <div class="hint">
          Use this to lock down a consistent final frame size. We will use the proposed frame plus header/footer offsets to anchor later deskew and crop passes, but nothing is cropped yet.
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
      pdfs: [],
      currentPdfIndex: 0,
      currentPageIndex: 0,
      pageData: null,
      pageImage: null,
      pageImageSize: { width: 0, height: 0 },
      displayScale: 1,
      zoomScale: 1,
      fitToPage: true,
      dirty: false,
      saveTimer: null,
      crop: null,
      drawing: false,
      moving: false,
      resizing: false,
      dragStart: null,
      moveStart: null,
      moveOrigin: null,
      resizeHandle: null,
      resizeOrigin: null,
      candidateOnly: true,
      showAllPages: false,
      overlayRaf: 0,
      cursorMode: 'default',
    };

    const els = {
      rootInfo: document.getElementById('rootInfo'),
      prevBtn: document.getElementById('prevBtn'),
      nextBtn: document.getElementById('nextBtn'),
      showAllPages: document.getElementById('showAllPages'),
      anchorEdge: document.getElementById('anchorEdge'),
      pageSideHint: document.getElementById('pageSideHint'),
      cropX: document.getElementById('cropX'),
      cropY: document.getElementById('cropY'),
      cropW: document.getElementById('cropW'),
      cropH: document.getElementById('cropH'),
      useFullBtn: document.getElementById('useFullBtn'),
      clearBtn: document.getElementById('clearBtn'),
      fitBtn: document.getElementById('fitBtn'),
      zoomOutBtn: document.getElementById('zoomOutBtn'),
      zoomInBtn: document.getElementById('zoomInBtn'),
      zoomLabel: document.getElementById('zoomLabel'),
      saveLabel: document.getElementById('saveLabel'),
      cropState: document.getElementById('cropState'),
      savedState: document.getElementById('savedState'),
      dirtyState: document.getElementById('dirtyState'),
      angleState: document.getElementById('angleState'),
      pageCounter: document.getElementById('pageCounter'),
      pageLabel: document.getElementById('pageLabel'),
      pageHint: document.getElementById('pageHint'),
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
      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`);
      }
      return res;
    }

    function pageStateFromUrl() {
      const pdf = Number(qs('pdf', '0')) || 0;
      const page = Number(qs('page', '1')) || 1;
      return { pdf, page: page - 1 };
    }

    function setHash(pdfIndex, pageIndex) {
      const url = new URL(window.location.href);
      url.searchParams.set('pdf', String(pdfIndex));
      url.searchParams.set('page', String(pageIndex + 1));
      history.replaceState(null, '', url);
    }

    function updateControls() {
      els.zoomLabel.textContent = state.fitToPage
        ? 'fit'
        : `${Math.round(state.displayScale * 100)}%`;
      els.savedState.textContent = state.pageData?.saved ? 'yes' : 'no';
      els.dirtyState.textContent = state.dirty ? 'yes' : 'no';
      els.cropState.textContent = state.crop ? `${state.crop.w} × ${state.crop.h}` : 'unset';
      els.angleState.textContent = state.pageData?.angle_deg == null ? 'n/a' : `${state.pageData.angle_deg.toFixed(2)}°`;
      els.pageLabel.textContent = state.pageData
        ? `${state.pageData.pdf} / page ${state.pageData.page}`
        : 'Loading...';
      els.pageHint.textContent = state.pageData?.candidate ? 'candidate page' : 'manual page';
      els.anchorEdge.value = state.pageData?.anchor_edge || 'footer';
      els.pageSideHint.value = state.pageData?.side_hint || 'n/a';
      els.offsetLabel.textContent = state.crop ? formatOffsets() : 'offsets: n/a';
      els.showAllPages.checked = state.showAllPages;
    }

    function formatOffsets() {
      if (!state.crop || !state.pageData) return 'offsets: n/a';
      const w = state.pageImageSize.width;
      const h = state.pageImageSize.height;
      const top = Math.round(state.crop.y);
      const bottom = Math.round(h - state.crop.y - state.crop.h);
      const left = Math.round(state.crop.x);
      const right = Math.round(w - state.crop.x - state.crop.w);
      return `offsets: t${top} / b${bottom} / l${left} / r${right}`;
    }

    function setDirty(dirty) {
      state.dirty = dirty;
      updateControls();
      rebuildPageList();
    }

    function updateCropInputs() {
      if (!state.crop) {
        els.cropX.value = '';
        els.cropY.value = '';
        els.cropW.value = '';
        els.cropH.value = '';
        updateControls();
        return;
      }
      els.cropX.value = String(Math.round(state.crop.x));
      els.cropY.value = String(Math.round(state.crop.y));
      els.cropW.value = String(Math.round(state.crop.w));
      els.cropH.value = String(Math.round(state.crop.h));
      updateControls();
    }

    function clampCrop(crop) {
      const w = state.pageImageSize.width;
      const h = state.pageImageSize.height;
      let x = Math.max(0, Math.min(w - 1, crop.x));
      let y = Math.max(0, Math.min(h - 1, crop.y));
      let cw = Math.max(1, Math.min(w - x, crop.w));
      let ch = Math.max(1, Math.min(h - y, crop.h));
      return { x, y, w: cw, h: ch };
    }

    function setCrop(crop, dirty = true) {
      if (!crop) {
        state.crop = null;
        updateCropInputs();
        if (dirty) {
          setDirty(true);
          scheduleSave();
        }
        redrawPage();
        return;
      }
      state.crop = clampCrop(crop);
      updateCropInputs();
      if (dirty) {
        setDirty(true);
        scheduleSave();
      }
      redrawPage();
    }

    function computeCropFromInputs() {
      if (!state.pageImageSize.width || !state.pageImageSize.height) return;
      const x = Number(els.cropX.value);
      const y = Number(els.cropY.value);
      const w = Number(els.cropW.value);
      const h = Number(els.cropH.value);
      if ([x, y, w, h].some((v) => Number.isNaN(v))) return;
      setCrop({ x, y, w, h });
    }

    function brushScale() {
      return Math.max(1, state.displayScale || 1);
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

    function fitScaleFor(width, height) {
      const availableWidth = els.workspace.clientWidth - 32;
      const availableHeight = els.workspace.clientHeight - 32;
      return Math.min(1, availableWidth / width, availableHeight / height);
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

    function redrawPage() {
      const img = state.pageImage;
      if (!img) return;
      const w = state.pageImageSize.width;
      const h = state.pageImageSize.height;
      displayCtx.save();
      displayCtx.clearRect(0, 0, els.displayCanvas.width, els.displayCanvas.height);
      displayCtx.scale(state.displayScale, state.displayScale);
      displayCtx.drawImage(img, 0, 0, w, h);
      if (state.crop) {
        displayCtx.globalAlpha = 0.16;
        displayCtx.fillStyle = '#facc15';
        displayCtx.fillRect(state.crop.x, state.crop.y, state.crop.w, state.crop.h);
        displayCtx.globalAlpha = 1.0;
      }
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

    function drawOverlay() {
      overlayCtx.clearRect(0, 0, els.overlayCanvas.width, els.overlayCanvas.height);
      if (!state.crop) return;
      overlayCtx.save();
      overlayCtx.scale(state.displayScale, state.displayScale);
      overlayCtx.strokeStyle = '#facc15';
      overlayCtx.fillStyle = 'rgba(250, 204, 21, 0.12)';
      overlayCtx.lineWidth = Math.max(1.5, 2 / brushScale());
      overlayCtx.setLineDash([]);
      overlayCtx.fillRect(state.crop.x, state.crop.y, state.crop.w, state.crop.h);
      overlayCtx.strokeRect(state.crop.x, state.crop.y, state.crop.w, state.crop.h);
      const handles = cropHandles(state.crop, true);
      overlayCtx.fillStyle = '#fde68a';
      overlayCtx.strokeStyle = '#7c5b00';
      for (const handle of handles) {
        overlayCtx.fillRect(handle.x, handle.y, handle.w, handle.h);
        overlayCtx.strokeRect(handle.x, handle.y, handle.w, handle.h);
      }
      overlayCtx.restore();
    }

    function cropHandles(crop, includeEdges = false) {
      if (!crop) return [];
      const s = Math.max(10, 14 / brushScale());
      const hs = s / 2;
      const x1 = crop.x;
      const y1 = crop.y;
      const x2 = crop.x + crop.w;
      const y2 = crop.y + crop.h;
      const xm = crop.x + crop.w / 2;
      const ym = crop.y + crop.h / 2;
      const handles = [
        { name: 'nw', x: x1 - hs, y: y1 - hs, w: s, h: s },
        { name: 'n', x: xm - hs, y: y1 - hs, w: s, h: s },
        { name: 'ne', x: x2 - hs, y: y1 - hs, w: s, h: s },
        { name: 'e', x: x2 - hs, y: ym - hs, w: s, h: s },
        { name: 'se', x: x2 - hs, y: y2 - hs, w: s, h: s },
        { name: 's', x: xm - hs, y: y2 - hs, w: s, h: s },
        { name: 'sw', x: x1 - hs, y: y2 - hs, w: s, h: s },
        { name: 'w', x: x1 - hs, y: ym - hs, w: s, h: s },
      ];
      return includeEdges ? handles : handles.filter((h) => ['n', 'e', 's', 'w', 'nw', 'ne', 'se', 'sw'].includes(h.name));
    }

    function hitTestHandle(pt, crop) {
      if (!crop) return null;
      const handles = cropHandles(crop, true);
      for (const handle of handles) {
        if (
          pt.x >= handle.x && pt.x <= handle.x + handle.w &&
          pt.y >= handle.y && pt.y <= handle.y + handle.h
        ) {
          return handle.name;
        }
      }
      return null;
    }

    function clampMinCrop(crop) {
      const minSize = 16;
      return {
        x: crop.x,
        y: crop.y,
        w: Math.max(minSize, crop.w),
        h: Math.max(minSize, crop.h),
      };
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

    function pointInCrop(pt, crop) {
      if (!crop) return false;
      return pt.x >= crop.x && pt.x <= crop.x + crop.w && pt.y >= crop.y && pt.y <= crop.y + crop.h;
    }

    function resizeCrop(handle, origin, p) {
      let left = origin.x;
      let top = origin.y;
      let right = origin.x + origin.w;
      let bottom = origin.y + origin.h;
      if (handle.includes('w')) left = Math.min(p.x, right - 16);
      if (handle.includes('e')) right = Math.max(p.x, left + 16);
      if (handle.includes('n')) top = Math.min(p.y, bottom - 16);
      if (handle.includes('s')) bottom = Math.max(p.y, top + 16);
      const next = clampCrop({
        x: left,
        y: top,
        w: right - left,
        h: bottom - top,
      });
      return clampMinCrop(next);
    }

    function pointerDown(evt) {
      evt.preventDefault();
      if (!state.pageData) return;
      const p = screenToCanvas(evt);
      const handle = state.crop ? hitTestHandle(p, state.crop) : null;
      if (handle && state.crop) {
        state.resizing = true;
        state.resizeHandle = handle;
        state.resizeOrigin = { ...state.crop };
        setCursor('grabbing');
      } else if (state.crop && pointInCrop(p, state.crop)) {
        state.moving = true;
        state.moveStart = p;
        state.moveOrigin = { ...state.crop };
        setCursor('grabbing');
      } else {
        state.drawing = true;
        state.dragStart = p;
        state.crop = { x: p.x, y: p.y, w: 1, h: 1 };
        setCursor('grabbing');
      }
      drawOverlay();
      els.overlayCanvas.setPointerCapture(evt.pointerId);
    }

    function pointerMove(evt) {
      if (!state.pageData) return;
      const p = screenToCanvas(evt);
      if (state.drawing && state.dragStart) {
        const x = Math.min(state.dragStart.x, p.x);
        const y = Math.min(state.dragStart.y, p.y);
        const w = Math.abs(p.x - state.dragStart.x);
        const h = Math.abs(p.y - state.dragStart.y);
        state.crop = clampCrop({ x, y, w: Math.max(1, w), h: Math.max(1, h) });
        scheduleOverlayRedraw();
      } else if (state.resizing && state.resizeHandle && state.resizeOrigin) {
        state.crop = resizeCrop(state.resizeHandle, state.resizeOrigin, p);
        scheduleOverlayRedraw();
      } else if (state.moving && state.moveStart && state.moveOrigin) {
        const dx = p.x - state.moveStart.x;
        const dy = p.y - state.moveStart.y;
        state.crop = clampCrop({
          x: state.moveOrigin.x + dx,
          y: state.moveOrigin.y + dy,
          w: state.moveOrigin.w,
          h: state.moveOrigin.h,
        });
        scheduleOverlayRedraw();
      } else if (!state.resizing && !state.moving && !state.drawing) {
        const handle = state.crop ? hitTestHandle(p, state.crop) : null;
        if (handle) {
          setCursor('grab');
        } else if (state.crop && pointInCrop(p, state.crop)) {
          setCursor('grab');
        } else {
          setCursor('default');
        }
      }
    }

    function pointerUp(evt) {
      if (state.drawing || state.moving || state.resizing) {
        state.drawing = false;
        state.moving = false;
        state.resizing = false;
        state.dragStart = null;
        state.moveStart = null;
        state.moveOrigin = null;
        state.resizeHandle = null;
        state.resizeOrigin = null;
        updateCropInputs();
        setDirty(true);
        scheduleSave();
        setCursor('default');
      }
      if (els.overlayCanvas.hasPointerCapture(evt.pointerId)) {
        els.overlayCanvas.releasePointerCapture(evt.pointerId);
      }
      drawOverlay();
    }

    function clearCrop() {
      state.crop = null;
      updateCropInputs();
      setDirty(true);
      scheduleSave();
      redrawPage();
    }

    function useFullPage() {
      if (!state.pageImageSize.width || !state.pageImageSize.height) return;
      setCrop({ x: 0, y: 0, w: state.pageImageSize.width, h: state.pageImageSize.height });
    }

    function setAnchorEdge(edge) {
      if (!state.pageData) return;
      state.pageData.anchor_edge = edge;
      setDirty(true);
      scheduleSave();
    }

    function renderPageList() {
      els.pageList.innerHTML = '';
      const pdf = state.pdfs[state.currentPdfIndex];
      if (!pdf) return;
      const pages = pdf.pages.filter((page) => state.showAllPages || page.candidate);
      pages.forEach((page) => {
        const item = document.createElement('div');
        item.className = 'page-item';
        const angle = page.angle_deg == null ? 'n/a' : `${page.angle_deg.toFixed(2)}°`;
        item.innerHTML = `<span>${page.number}</span><span class="muted">${angle}</span>`;
        if (page.index === state.currentPageIndex) item.classList.add('active');
        if (page.saved) item.classList.add('saved');
        if (page.dirty) item.classList.add('dirty');
        item.addEventListener('click', () => goToPage(state.currentPdfIndex, page.index));
        els.pageList.appendChild(item);
      });
    }

    function setPageDirtyFlag(dirty) {
      state.dirty = dirty;
      const pdf = state.pdfs[state.currentPdfIndex];
      if (pdf && pdf.pages[state.currentPageIndex]) {
        pdf.pages[state.currentPageIndex].dirty = dirty;
      }
      updateControls();
      renderPageList();
    }

    function scheduleSave() {
      clearTimeout(state.saveTimer);
      state.saveTimer = setTimeout(() => {
        saveCurrentPage();
      }, 650);
    }

    async function saveCurrentPage() {
      if (!state.pageData) return;
      const payload = {
        pdf: state.pageData.pdf,
        page: state.pageData.page,
        crop: state.crop,
        anchorEdge: els.anchorEdge.value,
      };
      els.saveLabel.textContent = 'saving...';
      try {
        await api('/api/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        state.pageData.saved = true;
        const pdf = state.pdfs[state.currentPdfIndex];
        if (pdf && pdf.pages[state.currentPageIndex]) {
          pdf.pages[state.currentPageIndex].saved = true;
          pdf.pages[state.currentPageIndex].dirty = false;
        }
        setPageDirtyFlag(false);
        els.saveLabel.textContent = 'saved';
      } catch (err) {
        els.saveLabel.textContent = `save failed: ${err.message}`;
      }
      renderPageList();
    }

    async function loadPage(pdfIndex, pageIndex) {
      if (state.pageData) {
        await saveCurrentPage();
      }
      const pdf = state.pdfs[pdfIndex];
      const page = pdf.pages[pageIndex];
      const res = await api(`/api/page?pdf=${encodeURIComponent(pdf.name)}&page=${page.number}`);
      const data = await res.json();

      state.currentPdfIndex = pdfIndex;
      state.currentPageIndex = pageIndex;
      state.pageData = data;
      state.crop = data.crop;
      state.fitToPage = true;
      state.zoomScale = 1;
      state.displayScale = 1;
      state.dirty = false;
      state.drawing = false;
      state.moving = false;
      state.dragStart = null;
      state.moveStart = null;
      state.moveOrigin = null;
      state.pageImage = new Image();
      state.pageImage.onload = () => {
        state.pageImageSize = { width: state.pageImage.width, height: state.pageImage.height };
        state.displayScale = fitScaleFor(state.pageImage.width, state.pageImage.height);
        resetCanvasSizes(state.pageImage.width, state.pageImage.height);
        updateCropInputs();
        redrawPage();
        updateControls();
        renderPageList();
        setPageDirtyFlag(false);
        els.saveLabel.textContent = data.saved ? 'saved' : 'loaded';
      };
      state.pageImage.src = data.pageImageUrl + `?t=${Date.now()}`;
      setHash(pdfIndex, pageIndex);
      renderPageList();
      updateControls();
    }

    function resetCanvasSizes(width, height) {
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

    async function goToPage(pdfIndex, pageIndex) {
      const pdf = state.pdfs[pdfIndex];
      if (!pdf) return;
      if (pageIndex < 0) {
        if (pdfIndex === 0) return;
        pdfIndex -= 1;
        pageIndex = state.pdfs[pdfIndex].pages.length - 1;
      }
      if (pageIndex >= pdf.pages.length) {
        if (pdfIndex >= state.pdfs.length - 1) return;
        pdfIndex += 1;
        pageIndex = 0;
      }
      const target = state.pdfs[pdfIndex].pages[pageIndex];
      if (!state.showAllPages && !target.candidate) {
        // Skip over non-candidate pages in candidate-only mode.
        let idx = pageIndex;
        let pdfIdx = pdfIndex;
        while (true) {
          idx += 1;
          if (idx >= state.pdfs[pdfIdx].pages.length) {
            if (pdfIdx >= state.pdfs.length - 1) return;
            pdfIdx += 1;
            idx = 0;
          }
          const nextPage = state.pdfs[pdfIdx].pages[idx];
          if (nextPage.candidate) {
            await loadPage(pdfIdx, idx);
            return;
          }
        }
      }
      await loadPage(pdfIndex, pageIndex);
    }

    function setupEvents() {
      els.prevBtn.addEventListener('click', () => goToPage(state.currentPdfIndex, state.currentPageIndex - 1));
      els.nextBtn.addEventListener('click', () => goToPage(state.currentPdfIndex, state.currentPageIndex + 1));
      els.showAllPages.addEventListener('change', () => {
        state.showAllPages = els.showAllPages.checked;
        renderPageList();
      });
      els.anchorEdge.addEventListener('change', () => setAnchorEdge(els.anchorEdge.value));
      [els.cropX, els.cropY, els.cropW, els.cropH].forEach((el) => {
        el.addEventListener('input', () => computeCropFromInputs());
      });
      els.useFullBtn.addEventListener('click', () => useFullPage());
      els.clearBtn.addEventListener('click', () => clearCrop());
      els.fitBtn.addEventListener('click', () => setFit());
      els.zoomOutBtn.addEventListener('click', () => zoomBy(0.9));
      els.zoomInBtn.addEventListener('click', () => zoomBy(1.1));
      els.overlayCanvas.addEventListener('pointerdown', pointerDown);
      els.overlayCanvas.addEventListener('pointermove', pointerMove);
      els.overlayCanvas.addEventListener('pointerup', pointerUp);
      els.overlayCanvas.addEventListener('pointercancel', pointerUp);
      els.overlayCanvas.addEventListener('pointerleave', () => {
        if (!state.drawing && !state.moving && !state.resizing) {
          setCursor('default');
          drawOverlay();
        }
      });
      els.overlayCanvas.addEventListener('wheel', (evt) => {
        if (!evt.ctrlKey && !evt.metaKey) return;
        evt.preventDefault();
        zoomBy(evt.deltaY < 0 ? 1.1 : 0.9, evt);
      }, { passive: false });
      window.addEventListener('resize', () => {
        if (state.pageImageSize.width && state.fitToPage) {
          state.displayScale = fitScaleFor(state.pageImageSize.width, state.pageImageSize.height);
          resetCanvasSizes(state.pageImageSize.width, state.pageImageSize.height);
          redrawPage();
        }
      });
      window.addEventListener('keydown', async (evt) => {
        if (evt.target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(evt.target.tagName)) return;
        if (evt.key === 'ArrowLeft') {
          evt.preventDefault();
          await goToPage(state.currentPdfIndex, state.currentPageIndex - 1);
        } else if (evt.key === 'ArrowRight') {
          evt.preventDefault();
          await goToPage(state.currentPdfIndex, state.currentPageIndex + 1);
        } else if (evt.key === 'f') {
          setFit();
        } else if (evt.key === '-' || evt.key === '_') {
          evt.preventDefault();
          zoomBy(0.9);
        } else if (evt.key === '=' || evt.key === '+') {
          evt.preventDefault();
          zoomBy(1.1);
        } else if (evt.key === 'u') {
          clearCrop();
        }
      });
      window.addEventListener('beforeunload', () => {
        if (state.dirty && state.pageData) {
          navigator.sendBeacon('/api/save', new Blob([JSON.stringify({
            pdf: state.pageData.pdf,
            page: state.pageData.page,
            crop: state.crop,
            anchorEdge: els.anchorEdge.value,
          })], { type: 'application/json' }));
        }
      });
    }

    async function boot() {
      const res = await api('/api/index');
      const data = await res.json();
      state.pdfs = data.pdfs;
      els.rootInfo.textContent = `${data.sourceRoot} -> ${data.cropRoot} | ${data.pageCount} pages`;
      setupEvents();
      const { pdf, page } = pageStateFromUrl();
      const pdfIndex = Math.max(0, Math.min(state.pdfs.length - 1, pdf));
      const pageIndex = Math.max(0, Math.min(state.pdfs[pdfIndex].pages.length - 1, page));
      await loadPage(pdfIndex, pageIndex);
      renderPageList();
      updateControls();
    }

    boot().catch(err => {
      document.body.innerHTML = `<pre style="white-space:pre-wrap;padding:20px;color:#fca5a5">${err.stack || err}</pre>`;
    });
  </script>
</body>
</html>
"""


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Required tool not found in PATH: {name}")
    return path


def page_sort_key(path: Path) -> tuple[int, str]:
    suffix = path.stem.rsplit("-", 1)[-1]
    try:
        return int(suffix), path.stem
    except ValueError:
        return 10**9, path.stem


def pdf_page_count(pdf_path: Path) -> int:
    proc = run(["pdfinfo", str(pdf_path)])
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise SystemExit(f"Could not determine page count for {pdf_path}")


def render_pdf(pdf_path: Path, out_dir: Path, dpi: int) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = out_dir / pdf_path.stem
    existing = list(out_dir.glob(f"{pdf_path.stem}-*.png"))
    if existing:
      for file in existing:
        file.unlink()
    run(["pdftoppm", "-png", "-r", str(dpi), str(pdf_path), str(prefix)])
    pages = sorted(out_dir.glob(f"{pdf_path.stem}-*.png"), key=page_sort_key)
    if not pages:
        raise SystemExit(f"pdftoppm produced no pages for {pdf_path}")
    return pages


def detect_footer_line(gray: np.ndarray) -> Optional[float]:
    h, w = gray.shape[:2]
    band = gray[int(h * 0.80):int(h * 0.98), :]
    if band.size == 0:
        return None
    inv = cv2.threshold(band, 220, 255, cv2.THRESH_BINARY_INV)[1]
    inv = cv2.morphologyEx(inv, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (90, 1)))
    lines = cv2.HoughLinesP(
        inv,
        1,
        np.pi / 180,
        threshold=80,
        minLineLength=int(w * 0.35),
        maxLineGap=25,
    )
    if lines is None:
        return None
    best = None
    for x1, y1, x2, y2 in lines[:, 0, :]:
        length = math.hypot(x2 - x1, y2 - y1)
        angle = math.degrees(math.atan2(y2 - y1, x2 - x1))
        score = length - abs(angle) * 12.0
        if best is None or score > best[0]:
            best = (score, angle)
    return None if best is None else float(best[1])


def page_side_hint(page_number: int) -> str:
    return "right" if (page_number + PAGE_NUMBER_OFFSET) % 2 == 1 else "left"


@dataclass
class PageInfo:
    index: int
    number: int
    path: Path
    angle_deg: Optional[float]
    candidate: bool
    saved: bool
    dirty: bool
    crop: Optional[Dict[str, Any]]
    anchor_edge: str
    side_hint: str


@dataclass
class PDFDoc:
    name: str
    pdf_path: Path
    page_count: int
    page_images: List[Path]
    pages_dir: Path
    state_dir: Path
    pages: List[PageInfo]


class CropStore:
    def __init__(self, pdf_dir: Path, source_root: Path, crop_root: Path, dpi: int) -> None:
        self.pdf_dir = pdf_dir
        self.source_root = source_root
        self.crop_root = crop_root
        self.dpi = dpi
        self.lock = threading.Lock()
        self.docs: List[PDFDoc] = []
        self._ensure_docs()

    def _ensure_docs(self) -> None:
        self.source_root.mkdir(parents=True, exist_ok=True)
        self.crop_root.mkdir(parents=True, exist_ok=True)
        for pdf_path in sorted(self.pdf_dir.glob("*.pdf")):
            page_count = pdf_page_count(pdf_path)
            doc_source_root = self.source_root / pdf_path.stem
            pages_dir = doc_source_root / "pages"
            pages_dir.mkdir(parents=True, exist_ok=True)
            page_images = sorted(pages_dir.glob(f"{pdf_path.stem}-*.png"), key=page_sort_key)
            if len(page_images) != page_count:
                page_images = render_pdf(pdf_path, pages_dir, self.dpi)

            crop_doc_root = self.crop_root / pdf_path.stem
            state_dir = crop_doc_root / "state"
            state_dir.mkdir(parents=True, exist_ok=True)
            pages = self._build_page_infos(pdf_path.stem, page_images, state_dir)
            self.docs.append(
                PDFDoc(
                    name=pdf_path.stem,
                    pdf_path=pdf_path,
                    page_count=page_count,
                    page_images=page_images,
                    pages_dir=pages_dir,
                    state_dir=state_dir,
                    pages=pages,
                )
            )

    def _read_state_file(self, state_path: Path, pdf_name: str, page: int) -> Dict[str, Any]:
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
        return {"pdf": pdf_name, "page": page, "crop": None, "anchor_edge": "footer"}

    def _build_page_infos(self, pdf_name: str, page_images: List[Path], state_dir: Path) -> List[PageInfo]:
        infos: List[PageInfo] = []
        angle_rows: List[tuple[int, float]] = []
        for idx, page_path in enumerate(page_images, start=1):
            img = cv2.imread(str(page_path), cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise SystemExit(f"Could not read page image: {page_path}")
            angle = detect_footer_line(img)
            if angle is not None:
                angle_rows.append((idx, abs(angle)))
            infos.append(
                PageInfo(
                    index=idx - 1,
                    number=idx,
                    path=page_path,
                    angle_deg=angle,
                    candidate=False,
                    saved=False,
                    dirty=False,
                    crop=None,
                    anchor_edge="footer",
                    side_hint=page_side_hint(idx),
                )
            )

        angle_rows.sort(key=lambda item: item[1])
        candidate_pages = {idx for idx, _ in angle_rows[:4]}
        for info in infos:
            info.candidate = info.number in candidate_pages
            state = self._read_state_file(state_dir / f"page-{info.number:03d}.json", pdf_name, info.number)
            info.saved = bool(state.get("updated_at"))
            info.crop = state.get("crop")
            info.anchor_edge = state.get("anchor_edge") or "footer"
            info.side_hint = page_side_hint(info.number)
        return infos

    def get_doc(self, pdf_name: str) -> PDFDoc:
        for doc in self.docs:
            if doc.name == pdf_name:
                return doc
        raise KeyError(pdf_name)

    def page_paths(self, pdf_name: str, page: int) -> Dict[str, Path]:
        doc = self.get_doc(pdf_name)
        if page < 1 or page > len(doc.page_images):
            raise IndexError(page)
        return {
            "page_image": doc.page_images[page - 1],
            "state": doc.state_dir / f"page-{page:03d}.json",
        }

    def get_state(self, pdf_name: str, page: int) -> Dict[str, Any]:
        state_path = self.page_paths(pdf_name, page)["state"]
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
        return {"pdf": pdf_name, "page": page, "crop": None, "anchor_edge": "footer"}

    def index(self) -> Dict[str, Any]:
        return {
            "sourceRoot": str(self.source_root),
            "cropRoot": str(self.crop_root),
            "pageCount": sum(doc.page_count for doc in self.docs),
            "pdfs": [
                {
                    "name": doc.name,
                    "pageCount": doc.page_count,
                    "pages": [
                        {
                            "index": page.index,
                            "number": page.number,
                            "angle_deg": page.angle_deg,
                            "candidate": page.candidate,
                            "saved": page.saved,
                            "dirty": page.dirty,
                            "crop": page.crop,
                            "anchor_edge": page.anchor_edge,
                            "side_hint": page.side_hint,
                        }
                        for page in doc.pages
                    ],
                }
                for doc in self.docs
            ],
        }

    def save_page(self, payload: Dict[str, Any]) -> None:
        pdf_name = payload["pdf"]
        page = int(payload["page"])
        crop = payload.get("crop")
        anchor_edge = payload.get("anchorEdge") or "footer"
        if crop is not None:
            crop = {
                "x": int(round(float(crop["x"]))),
                "y": int(round(float(crop["y"]))),
                "w": int(round(float(crop["w"]))),
                "h": int(round(float(crop["h"]))),
            }
        paths = self.page_paths(pdf_name, page)
        state = {
            "pdf": pdf_name,
            "page": page,
            "crop": crop,
            "anchor_edge": anchor_edge,
            "side_hint": page_side_hint(page),
            "margins": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if crop is not None:
            page_img = cv2.imread(str(paths["page_image"]), cv2.IMREAD_GRAYSCALE)
            if page_img is None:
                raise ValueError(f"Could not read page image for {pdf_name} page {page}")
            h, w = page_img.shape[:2]
            state["margins"] = {
                "top": int(round(crop["y"])),
                "bottom": int(round(h - crop["y"] - crop["h"])),
                "left": int(round(crop["x"])),
                "right": int(round(w - crop["x"] - crop["w"])),
            }
        with self.lock:
            paths["state"].write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    store: CropStore

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_bytes(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_bytes(HTML.encode("utf-8"), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/index":
            self._send_json(self.store.index())
            return
        if parsed.path == "/api/page":
            query = parse_qs(parsed.query)
            pdf = query.get("pdf", [None])[0]
            page = int(query.get("page", ["1"])[0])
            if not pdf:
                self._send_json({"error": "missing pdf"}, status=400)
                return
            paths = self.store.page_paths(pdf, page)
            state = self.store.get_state(pdf, page)
            doc = self.store.get_doc(pdf)
            body = {
                "pdf": pdf,
                "page": page,
                "pageImageUrl": f"/files/{pdf}/pages/{paths['page_image'].name}",
                "saved": bool(state.get("updated_at")),
                "crop": state.get("crop"),
                "anchor_edge": state.get("anchor_edge") or "footer",
                "side_hint": page_side_hint(page),
                "margins": state.get("margins"),
                "angle_deg": doc.pages[page - 1].angle_deg,
                "candidate": doc.pages[page - 1].candidate,
                "pageCount": doc.page_count,
            }
            self._send_json(body)
            return
        if parsed.path.startswith("/files/"):
            rel = parsed.path.removeprefix("/files/")
            full = self.store.source_root / rel
            if not full.exists() or not full.is_file():
                self.send_error(404)
                return
            self._send_bytes(full.read_bytes(), "image/png")
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/save":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self.store.save_page(payload)
        self._send_json({"ok": True})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=Path("pdfs"))
    parser.add_argument("--source-root", type=Path, default=Path("pages"))
    parser.add_argument("--crop-root", type=Path, default=Path("crop-annotations"))
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--page-number-offset",
        type=int,
        default=1,
        help="Offset applied to the scan index before determining left/right folio parity",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()

    require_tool("pdftoppm")
    require_tool("pdfinfo")

    global PAGE_NUMBER_OFFSET
    PAGE_NUMBER_OFFSET = args.page_number_offset
    store = CropStore(args.pdf_dir, args.source_root, args.crop_root, args.dpi)
    Handler.store = store
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}")
    print(f"Source root: {args.source_root}")
    print(f"Crop root: {args.crop_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

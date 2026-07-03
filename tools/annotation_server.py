#!/usr/bin/env python3
"""Local web editor for scanned-page annotations.

The editor lets you:
- navigate page by page across the three PDFs or image page sets
- paint freeform masks for text and image regions
- set a page mode: text, image, or page image
- autosave masks and per-page state as you work

Storage layout:
  annotations/
    document-name/
      pages/page-001.png
      masks/text/page-001.png
      masks/image/page-001.png
      masks/equation/page-001.png
      state/page-001.json

The masks are black PNGs. White painted areas are the annotated regions.
"""

from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, urlparse

import cv2
import numpy as np


HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PDF Annotation Editor</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #111418;
      --panel: #171b20;
      --panel-2: #0f1317;
      --line: #2b3138;
      --text: #e8edf2;
      --muted: #93a1b2;
      --accent: #7dd3fc;
      --text-layer: #ff6b6b;
      --image-layer: #5eead4;
      --equation-layer: #f59e0b;
      --warn: #fbbf24;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(180deg, #0b0f13, #111418 30%, #0b0f13 100%);
      color: var(--text);
      font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      height: 100vh;
      overflow: hidden;
    }
    button, select, input {
      font: inherit;
      color: inherit;
    }
    .app {
      display: grid;
      grid-template-columns: 290px 1fr;
      height: 100vh;
      min-height: 0;
    }
    .sidebar {
      border-right: 1px solid var(--line);
      background: rgba(15, 19, 23, 0.96);
      padding: 14px;
      overflow: auto;
    }
    .brand {
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0.02em;
      margin-bottom: 10px;
    }
    .muted { color: var(--muted); }
    .stack { display: grid; gap: 10px; }
    .card {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(23, 27, 32, 0.85);
      padding: 10px;
    }
    .row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .row > * { flex: 0 0 auto; }
    .row .grow { flex: 1 1 auto; min-width: 0; }
    .btn {
      border: 1px solid var(--line);
      background: #1a2027;
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
    }
    .btn:hover { border-color: #415062; }
    .btn:disabled {
      opacity: 0.45;
      cursor: default;
      border-color: var(--line);
    }
    .btn.primary { background: #203041; border-color: #2e4c66; }
    .btn.active { outline: 2px solid var(--accent); outline-offset: 1px; }
    .btn.text { color: #ffd1d1; }
    .btn.image { color: #c8fff5; }
    .btn.equation { color: #ffd58a; }
    .btn.tool { color: #d7e3f0; }
    .btn.tool.active { outline-color: var(--warn); }
    .brush-preview {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 3.2em;
      height: 1.75em;
      padding: 0 0.5em;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #11161d;
      color: var(--text);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.02em;
      box-shadow: inset 0 0 0 1px rgba(255,255,255,0.03);
    }
    .zoom-preview {
      min-width: 4.5em;
    }
    .small { font-size: 12px; }
    .page-list {
      display: grid;
      gap: 4px;
      max-height: 38vh;
      overflow: auto;
      padding-right: 4px;
    }
    .page-item {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      align-items: center;
      border: 1px solid transparent;
      border-radius: 8px;
      padding: 6px 8px;
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
      color: #8ce99a;
      font-size: 11px;
    }
    .page-item.dirty::after {
      content: "unsaved";
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
      background: rgba(17, 20, 24, 0.92);
      padding: 12px 14px;
      display: grid;
      gap: 10px;
    }
    .statusline {
      display: flex;
      gap: 14px;
      flex-wrap: wrap;
      align-items: center;
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
      box-shadow: 0 24px 70px rgba(0,0,0,0.45);
      background: #0b0d10;
      border: 1px solid #2a3037;
    }
    canvas {
      display: block;
      image-rendering: auto;
    }
    #displayCanvas {
      position: relative;
      left: 0;
      top: 0;
    }
    #overlayCanvas {
      position: absolute;
      left: 0;
      top: 0;
    }
    #overlayCanvas {
      pointer-events: auto;
      touch-action: none;
    }
    .hint {
      color: var(--muted);
      font-size: 12px;
    }
    .pill {
      padding: 3px 8px;
      border-radius: 999px;
      background: rgba(255,255,255,0.06);
      border: 1px solid rgba(255,255,255,0.09);
    }
    .layer-text { color: var(--text-layer); }
    .layer-image { color: var(--image-layer); }
    .sep {
      height: 1px;
      background: var(--line);
      margin: 8px 0;
    }
    .field {
      display: grid;
      gap: 4px;
    }
    .field label { color: var(--muted); font-size: 12px; }
    input[type="range"] { width: 100%; }
    .meta-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 6px;
      font-size: 12px;
    }
    .meta-grid div {
      padding: 6px 8px;
      background: rgba(255,255,255,0.03);
      border: 1px solid var(--line);
      border-radius: 8px;
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
      <div class="brand">PDF Annotation Editor</div>
      <div class="muted small" id="rootInfo"></div>
      <div class="sep"></div>
      <div class="stack">
        <div class="card">
          <div class="field">
            <label>PDF</label>
            <select id="pdfSelect" class="grow"></select>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="btn" id="prevBtn">Prev</button>
            <button class="btn" id="nextBtn">Next</button>
            <span class="pill" id="pageCounter"></span>
          </div>
        </div>
        <div class="card">
          <div class="field">
            <label>Page mode</label>
            <div class="row">
              <button class="btn" id="modeText">Text</button>
              <button class="btn" id="modeImage">Image</button>
              <button class="btn" id="modePageImage">Page Image</button>
            </div>
          </div>
          <div class="sep"></div>
          <div class="field">
            <label>Layout role</label>
            <select id="layoutRoleSelect" class="grow">
              <option value="">Body</option>
              <option value="toc">TOC</option>
              <option value="cover">Cover</option>
            </select>
          </div>
          <div class="sep"></div>
          <div class="field">
            <label>Tool</label>
            <div class="row">
              <button class="btn tool" id="toolSelect">Select</button>
              <button class="btn tool" id="toolBrush">Brush</button>
              <button class="btn tool" id="toolRect">Rectangle</button>
            </div>
          </div>
          <div class="sep"></div>
          <div class="field">
            <label>Paint layer</label>
            <div class="row">
              <button class="btn text" id="layerText">Mark Text</button>
              <button class="btn image" id="layerImage">Mark Image</button>
              <button class="btn equation" id="layerEquation">Mark Equation</button>
            </div>
          </div>
          <div class="sep"></div>
          <div class="field">
            <label>Brush size <span id="brushValue"></span></label>
            <input id="brushSize" type="range" min="4" max="180" value="26" />
            <div class="row" style="margin-top:8px;">
              <span class="brush-preview" id="brushPreview">text</span>
            </div>
          </div>
          <div class="row" style="margin-top:8px;">
            <button class="btn" id="eraseBtn">Erase off</button>
            <button class="btn" id="undoBtn">Undo</button>
            <button class="btn" id="clearBtn">Clear layer</button>
          </div>
            <div class="row" style="margin-top:8px;">
              <button class="btn" id="toggleOverlayBtn">Toggle overlays</button>
              <button class="btn" id="refreshBtn" title="Reload the current page from saved masks">Refresh masks</button>
              <button class="btn" id="zoomOutBtn">Zoom -</button>
              <button class="btn" id="zoomInBtn">Zoom +</button>
              <button class="btn" id="fitBtn">Fit page</button>
              <span class="brush-preview zoom-preview" id="zoomPreview">100%</span>
            </div>
          <div class="sep"></div>
          <div class="field">
            <label>Selected region</label>
            <div class="row small">
              <span class="pill" id="selectionInfo">none</span>
              <span class="pill" id="selectionBox">-</span>
            </div>
            <div class="row" style="margin-top:8px;">
              <button class="btn text" id="selectionToText">To Text</button>
              <button class="btn image" id="selectionToImage">To Image</button>
              <button class="btn equation" id="selectionToEquation">To Equation</button>
              <button class="btn" id="clearSelectionBtn">Clear selection</button>
            </div>
            <div class="hint" style="margin-top:8px;">Use Select to pick a connected mask region. Drag inside to move. Drag edges or corners to resize.</div>
          </div>
        </div>
        <div class="card">
          <div class="meta-grid">
            <div><strong>Text px</strong><br><span id="textPixels">0</span></div>
            <div><strong>Image px</strong><br><span id="imagePixels">0</span></div>
            <div><strong>Saved</strong><br><span id="savedState">no</span></div>
            <div><strong>Dirty</strong><br><span id="dirtyState">no</span></div>
          </div>
        </div>
        <div class="card">
          <div class="hint">
            Shortcuts:
            <div><span class="kbd">←</span> / <span class="kbd">→</span> page nav</div>
            <div><span class="kbd">t</span> text layer, <span class="kbd">i</span> image layer</div>
            <div><span class="kbd">s</span> select tool, <span class="kbd">b</span> brush, <span class="kbd">r</span> rectangle</div>
            <div><span class="kbd">1</span> text mode, <span class="kbd">2</span> image mode, <span class="kbd">3</span> page image</div>
            <div><span class="kbd">-</span>/<span class="kbd">+</span> zoom, <span class="kbd">f</span> fit page</div>
            <div><span class="kbd">u</span> undo, <span class="kbd">c</span> clear layer</div>
          </div>
        </div>
      </div>
      <div class="sep"></div>
      <div class="small muted">Pages</div>
      <div id="pageList" class="page-list"></div>
    </aside>
    <main class="main">
      <div class="toolbar">
        <div class="statusline">
          <span class="pill" id="pageLabel">Loading...</span>
          <span class="pill" id="modeLabel">mode: unset</span>
          <span class="pill" id="layerLabel">layer: text</span>
          <span class="pill" id="saveLabel">autosave ready</span>
        </div>
        <div class="hint">Paint on the selected layer. The editor autosaves when you stop drawing and when you switch pages.</div>
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
      brushSize: 26,
      activeLayer: 'text',
      tool: 'brush',
      eraseMode: false,
      pageMode: null,
      layoutRole: null,
      overlayVisible: true,
      fitToPage: true,
      zoomScale: 1,
      drawing: false,
      dirty: false,
      saveTimer: null,
      undoHistory: [],
      loadedMasks: { text: null, image: null, equation: null },
      pageImage: null,
      displayScale: 1,
      pageImageSize: { width: 0, height: 0 },
      hoverPoint: null,
      rectStart: null,
      rectEnd: null,
      selection: null,
      transform: null,
    };

    const els = {
      rootInfo: document.getElementById('rootInfo'),
      pdfSelect: document.getElementById('pdfSelect'),
      prevBtn: document.getElementById('prevBtn'),
      nextBtn: document.getElementById('nextBtn'),
      pageCounter: document.getElementById('pageCounter'),
      modeText: document.getElementById('modeText'),
      modeImage: document.getElementById('modeImage'),
      modePageImage: document.getElementById('modePageImage'),
      layoutRoleSelect: document.getElementById('layoutRoleSelect'),
      toolSelect: document.getElementById('toolSelect'),
      toolBrush: document.getElementById('toolBrush'),
      toolRect: document.getElementById('toolRect'),
      layerText: document.getElementById('layerText'),
      layerImage: document.getElementById('layerImage'),
      layerEquation: document.getElementById('layerEquation'),
      brushSize: document.getElementById('brushSize'),
      brushValue: document.getElementById('brushValue'),
      brushPreview: document.getElementById('brushPreview'),
      eraseBtn: document.getElementById('eraseBtn'),
      undoBtn: document.getElementById('undoBtn'),
      clearBtn: document.getElementById('clearBtn'),
      toggleOverlayBtn: document.getElementById('toggleOverlayBtn'),
      refreshBtn: document.getElementById('refreshBtn'),
      zoomOutBtn: document.getElementById('zoomOutBtn'),
      zoomInBtn: document.getElementById('zoomInBtn'),
      fitBtn: document.getElementById('fitBtn'),
      zoomPreview: document.getElementById('zoomPreview'),
      selectionInfo: document.getElementById('selectionInfo'),
      selectionBox: document.getElementById('selectionBox'),
      selectionToText: document.getElementById('selectionToText'),
      selectionToImage: document.getElementById('selectionToImage'),
      selectionToEquation: document.getElementById('selectionToEquation'),
      clearSelectionBtn: document.getElementById('clearSelectionBtn'),
      textPixels: document.getElementById('textPixels'),
      imagePixels: document.getElementById('imagePixels'),
      savedState: document.getElementById('savedState'),
      dirtyState: document.getElementById('dirtyState'),
      pageLabel: document.getElementById('pageLabel'),
      modeLabel: document.getElementById('modeLabel'),
      layerLabel: document.getElementById('layerLabel'),
      saveLabel: document.getElementById('saveLabel'),
      pageList: document.getElementById('pageList'),
      workspace: document.getElementById('workspace'),
      canvasWrap: document.getElementById('canvasWrap'),
      displayCanvas: document.getElementById('displayCanvas'),
      overlayCanvas: document.getElementById('overlayCanvas'),
    };

    const displayCtx = els.displayCanvas.getContext('2d');
    const overlayCtx = els.overlayCanvas.getContext('2d');
    const offscreen = {
      text: document.createElement('canvas'),
      image: document.createElement('canvas'),
      equation: document.createElement('canvas'),
    };
    const offCtx = {
      text: offscreen.text.getContext('2d'),
      image: offscreen.image.getContext('2d'),
      equation: offscreen.equation.getContext('2d'),
    };
    const brushColors = {
      text: '#ff6b7d',
      image: '#2dd4bf',
      equation: '#f59e0b',
      'page-image': '#fbbf24',
    };
    const layerNames = ['text', 'image', 'equation'];
    const handleCursor = {
      n: 'ns-resize',
      s: 'ns-resize',
      e: 'ew-resize',
      w: 'ew-resize',
      ne: 'nesw-resize',
      sw: 'nesw-resize',
      nw: 'nwse-resize',
      se: 'nwse-resize',
      body: 'grab',
    };

    function qs(name, fallback = null) {
      const url = new URL(window.location.href);
      return url.searchParams.get(name) ?? fallback;
    }

    function setHash(pdfIndex, pageIndex) {
      const url = new URL(window.location.href);
      url.searchParams.set('pdf', String(pdfIndex));
      url.searchParams.set('page', String(pageIndex));
      history.replaceState(null, '', url);
    }

    async function api(path, options) {
      const res = await fetch(path, options);
      if (!res.ok) {
        throw new Error(`${res.status} ${res.statusText}`);
      }
      return res;
    }

    function updateControls() {
      els.brushValue.textContent = `${state.brushSize}px`;
      els.modeLabel.textContent = `mode: ${state.pageMode ?? 'unset'}${state.layoutRole ? ` / ${state.layoutRole}` : ''}`;
      els.layoutRoleSelect.value = state.layoutRole ?? '';
      els.layerLabel.textContent = `layer: ${state.activeLayer}`;
      els.toolSelect.classList.toggle('active', state.tool === 'select');
      els.toolBrush.classList.toggle('active', state.tool === 'brush');
      els.toolRect.classList.toggle('active', state.tool === 'rect');
      els.brushPreview.textContent = state.activeLayer;
      els.brushPreview.style.background = brushColors[state.activeLayer] || '#11161d';
      els.brushPreview.style.color = '#081017';
      els.zoomPreview.textContent = state.fitToPage
        ? 'fit'
        : `${Math.round(state.displayScale * 100)}%`;
      els.savedState.textContent = state.dirty ? 'no' : 'yes';
      els.dirtyState.textContent = state.dirty ? 'yes' : 'no';
      const pdf = state.pdfs[state.currentPdfIndex];
      const totalPages = pdf ? pdf.pages.length : 0;
      els.pageCounter.textContent = `${state.currentPageIndex + 1}/${totalPages}`;
      els.pageLabel.textContent = state.pageData ? `${state.pageData.pdf} / page ${state.pageData.page}` : 'Loading...';
      els.modeText.classList.toggle('active', state.pageMode === 'text');
      els.modeImage.classList.toggle('active', state.pageMode === 'image');
      els.modePageImage.classList.toggle('active', state.pageMode === 'page-image');
      els.layerText.classList.toggle('active', state.activeLayer === 'text');
      els.layerImage.classList.toggle('active', state.activeLayer === 'image');
      els.layerEquation.classList.toggle('active', state.activeLayer === 'equation');
      els.toggleOverlayBtn.classList.toggle('active', state.overlayVisible);
      els.fitBtn.classList.toggle('active', state.fitToPage);
      els.eraseBtn.classList.toggle('active', state.eraseMode);
      els.eraseBtn.textContent = state.eraseMode ? 'Erase on' : 'Erase off';
      const selectionActive = Boolean(state.selection);
      els.selectionInfo.textContent = selectionActive
        ? `${state.selection.sourceLayer} -> ${state.selection.currentLayer}`
        : 'none';
      if (selectionActive) {
        const box = state.transform?.previewBox || state.selection.bbox;
        els.selectionBox.textContent = `${Math.round(box.x)}, ${Math.round(box.y)} / ${Math.round(box.w)} x ${Math.round(box.h)}`;
      } else {
        els.selectionBox.textContent = '-';
      }
      els.selectionToText.disabled = !selectionActive || state.selection.currentLayer === 'text';
      els.selectionToImage.disabled = !selectionActive || state.selection.currentLayer === 'image';
      els.selectionToEquation.disabled = !selectionActive || state.selection.currentLayer === 'equation';
      els.clearSelectionBtn.disabled = !selectionActive;
    }

    function brushColorFor(layer) {
      return brushColors[layer] || '#ffffff';
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
      redraw();
      if (anchor && contentX !== null && contentY !== null && anchorViewX !== null && anchorViewY !== null) {
        const targetLeft = (contentX * state.displayScale) - anchorViewX;
        const targetTop = (contentY * state.displayScale) - anchorViewY;
        els.workspace.scrollLeft = Math.max(0, targetLeft);
        els.workspace.scrollTop = Math.max(0, targetTop);
      }
      updateControls();
    }

    function setFitPage() {
      setScale(state.zoomScale || 1, true);
    }

    function zoomBy(factor, anchor = null) {
      const next = Math.max(0.1, Math.min(8, (state.fitToPage ? state.displayScale : state.zoomScale) * factor));
      setScale(next, false, anchor);
    }

    function clearPreview() {
      state.hoverPoint = null;
      state.rectEnd = null;
      overlayCtx.clearRect(0, 0, els.overlayCanvas.width, els.overlayCanvas.height);
    }

    function clearSelection() {
      state.selection = null;
      state.transform = null;
      updateControls();
    }

    function currentSelectionBox() {
      if (state.transform && state.transform.previewBox) return state.transform.previewBox;
      return state.selection ? state.selection.bbox : null;
    }

    function normalizeBox(box) {
      const width = state.pageImageSize.width || 0;
      const height = state.pageImageSize.height || 0;
      const minSize = 4;
      let x = Math.max(0, Math.min(width - 1, box.x));
      let y = Math.max(0, Math.min(height - 1, box.y));
      let w = Math.max(minSize, box.w);
      let h = Math.max(minSize, box.h);
      if (x + w > width) {
        x = Math.max(0, width - w);
        w = Math.min(w, width);
      }
      if (y + h > height) {
        y = Math.max(0, height - h);
        h = Math.min(h, height);
      }
      return {
        x: Math.round(x),
        y: Math.round(y),
        w: Math.round(w),
        h: Math.round(h),
      };
    }

    function selectionHandleRadius() {
      return 10 / Math.max(0.45, state.displayScale || 1);
    }

    function selectionHandles(box) {
      const midX = box.x + (box.w / 2);
      const midY = box.y + (box.h / 2);
      return {
        nw: { x: box.x, y: box.y },
        n: { x: midX, y: box.y },
        ne: { x: box.x + box.w, y: box.y },
        e: { x: box.x + box.w, y: midY },
        se: { x: box.x + box.w, y: box.y + box.h },
        s: { x: midX, y: box.y + box.h },
        sw: { x: box.x, y: box.y + box.h },
        w: { x: box.x, y: midY },
      };
    }

    function hitTestSelection(point) {
      const box = currentSelectionBox();
      if (!box) return null;
      const handleRadius = selectionHandleRadius();
      for (const [name, handle] of Object.entries(selectionHandles(box))) {
        if (Math.abs(point.x - handle.x) <= handleRadius && Math.abs(point.y - handle.y) <= handleRadius) {
          return { type: 'handle', handle: name };
        }
      }
      if (
        point.x >= box.x &&
        point.x <= box.x + box.w &&
        point.y >= box.y &&
        point.y <= box.y + box.h
      ) {
        return { type: 'body', handle: 'body' };
      }
      return null;
    }

    function applyResize(box, handle, dx, dy) {
      const right = box.x + box.w;
      const bottom = box.y + box.h;
      let nextLeft = box.x;
      let nextTop = box.y;
      let nextRight = right;
      let nextBottom = bottom;
      if (handle.includes('w')) nextLeft += dx;
      if (handle.includes('e')) nextRight += dx;
      if (handle.includes('n')) nextTop += dy;
      if (handle.includes('s')) nextBottom += dy;
      if (nextRight - nextLeft < 4) {
        if (handle.includes('w')) nextLeft = nextRight - 4;
        else nextRight = nextLeft + 4;
      }
      if (nextBottom - nextTop < 4) {
        if (handle.includes('n')) nextTop = nextBottom - 4;
        else nextBottom = nextTop + 4;
      }
      return normalizeBox({
        x: nextLeft,
        y: nextTop,
        w: nextRight - nextLeft,
        h: nextBottom - nextTop,
      });
    }

    function updateCanvasCursor() {
      let cursor = 'crosshair';
      if (state.tool === 'select') {
        if (state.transform) {
          cursor = state.transform.mode === 'move'
            ? 'grabbing'
            : (handleCursor[state.transform.handle] || 'grabbing');
        } else if (state.hoverPoint) {
          const hit = hitTestSelection(state.hoverPoint);
          cursor = hit ? (handleCursor[hit.handle] || 'grab') : 'default';
        } else {
          cursor = 'default';
        }
      } else if (state.tool === 'brush') {
        cursor = state.eraseMode ? 'cell' : 'crosshair';
      }
      els.overlayCanvas.style.cursor = cursor;
    }

    function imageDataToCanvas(imageData) {
      const canvas = document.createElement('canvas');
      canvas.width = imageData.width;
      canvas.height = imageData.height;
      canvas.getContext('2d').putImageData(imageData, 0, 0);
      return canvas;
    }

    function tintedMaskCanvas(imageData, layer) {
      const maskCanvas = imageDataToCanvas(imageData);
      const tinted = document.createElement('canvas');
      tinted.width = imageData.width;
      tinted.height = imageData.height;
      const ctx = tinted.getContext('2d');
      ctx.fillStyle = brushColorFor(layer);
      ctx.fillRect(0, 0, tinted.width, tinted.height);
      ctx.globalCompositeOperation = 'destination-in';
      ctx.drawImage(maskCanvas, 0, 0);
      ctx.globalCompositeOperation = 'source-over';
      return tinted;
    }

    function scaledSelectionCanvas(imageData, width, height, layer) {
      const source = tintedMaskCanvas(imageData, layer);
      const scaled = document.createElement('canvas');
      scaled.width = Math.max(1, Math.round(width));
      scaled.height = Math.max(1, Math.round(height));
      const ctx = scaled.getContext('2d');
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, scaled.width, scaled.height);
      ctx.drawImage(source, 0, 0, source.width, source.height, 0, 0, scaled.width, scaled.height);
      return scaled;
    }

    function captureAllLayerSnapshots() {
      const snapshots = {};
      for (const layer of layerNames) {
        snapshots[layer] = offCtx[layer].getImageData(0, 0, offscreen[layer].width, offscreen[layer].height);
      }
      return snapshots;
    }

    function findConnectedRegionInLayer(layer, point) {
      const width = offscreen[layer].width;
      const height = offscreen[layer].height;
      const startX = Math.floor(point.x);
      const startY = Math.floor(point.y);
      if (startX < 0 || startY < 0 || startX >= width || startY >= height) return null;
      const imageData = offCtx[layer].getImageData(0, 0, width, height);
      const data = imageData.data;
      const startIndex = startY * width + startX;
      if (data[(startIndex * 4) + 3] === 0) return null;
      const visited = new Uint8Array(width * height);
      const stack = [startIndex];
      const pixels = [];
      visited[startIndex] = 1;
      let minX = startX;
      let maxX = startX;
      let minY = startY;
      let maxY = startY;
      while (stack.length) {
        const index = stack.pop();
        pixels.push(index);
        const x = index % width;
        const y = Math.floor(index / width);
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
        const neighbors = [];
        if (x > 0) neighbors.push(index - 1);
        if (x + 1 < width) neighbors.push(index + 1);
        if (y > 0) neighbors.push(index - width);
        if (y + 1 < height) neighbors.push(index + width);
        for (const next of neighbors) {
          if (visited[next]) continue;
          visited[next] = 1;
          if (data[(next * 4) + 3] > 0) {
            stack.push(next);
          }
        }
      }
      const boxWidth = (maxX - minX) + 1;
      const boxHeight = (maxY - minY) + 1;
      const region = new ImageData(boxWidth, boxHeight);
      for (const index of pixels) {
        const x = index % width;
        const y = Math.floor(index / width);
        const srcOffset = index * 4;
        const dstOffset = (((y - minY) * boxWidth) + (x - minX)) * 4;
        region.data[dstOffset] = data[srcOffset];
        region.data[dstOffset + 1] = data[srcOffset + 1];
        region.data[dstOffset + 2] = data[srcOffset + 2];
        region.data[dstOffset + 3] = data[srcOffset + 3];
      }
      return {
        sourceLayer: layer,
        currentLayer: layer,
        bbox: { x: minX, y: minY, w: boxWidth, h: boxHeight },
        imageData: region,
      };
    }

    function findRegionAtPoint(point) {
      const regions = [];
      for (const layer of layerNames) {
        const region = findConnectedRegionInLayer(layer, point);
        if (region) {
          const area = region.bbox.w * region.bbox.h;
          regions.push({
            ...region,
            area,
            activePriority: layer === state.activeLayer ? 0 : 1,
          });
        }
      }
      if (!regions.length) return null;
      regions.sort((a, b) => {
        if (a.area !== b.area) return a.area - b.area;
        if (a.activePriority !== b.activePriority) return a.activePriority - b.activePriority;
        return layerNames.indexOf(a.currentLayer) - layerNames.indexOf(b.currentLayer);
      });
      return regions[0];
    }

    function sameRegion(a, b) {
      if (!a || !b) return false;
      return (
        a.currentLayer === b.currentLayer &&
        a.bbox.x === b.bbox.x &&
        a.bbox.y === b.bbox.y &&
        a.bbox.w === b.bbox.w &&
        a.bbox.h === b.bbox.h
      );
    }

    function commitSelection(targetLayer = null, targetBox = null) {
      if (!state.selection) return;
      const sourceLayer = state.selection.sourceLayer;
      const nextLayer = targetLayer || state.selection.currentLayer || sourceLayer;
      const nextBox = normalizeBox(targetBox || state.selection.bbox);
      state.undoHistory.push({ kind: 'all', snapshots: captureAllLayerSnapshots() });
      if (state.undoHistory.length > 18) {
        state.undoHistory.shift();
      }

      const sourceMask = imageDataToCanvas(state.selection.imageData);
      const sourceCtx = offCtx[sourceLayer];
      sourceCtx.save();
      sourceCtx.globalCompositeOperation = 'destination-out';
      sourceCtx.drawImage(sourceMask, state.selection.bbox.x, state.selection.bbox.y);
      sourceCtx.restore();

      const scaled = scaledSelectionCanvas(state.selection.imageData, nextBox.w, nextBox.h, nextLayer);
      const targetCtx = offCtx[nextLayer];
      targetCtx.save();
      targetCtx.imageSmoothingEnabled = false;
      targetCtx.drawImage(scaled, nextBox.x, nextBox.y);
      targetCtx.restore();

      state.selection = {
        sourceLayer: nextLayer,
        currentLayer: nextLayer,
        bbox: nextBox,
        imageData: scaled.getContext('2d').getImageData(0, 0, scaled.width, scaled.height),
      };
      state.transform = null;
      setDirty(true);
      redraw();
      scheduleSave();
      updateControls();
    }

    function drawPreview() {
      overlayCtx.clearRect(0, 0, els.overlayCanvas.width, els.overlayCanvas.height);
      if (!state.pageImageSize.width || !state.pageImageSize.height) return;
      overlayCtx.save();
      overlayCtx.scale(state.displayScale, state.displayScale);
      const color = brushColorFor(state.activeLayer);
      overlayCtx.strokeStyle = color;
      overlayCtx.fillStyle = color;
      overlayCtx.lineWidth = Math.max(1, 2 / Math.max(0.5, state.displayScale));
      overlayCtx.setLineDash([8 / Math.max(0.5, state.displayScale), 6 / Math.max(0.5, state.displayScale)]);
      if (state.tool === 'brush' && state.hoverPoint && !state.drawing) {
        overlayCtx.beginPath();
        overlayCtx.arc(state.hoverPoint.x, state.hoverPoint.y, state.brushSize, 0, Math.PI * 2);
        overlayCtx.stroke();
      } else if (state.selection) {
        const box = currentSelectionBox();
        overlayCtx.strokeStyle = brushColorFor(state.selection.currentLayer);
        overlayCtx.fillStyle = brushColorFor(state.selection.currentLayer);
        overlayCtx.setLineDash([]);
        overlayCtx.lineWidth = Math.max(2 / Math.max(0.5, state.displayScale), 1);
        overlayCtx.strokeRect(box.x, box.y, box.w, box.h);
        const handleSize = selectionHandleRadius() * 1.2;
        for (const handle of Object.values(selectionHandles(box))) {
          overlayCtx.fillRect(handle.x - handleSize / 2, handle.y - handleSize / 2, handleSize, handleSize);
        }
      } else if (state.tool === 'rect' && state.rectStart && state.rectEnd) {
        const x = Math.min(state.rectStart.x, state.rectEnd.x);
        const y = Math.min(state.rectStart.y, state.rectEnd.y);
        const w = Math.abs(state.rectEnd.x - state.rectStart.x);
        const h = Math.abs(state.rectEnd.y - state.rectStart.y);
        overlayCtx.strokeRect(x, y, w, h);
      } else if (state.tool === 'rect' && state.hoverPoint && !state.drawing) {
        const size = Math.max(12, state.brushSize * 2);
        overlayCtx.strokeRect(state.hoverPoint.x - size / 2, state.hoverPoint.y - size / 2, size, size);
      }
      overlayCtx.restore();
      updateCanvasCursor();
    }

    function pageStateFromUrl() {
      const pdf = Number(qs('pdf', '0')) || 0;
      const page = Number(qs('page', '1')) || 1;
      return { pdf, page: page - 1 };
    }

    function makePageItem(page, index) {
      const el = document.createElement('div');
      el.className = 'page-item';
      el.dataset.index = String(index);
      const role = page.layoutRole ? ` / ${page.layoutRole}` : '';
      el.innerHTML = `<span>${page.number}</span><span class="muted">${page.mode}${role}</span>`;
      el.addEventListener('click', () => goToPage(state.currentPdfIndex, index));
      return el;
    }

    function rebuildPageList() {
      els.pageList.innerHTML = '';
      const pages = state.pdfs[state.currentPdfIndex]?.pages ?? [];
      pages.forEach((page, index) => {
        const item = makePageItem(page, index);
        if (index === state.currentPageIndex) item.classList.add('active');
        if (page.saved) item.classList.add('saved');
        if (page.dirty) item.classList.add('dirty');
        els.pageList.appendChild(item);
      });
    }

    function setDirty(dirty) {
      state.dirty = dirty;
      const pdf = state.pdfs[state.currentPdfIndex];
      if (pdf && pdf.pages[state.currentPageIndex]) {
        pdf.pages[state.currentPageIndex].dirty = dirty;
      }
      updateControls();
      rebuildPageList();
    }

    function clearUndo() {
      state.undoHistory = [];
    }

    function pushUndo(layer) {
      const canvas = offscreen[layer];
      const ctx = offCtx[layer];
      const snapshot = ctx.getImageData(0, 0, canvas.width, canvas.height);
      state.undoHistory.push({ kind: 'layer', layer, snapshot });
      if (state.undoHistory.length > 18) {
        state.undoHistory.shift();
      }
    }

    function restoreUndo() {
      if (!state.undoHistory.length) return;
      const entry = state.undoHistory.pop();
      if (entry.kind === 'all') {
        for (const layer of layerNames) {
          offCtx[layer].putImageData(entry.snapshots[layer], 0, 0);
        }
      } else {
        offCtx[entry.layer].putImageData(entry.snapshot, 0, 0);
      }
      clearSelection();
      setDirty(true);
      redraw();
      scheduleSave();
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

    function resetAnnotationCanvases(width, height) {
      offscreen.text.width = width;
      offscreen.text.height = height;
      offscreen.image.width = width;
      offscreen.image.height = height;
      offscreen.equation.width = width;
      offscreen.equation.height = height;
    }

    function fitScaleFor(width, height) {
      const availableWidth = els.workspace.clientWidth - 32;
      const availableHeight = els.workspace.clientHeight - 32;
      return Math.min(1, availableWidth / width, availableHeight / height);
    }

    function redraw() {
      const img = state.pageImage;
      if (!img) return;
      const w = state.pageImageSize.width;
      const h = state.pageImageSize.height;
      displayCtx.save();
      displayCtx.clearRect(0, 0, els.displayCanvas.width, els.displayCanvas.height);
      displayCtx.scale(state.displayScale, state.displayScale);
      displayCtx.drawImage(img, 0, 0, w, h);
      if (state.overlayVisible) {
        displayCtx.globalAlpha = 0.28;
        displayCtx.drawImage(offscreen.text, 0, 0, w, h);
        displayCtx.globalAlpha = 0.28;
        displayCtx.drawImage(offscreen.image, 0, 0, w, h);
        displayCtx.globalAlpha = 0.28;
        displayCtx.drawImage(offscreen.equation, 0, 0, w, h);
        displayCtx.globalAlpha = 1.0;
      }
      displayCtx.restore();
      drawPreview();
    }

    function renderPixels() {
      const t = offCtx.text.getImageData(0, 0, offscreen.text.width || 1, offscreen.text.height || 1).data;
      const i = offCtx.image.getImageData(0, 0, offscreen.image.width || 1, offscreen.image.height || 1).data;
      const e = offCtx.equation.getImageData(0, 0, offscreen.equation.width || 1, offscreen.equation.height || 1).data;
      let tp = 0, ip = 0;
      for (let idx = 3; idx < t.length; idx += 4) if (t[idx] > 0) tp++;
      for (let idx = 3; idx < i.length; idx += 4) if (i[idx] > 0) ip++;
      let ep = 0;
      for (let idx = 3; idx < e.length; idx += 4) if (e[idx] > 0) ep++;
      els.textPixels.textContent = tp.toLocaleString();
      els.imagePixels.textContent = ip.toLocaleString();
    }

    function screenToCanvas(evt) {
      const rect = els.overlayCanvas.getBoundingClientRect();
      const scaleX = state.pageImageSize.width / rect.width;
      const scaleY = state.pageImageSize.height / rect.height;
      const x = (evt.clientX - rect.left) * scaleX;
      const y = (evt.clientY - rect.top) * scaleY;
      return { x, y };
    }

    function drawPoint(layer, x, y, erase=false) {
      const ctx = offCtx[layer];
      ctx.save();
      ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
      ctx.fillStyle = brushColorFor(layer);
      ctx.beginPath();
      ctx.arc(x, y, state.brushSize, 0, Math.PI * 2);
      ctx.fill();
      ctx.restore();
    }

    function strokeLine(layer, from, to, erase=false) {
      const ctx = offCtx[layer];
      ctx.save();
      ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
      ctx.strokeStyle = brushColorFor(layer);
      ctx.lineWidth = state.brushSize * 2;
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(from.x, from.y);
      ctx.lineTo(to.x, to.y);
      ctx.stroke();
      ctx.restore();
    }

    function fillRect(layer, a, b, erase=false) {
      const ctx = offCtx[layer];
      let x = Math.min(a.x, b.x);
      let y = Math.min(a.y, b.y);
      let w = Math.max(1, Math.abs(b.x - a.x));
      let h = Math.max(1, Math.abs(b.y - a.y));
      if (w < 2 && h < 2) {
        w = state.brushSize * 2;
        h = state.brushSize * 2;
        x = a.x - (w / 2);
        y = a.y - (h / 2);
      }
      ctx.save();
      ctx.globalCompositeOperation = erase ? 'destination-out' : 'source-over';
      ctx.fillStyle = brushColorFor(layer);
      ctx.fillRect(x, y, w, h);
      ctx.restore();
    }

    let lastPoint = null;
    let rectAnchor = null;
    function isErasing(evt) {
      return state.eraseMode || Boolean(evt && evt.altKey);
    }

    function pointerDown(evt) {
      evt.preventDefault();
      if (!state.pageData) return;
      const p = screenToCanvas(evt);
      if (state.tool === 'select') {
        state.hoverPoint = p;
        const hit = hitTestSelection(p);
        if (hit && hit.type === 'handle' && state.selection) {
          state.transform = {
            mode: 'resize',
            handle: hit.handle,
            startPoint: p,
            startBox: { ...state.selection.bbox },
            previewBox: { ...state.selection.bbox },
          };
          state.drawing = true;
          els.overlayCanvas.setPointerCapture(evt.pointerId);
          redraw();
          return;
        }
        const region = findRegionAtPoint(p);
        if (region) {
          state.activeLayer = region.currentLayer;
          if (!sameRegion(region, state.selection)) {
            state.selection = region;
            state.transform = null;
            updateControls();
            redraw();
            return;
          }
          state.transform = {
            mode: 'move',
            handle: 'body',
            startPoint: p,
            startBox: { ...state.selection.bbox },
            previewBox: { ...state.selection.bbox },
          };
          state.drawing = true;
          els.overlayCanvas.setPointerCapture(evt.pointerId);
          updateControls();
          redraw();
          return;
        } else if (hit && hit.type === 'body' && state.selection) {
          state.transform = {
            mode: 'move',
            handle: 'body',
            startPoint: p,
            startBox: { ...state.selection.bbox },
            previewBox: { ...state.selection.bbox },
          };
          state.drawing = true;
          els.overlayCanvas.setPointerCapture(evt.pointerId);
          updateControls();
          redraw();
          return;
        } else {
          clearSelection();
        }
        redraw();
        return;
      }
      clearSelection();
      const layer = state.activeLayer;
      const erasing = isErasing(evt);
      pushUndo(layer);
      state.drawing = true;
      if (state.tool === 'brush') {
        lastPoint = p;
        drawPoint(layer, p.x, p.y, erasing);
      } else {
        rectAnchor = p;
        state.rectStart = p;
        state.rectEnd = p;
      }
      redraw();
      setDirty(true);
      scheduleSave();
      els.overlayCanvas.setPointerCapture(evt.pointerId);
    }

    function pointerMove(evt) {
      const p = screenToCanvas(evt);
      state.hoverPoint = p;
      if (state.tool === 'select' && state.drawing && state.transform && state.selection) {
        const dx = p.x - state.transform.startPoint.x;
        const dy = p.y - state.transform.startPoint.y;
        if (state.transform.mode === 'move') {
          state.transform.previewBox = normalizeBox({
            x: state.transform.startBox.x + dx,
            y: state.transform.startBox.y + dy,
            w: state.transform.startBox.w,
            h: state.transform.startBox.h,
          });
        } else {
          state.transform.previewBox = applyResize(state.transform.startBox, state.transform.handle, dx, dy);
        }
      } else if (state.drawing) {
        const erasing = isErasing(evt);
        if (state.tool === 'brush') {
          strokeLine(state.activeLayer, lastPoint, p, erasing);
          lastPoint = p;
        } else if (state.tool === 'rect') {
          state.rectEnd = p;
        }
        setDirty(true);
        scheduleSave();
      }
      redraw();
    }

    function pointerUp(evt) {
      if (!state.drawing) return;
      if (state.tool === 'select') {
        state.drawing = false;
        if (state.selection && state.transform && state.transform.previewBox) {
          commitSelection(state.selection.currentLayer, state.transform.previewBox);
        } else {
          state.transform = null;
          redraw();
        }
        if (els.overlayCanvas.hasPointerCapture(evt.pointerId)) {
          els.overlayCanvas.releasePointerCapture(evt.pointerId);
        }
        return;
      }
      const p = screenToCanvas(evt);
      const erasing = isErasing(evt);
      if (state.tool === 'rect' && rectAnchor) {
        state.rectEnd = p;
        fillRect(state.activeLayer, rectAnchor, p, erasing);
      }
      state.drawing = false;
      lastPoint = null;
      rectAnchor = null;
      state.rectStart = null;
      state.rectEnd = null;
      redraw();
      scheduleSave();
      if (els.overlayCanvas.hasPointerCapture(evt.pointerId)) {
        els.overlayCanvas.releasePointerCapture(evt.pointerId);
      }
    }

    function fillCanvas(canvas, color) {
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (color && color !== 'black') {
        ctx.save();
        ctx.fillStyle = color;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.restore();
      }
    }

    function normalizeLoadedMaskCanvas(canvas, layer) {
      const ctx = canvas.getContext('2d');
      const imageData = ctx.getImageData(0, 0, canvas.width, canvas.height);
      const data = imageData.data;
      const color = brushColorFor(layer);
      const hex = color.replace('#', '');
      const rgb = [
        parseInt(hex.slice(0, 2), 16),
        parseInt(hex.slice(2, 4), 16),
        parseInt(hex.slice(4, 6), 16),
      ];
      for (let i = 0; i < data.length; i += 4) {
        const intensity = (data[i] + data[i + 1] + data[i + 2]) / 3;
        const painted = data[i + 3] > 0 && intensity >= 127;
        if (painted) {
          data[i] = rgb[0];
          data[i + 1] = rgb[1];
          data[i + 2] = rgb[2];
          data[i + 3] = 255;
        } else {
          data[i] = 0;
          data[i + 1] = 0;
          data[i + 2] = 0;
          data[i + 3] = 0;
        }
      }
      ctx.putImageData(imageData, 0, 0);
    }

    function loadMaskIntoCanvas(canvas, url, layer) {
      const ctx = canvas.getContext('2d');
      return new Promise((resolve) => {
        if (!url) {
          fillCanvas(canvas, 'black');
          resolve();
          return;
        }
        const img = new Image();
        img.onload = () => {
          ctx.clearRect(0, 0, canvas.width, canvas.height);
          ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
          normalizeLoadedMaskCanvas(canvas, layer);
          resolve();
        };
        img.onerror = () => {
          fillCanvas(canvas, 'black');
          resolve();
        };
        img.src = url + `?t=${Date.now()}`;
      });
    }

    async function saveCurrentPage() {
      if (!state.pageData) return;
      if (!state.pageMode) {
        els.saveLabel.textContent = 'choose a page mode before saving';
        return;
      }
      const payload = {
        pdf: state.pageData.pdf,
        page: state.pageData.page,
        mode: state.pageMode,
        layoutRole: state.layoutRole,
        textMask: offscreen.text.toDataURL('image/png'),
        imageMask: offscreen.image.toDataURL('image/png'),
        equationMask: offscreen.equation.toDataURL('image/png'),
      };
      els.saveLabel.textContent = 'saving...';
      try {
        await api('/api/save', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        renderPixels();
        state.pageData.saved = true;
        state.pageData.dirty = false;
        const pdf = state.pdfs[state.currentPdfIndex];
        if (pdf && pdf.pages[state.currentPageIndex]) {
          pdf.pages[state.currentPageIndex].saved = true;
          pdf.pages[state.currentPageIndex].dirty = false;
        }
        setDirty(false);
        els.saveLabel.textContent = 'saved';
      } catch (err) {
        els.saveLabel.textContent = `save failed: ${err.message}`;
      }
      rebuildPageList();
    }

    function scheduleSave() {
      clearTimeout(state.saveTimer);
      state.saveTimer = setTimeout(() => {
        saveCurrentPage();
      }, 850);
    }

    async function loadPage(pdfIndex, pageIndex, saveBeforeLoad = true) {
      clearTimeout(state.saveTimer);
      state.saveTimer = null;
      if (state.pageData && saveBeforeLoad) {
        await saveCurrentPage();
      }
      const pdf = state.pdfs[pdfIndex];
      const page = pdf.pages[pageIndex];
      const res = await api(`/api/page?pdf=${encodeURIComponent(pdf.name)}&page=${page.number}`);
      const data = await res.json();

      state.currentPdfIndex = pdfIndex;
      state.currentPageIndex = pageIndex;
      state.pageData = data;
      state.pageMode = data.mode || null;
      state.layoutRole = data.layoutRole && data.layoutRole !== 'body' ? data.layoutRole : null;
      state.activeLayer = data.mode === 'image' ? 'image' : 'text';
      state.drawing = false;
      state.hoverPoint = null;
      state.rectStart = null;
      state.rectEnd = null;
      state.selection = null;
      state.transform = null;
      if (state.pdfs[pdfIndex] && state.pdfs[pdfIndex].pages[pageIndex]) {
        state.pdfs[pdfIndex].pages[pageIndex].saved = Boolean(data.saved);
        state.pdfs[pdfIndex].pages[pageIndex].dirty = false;
      }
      state.pageImage = new Image();
      state.pageImage.onload = () => {
        state.pageImageSize = { width: state.pageImage.width, height: state.pageImage.height };
        state.displayScale = state.fitToPage
          ? fitScaleFor(state.pageImage.width, state.pageImage.height)
          : state.zoomScale;
        resetAnnotationCanvases(state.pageImage.width, state.pageImage.height);
        ensureCanvasSize(state.pageImage.width, state.pageImage.height);
        Promise.all([
          loadMaskIntoCanvas(offscreen.text, data.textMaskUrl, 'text'),
          loadMaskIntoCanvas(offscreen.image, data.imageMaskUrl, 'image'),
          loadMaskIntoCanvas(offscreen.equation, data.equationMaskUrl, 'equation'),
        ]).then(() => {
          clearUndo();
          redraw();
          renderPixels();
          setDirty(false);
          els.saveLabel.textContent = data.saved ? 'saved' : 'loaded';
          rebuildPageList();
          updateControls();
        });
      };
      state.pageImage.src = data.pageImageUrl + `?t=${Date.now()}`;
      setHash(pdfIndex, pageIndex + 1);
      els.pageCounter.textContent = `${pageIndex + 1}/${pdf.pages.length}`;
      rebuildPageList();
      updateControls();
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
      await loadPage(pdfIndex, pageIndex);
    }

    function setMode(mode) {
      state.pageMode = mode;
      if (mode === 'text') state.activeLayer = 'text';
      if (mode === 'image') state.activeLayer = 'image';
      if (mode === 'page-image') {
        state.activeLayer = 'text';
      }
      setDirty(true);
      updateControls();
      scheduleSave();
    }

    function setTool(tool) {
      state.tool = tool;
      state.transform = null;
      updateControls();
      redraw();
    }

    function setLayer(layer) {
      state.activeLayer = layer;
      updateControls();
      redraw();
    }

    function setupEvents() {
      els.pdfSelect.addEventListener('change', async () => {
        const pdfIndex = Number(els.pdfSelect.value);
        await loadPage(pdfIndex, 0);
      });
      els.prevBtn.addEventListener('click', () => goToPage(state.currentPdfIndex, state.currentPageIndex - 1));
      els.nextBtn.addEventListener('click', () => goToPage(state.currentPdfIndex, state.currentPageIndex + 1));
      els.modeText.addEventListener('click', () => setMode('text'));
      els.modeImage.addEventListener('click', () => setMode('image'));
      els.modePageImage.addEventListener('click', () => setMode('page-image'));
      els.layoutRoleSelect.addEventListener('change', () => {
        state.layoutRole = els.layoutRoleSelect.value || null;
        setDirty(true);
        updateControls();
        scheduleSave();
      });
      els.toolSelect.addEventListener('click', () => setTool('select'));
      els.toolBrush.addEventListener('click', () => setTool('brush'));
      els.toolRect.addEventListener('click', () => setTool('rect'));
      els.layerText.addEventListener('click', () => setLayer('text'));
      els.layerImage.addEventListener('click', () => setLayer('image'));
      els.layerEquation.addEventListener('click', () => setLayer('equation'));
      els.brushSize.addEventListener('input', () => {
        state.brushSize = Number(els.brushSize.value);
        updateControls();
      });
      els.eraseBtn.addEventListener('click', () => {
        state.eraseMode = !state.eraseMode;
        updateControls();
        redraw();
      });
      els.undoBtn.addEventListener('click', () => restoreUndo());
      els.clearBtn.addEventListener('click', () => {
        pushUndo(state.activeLayer);
        clearSelection();
        fillCanvas(offscreen[state.activeLayer], 'black');
        redraw();
        setDirty(true);
        scheduleSave();
      });
      els.selectionToText.addEventListener('click', () => commitSelection('text'));
      els.selectionToImage.addEventListener('click', () => commitSelection('image'));
      els.selectionToEquation.addEventListener('click', () => commitSelection('equation'));
      els.clearSelectionBtn.addEventListener('click', () => {
        clearSelection();
        redraw();
      });
      els.toggleOverlayBtn.addEventListener('click', () => {
        state.overlayVisible = !state.overlayVisible;
        updateControls();
        redraw();
      });
      els.refreshBtn.addEventListener('click', async () => {
        if (!state.pageData) return;
        if (state.dirty && !window.confirm('Discard unsaved changes and reload the saved masks for this page?')) {
          return;
        }
        clearSelection();
        els.saveLabel.textContent = 'reloading...';
        await loadPage(state.currentPdfIndex, state.currentPageIndex, false);
      });
      els.fitBtn.addEventListener('click', () => {
        setFitPage();
      });
      els.zoomOutBtn.addEventListener('click', () => zoomBy(0.9));
      els.zoomInBtn.addEventListener('click', () => zoomBy(1.1));
      els.overlayCanvas.addEventListener('pointerdown', pointerDown);
      els.overlayCanvas.addEventListener('pointermove', pointerMove);
      els.overlayCanvas.addEventListener('pointerup', pointerUp);
      els.overlayCanvas.addEventListener('pointercancel', pointerUp);
      els.overlayCanvas.addEventListener('pointerleave', () => {
        if (!state.drawing) {
          clearPreview();
          redraw();
        }
      });
      els.overlayCanvas.addEventListener('wheel', (evt) => {
        if (!evt.ctrlKey && !evt.metaKey) return;
        evt.preventDefault();
        zoomBy(evt.deltaY < 0 ? 1.1 : 0.9, evt);
      }, { passive: false });
      window.addEventListener('keydown', async (evt) => {
        if (evt.target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(evt.target.tagName)) return;
        if (evt.key === 'ArrowLeft') {
          evt.preventDefault();
          await goToPage(state.currentPdfIndex, state.currentPageIndex - 1);
        } else if (evt.key === 'ArrowRight') {
          evt.preventDefault();
          await goToPage(state.currentPdfIndex, state.currentPageIndex + 1);
        } else if (evt.key === 't') {
          setLayer('text');
        } else if (evt.key === 'i') {
          setLayer('image');
        } else if (evt.key === 'q') {
          setLayer('equation');
        } else if (evt.key === 's') {
          setTool('select');
        } else if (evt.key === 'b') {
          setTool('brush');
        } else if (evt.key === 'r') {
          setTool('rect');
        } else if (evt.key === 'e') {
          state.eraseMode = !state.eraseMode;
          updateControls();
          redraw();
        } else if (evt.key === '1') {
          setMode('text');
        } else if (evt.key === '2') {
          setMode('image');
        } else if (evt.key === '3') {
          setMode('page-image');
        } else if (evt.key === 'u') {
          restoreUndo();
        } else if (evt.key === 'c') {
          pushUndo(state.activeLayer);
          clearSelection();
          fillCanvas(offscreen[state.activeLayer], 'black');
          redraw();
          setDirty(true);
          scheduleSave();
        } else if (evt.key === '[') {
          state.brushSize = Math.max(4, state.brushSize - 2);
          els.brushSize.value = String(state.brushSize);
          updateControls();
          redraw();
        } else if (evt.key === ']') {
          state.brushSize = Math.min(180, state.brushSize + 2);
          els.brushSize.value = String(state.brushSize);
          updateControls();
          redraw();
        } else if (evt.key === '-' || evt.key === '_') {
          evt.preventDefault();
          zoomBy(0.9);
        } else if (evt.key === '=' || evt.key === '+') {
          evt.preventDefault();
          zoomBy(1.1);
        } else if (evt.key === 'f') {
          setFitPage();
        }
      });
      window.addEventListener('beforeunload', () => {
        if (state.dirty) {
          navigator.sendBeacon('/api/save', new Blob([JSON.stringify({
            pdf: state.pageData?.pdf,
            page: state.pageData?.page,
            mode: state.pageMode,
            layoutRole: state.layoutRole,
            textMask: offscreen.text.toDataURL('image/png'),
            imageMask: offscreen.image.toDataURL('image/png'),
            equationMask: offscreen.equation.toDataURL('image/png'),
          })], { type: 'application/json' }));
        }
      });
      window.addEventListener('resize', () => {
        if (state.pageImageSize.width && state.fitToPage) {
          state.displayScale = fitScaleFor(state.pageImageSize.width, state.pageImageSize.height);
          ensureCanvasSize(state.pageImageSize.width, state.pageImageSize.height);
          redraw();
        }
      });
    }

    async function boot() {
      const res = await api('/api/index');
      const data = await res.json();
      state.pdfs = data.pdfs || [];
      els.rootInfo.textContent = `${data.annotationRoot} | ${data.pageCount} pages`;
      if (!state.pdfs.length) {
        throw new Error(`No PDFs discovered for ${data.annotationRoot}. Check --pages-root or --pdf-dir.`);
      }
      els.pdfSelect.innerHTML = '';
      state.pdfs.forEach((pdf, i) => {
        const option = document.createElement('option');
        option.value = String(i);
        option.textContent = `${pdf.name} (${pdf.pages.length})`;
        els.pdfSelect.appendChild(option);
      });
      setupEvents();
      const { pdf, page } = pageStateFromUrl();
      const pdfIndex = Math.max(0, Math.min(state.pdfs.length - 1, pdf));
      const pageIndex = Math.max(0, Math.min(state.pdfs[pdfIndex].pages.length - 1, page));
      els.pdfSelect.value = String(pdfIndex);
      await loadPage(pdfIndex, pageIndex);
      updateControls();
    }

    boot().catch(err => {
      document.body.innerHTML = `<pre style="white-space:pre-wrap;padding:20px;color:#fca5a5">${err.stack || err}</pre>`;
    });
  </script>
</body>
</html>
"""

VIEWER_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PDF Reconstruction Viewer</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #0b0f13;
      --panel: #141922;
      --line: #28313c;
      --text: #e8edf2;
      --muted: #95a3b3;
      --accent: #7dd3fc;
      --good: #8ce99a;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(180deg, #090c10 0%, #10151b 100%);
      color: var(--text);
      font: 14px/1.4 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
      min-height: 100vh;
    }
    button, input {
      font: inherit;
    }
    .shell {
      display: grid;
      grid-template-rows: auto 1fr;
      min-height: 100vh;
    }
    .topbar {
      display: grid;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid var(--line);
      background: rgba(13, 17, 23, 0.96);
    }
    .row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      align-items: center;
    }
    .pill {
      padding: 4px 10px;
      border: 1px solid rgba(255,255,255,0.08);
      border-radius: 999px;
      background: rgba(255,255,255,0.04);
    }
    .btn {
      border: 1px solid var(--line);
      background: #171d25;
      color: var(--text);
      border-radius: 10px;
      padding: 8px 10px;
      cursor: pointer;
    }
    .btn:hover { border-color: #43607a; }
    .btn.active { outline: 2px solid var(--accent); outline-offset: 1px; }
    .content {
      padding: 16px;
      overflow: auto;
    }
    .grid {
      display: grid;
      grid-template-columns: minmax(420px, 1fr) minmax(420px, 1fr);
      gap: 16px;
      align-items: start;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: rgba(20, 25, 34, 0.92);
      overflow: hidden;
      box-shadow: 0 18px 50px rgba(0,0,0,0.35);
    }
    .card h2 {
      margin: 0;
      padding: 12px 14px;
      font-size: 15px;
      border-bottom: 1px solid var(--line);
      background: rgba(255,255,255,0.02);
    }
    .image-wrap {
      overflow: auto;
      max-height: calc(100vh - 190px);
      background: #0a0d11;
    }
    .image-wrap img {
      display: block;
      width: 100%;
      height: auto;
    }
    .equations {
      display: grid;
      gap: 10px;
      padding: 12px;
    }
    .equation {
      display: grid;
      gap: 6px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: rgba(255,255,255,0.03);
    }
    .equation img {
      width: 100%;
      height: auto;
      background: white;
      border-radius: 8px;
    }
    .meta {
      color: var(--muted);
      font-size: 12px;
      display: grid;
      gap: 2px;
    }
    .muted { color: var(--muted); }
    .status { color: var(--good); }
    .nav input {
      width: 80px;
      padding: 7px 9px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: #0f1318;
      color: var(--text);
    }
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div class="row">
        <span class="pill" id="title">Loading...</span>
        <span class="pill" id="state">...</span>
      </div>
      <div class="row nav">
        <button class="btn" id="prevBtn">Prev</button>
        <button class="btn" id="nextBtn">Next</button>
        <label class="muted">PDF <input id="pdfInput" /></label>
        <label class="muted">Page <input id="pageInput" type="number" min="1" step="1" /></label>
        <button class="btn" id="goBtn">Go</button>
        <button class="btn" id="toggleComparisonBtn">Comparison</button>
        <button class="btn" id="toggleRerenderBtn">Rerender</button>
      </div>
    </div>
    <div class="content">
      <div class="grid">
        <section class="card">
          <h2>Original page</h2>
          <div class="image-wrap"><img id="originalImg" alt="Original page" /></div>
        </section>
        <section class="card">
          <h2 id="rightTitle">Reconstruction</h2>
          <div class="image-wrap"><img id="rightImg" alt="Reconstruction view" /></div>
        </section>
      </div>
      <section class="card" style="margin-top:16px;">
        <h2>Equations</h2>
        <div class="equations" id="equations"></div>
      </section>
    </div>
  </div>
  <script>
    const state = {
      pdf: new URL(location.href).searchParams.get('pdf') || '',
      page: Number(new URL(location.href).searchParams.get('page') || '31'),
      mode: new URL(location.href).searchParams.get('mode') || 'comparison',
      data: null,
    };

    const els = {
      title: document.getElementById('title'),
      state: document.getElementById('state'),
      prevBtn: document.getElementById('prevBtn'),
      nextBtn: document.getElementById('nextBtn'),
      pdfInput: document.getElementById('pdfInput'),
      pageInput: document.getElementById('pageInput'),
      goBtn: document.getElementById('goBtn'),
      toggleComparisonBtn: document.getElementById('toggleComparisonBtn'),
      toggleRerenderBtn: document.getElementById('toggleRerenderBtn'),
      rightTitle: document.getElementById('rightTitle'),
      originalImg: document.getElementById('originalImg'),
      rightImg: document.getElementById('rightImg'),
      equations: document.getElementById('equations'),
    };

    function api(path) {
      return fetch(path).then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      });
    }

    function setUrl() {
      const url = new URL(location.href);
      url.searchParams.set('view', 'rerender');
      url.searchParams.set('pdf', state.pdf);
      url.searchParams.set('page', String(state.page));
      url.searchParams.set('mode', state.mode);
      history.replaceState(null, '', url);
    }

    function setMode(mode) {
      state.mode = mode;
      setUrl();
      render();
    }

    function updateButtons() {
      els.toggleComparisonBtn.classList.toggle('active', state.mode === 'comparison');
      els.toggleRerenderBtn.classList.toggle('active', state.mode === 'rerender');
      els.rightTitle.textContent = state.mode === 'comparison' ? 'Comparison' : 'Rerender only';
    }

    function buildEquationHtml(equations) {
      if (!Array.isArray(equations) || !equations.length) {
        return '<div class="muted">No equation crops found.</div>';
      }
      return equations.map((eq) => `
        <div class="equation">
          <img src="${eq.image}" alt="Equation ${eq.index}" />
          <div class="meta">
            <div>equation ${eq.index}</div>
            <div>${eq.w} x ${eq.h} at (${eq.x}, ${eq.y})</div>
          </div>
        </div>
      `).join('');
    }

    async function render() {
      els.pdfInput.value = state.pdf;
      els.pageInput.value = String(state.page);
      setUrl();
      updateButtons();
      els.state.textContent = 'loading...';
      const data = await api(`/api/rerender-page?pdf=${encodeURIComponent(state.pdf)}&page=${encodeURIComponent(state.page)}`);
      state.data = data;
      els.title.textContent = `${data.pdf} / page ${data.page}`;
      els.originalImg.src = data.originalPageUrl + `?t=${Date.now()}`;
      els.rightImg.src = (state.mode === 'comparison' ? data.comparisonUrl : data.rerenderUrl) + `?t=${Date.now()}`;
      els.rightTitle.textContent = state.mode === 'comparison' ? 'Comparison' : 'Rerender only';
      const equations = Array.isArray(data.equationCrops) ? data.equationCrops : [];
      els.state.textContent = `${equations.length} equations, ${data.acceptedLines ?? 0} text lines`;
      els.equations.innerHTML = buildEquationHtml(equations);
    }

    els.prevBtn.onclick = () => { if (state.page > 1) { state.page -= 1; render(); } };
    els.nextBtn.onclick = () => { state.page += 1; render(); };
    els.goBtn.onclick = () => {
      state.pdf = els.pdfInput.value.trim() || state.pdf;
      state.page = Number(els.pageInput.value || state.page);
      render();
    };
    els.toggleComparisonBtn.onclick = () => setMode('comparison');
    els.toggleRerenderBtn.onclick = () => setMode('rerender');

    render().catch((err) => {
      els.state.textContent = `error: ${err.message}`;
    });
  </script>
</body>
</html>
"""


def run(cmd: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def page_sort_key(path: Path) -> Tuple[int, str]:
    suffix = path.stem.rsplit("-", 1)[-1]
    try:
        return int(suffix), path.stem
    except ValueError:
        return 10**9, path.stem


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SystemExit(f"Required tool not found in PATH: {name}")
    return path


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


@dataclass
class PDFDoc:
    name: str
    pdf_path: Path
    page_count: int
    page_images: List[Path]
    pages_dir: Path
    masks_text_dir: Path
    masks_image_dir: Path
    masks_equation_dir: Path
    state_dir: Path


class AnnotationStore:
    def __init__(
        self,
        pdf_dir: Path | None,
        annotation_root: Path,
        dpi: int,
        pages_root: Path | None = None,
        rerender_root: Path | None = None,
    ) -> None:
        self.pdf_dir = pdf_dir
        self.annotation_root = annotation_root
        self.rerender_root = rerender_root
        self.dpi = dpi
        self.pages_root = pages_root
        self.lock = threading.Lock()
        self.docs: List[PDFDoc] = []
        self._ensure_docs()

    def _ensure_docs(self) -> None:
        self.annotation_root.mkdir(parents=True, exist_ok=True)
        if self.pages_root is not None:
            doc_dirs = [p for p in sorted(self.pages_root.iterdir()) if p.is_dir()]
        else:
            doc_dirs = [p for p in sorted(self.pdf_dir.glob("*.pdf"))]
        for doc_path in doc_dirs:
            if doc_path.suffix.lower() == ".pdf":
                doc_name = doc_path.stem
                page_count = pdf_page_count(doc_path)
                source_pages = None
                page_images = []
            else:
                doc_name = doc_path.name
                source_pages_dir = doc_path / "pages"
                if not source_pages_dir.exists():
                    continue
                page_images = sorted(source_pages_dir.glob("*.png"), key=page_sort_key)
                page_count = len(page_images)
                source_pages = page_images
            doc_root = self.annotation_root / doc_name
            pages_dir = doc_root / "pages"
            masks_text_dir = doc_root / "masks" / "text"
            masks_image_dir = doc_root / "masks" / "image"
            masks_equation_dir = doc_root / "masks" / "equation"
            state_dir = doc_root / "state"
            pages_dir.mkdir(parents=True, exist_ok=True)
            masks_text_dir.mkdir(parents=True, exist_ok=True)
            masks_image_dir.mkdir(parents=True, exist_ok=True)
            masks_equation_dir.mkdir(parents=True, exist_ok=True)
            state_dir.mkdir(parents=True, exist_ok=True)
            if doc_path.suffix.lower() == ".pdf":
                page_images = sorted(pages_dir.glob(f"{doc_name}-*.png"), key=page_sort_key)
                if len(page_images) != page_count:
                    render_pdf(doc_path, pages_dir, self.dpi)
                    page_images = sorted(pages_dir.glob(f"{doc_name}-*.png"), key=page_sort_key)
            else:
                # Link page images directly into the annotation root cache.
                for src in source_pages or []:
                    dst = pages_dir / src.name
                    if not dst.exists() or dst.stat().st_size != src.stat().st_size:
                        shutil.copy2(src, dst)
                page_images = sorted(pages_dir.glob("*.png"), key=page_sort_key)
                page_count = len(page_images)
            for idx in range(1, page_count + 1):
                state_path = state_dir / f"page-{idx:03d}.json"
                if not state_path.exists():
                    state = {
                        "pdf": doc_name,
                        "page": idx,
                        "mode": None,
                        "layout_role": None,
                        "updated_at": None,
                    }
                    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                for layer_dir in [masks_text_dir, masks_image_dir, masks_equation_dir]:
                    mask_path = layer_dir / f"page-{idx:03d}.png"
                    if not mask_path.exists():
                        if idx - 1 >= len(page_images):
                            raise RuntimeError(f"Missing rendered page {idx} for {doc_name}")
                        src_img = cv2.imread(str(page_images[idx - 1]), cv2.IMREAD_COLOR)
                        if src_img is None:
                            raise RuntimeError(f"Could not read rendered page: {page_images[idx - 1]}")
                        blank = np.zeros(src_img.shape[:2], dtype=np.uint8)
                        cv2.imwrite(str(mask_path), blank)
            self.docs.append(
                PDFDoc(
                    name=doc_name,
                    pdf_path=doc_path if doc_path.suffix.lower() == ".pdf" else Path(doc_name),
                    page_count=page_count,
                    page_images=page_images,
                    pages_dir=pages_dir,
                    masks_text_dir=masks_text_dir,
                    masks_image_dir=masks_image_dir,
                    masks_equation_dir=masks_equation_dir,
                    state_dir=state_dir,
                )
            )

    def index(self) -> Dict[str, Any]:
        return {
            "annotationRoot": str(self.annotation_root),
            "pageCount": sum(doc.page_count for doc in self.docs),
            "pdfs": [
                {
                    "name": doc.name,
                    "pageCount": doc.page_count,
                    "pages": [
                        {
                            "number": idx,
                            "mode": self.get_state(doc.name, idx)["mode"],
                            "layoutRole": self.get_state(doc.name, idx).get("layout_role"),
                            "saved": self.is_saved(doc.name, idx),
                            "dirty": False,
                        }
                        for idx in range(1, doc.page_count + 1)
                    ],
                }
                for doc in self.docs
            ],
        }

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
            "text_mask": doc.masks_text_dir / f"page-{page:03d}.png",
            "image_mask": doc.masks_image_dir / f"page-{page:03d}.png",
            "equation_mask": doc.masks_equation_dir / f"page-{page:03d}.png",
            "state": doc.state_dir / f"page-{page:03d}.json",
        }

    def rerender_paths(self, pdf_name: str, page: int) -> Dict[str, Path]:
        if self.rerender_root is None:
            raise KeyError("rerender root not configured")
        base = self.rerender_root / pdf_name
        return {
            "rerender": base / f"page-{page:03d}-rerender.png",
            "comparison": base / f"page-{page:03d}-comparison.png",
            "meta": base / f"page-{page:03d}-rerender.json",
            "equation_dir": base / "equation_crops" / f"page-{page:03d}",
        }

    def get_state(self, pdf_name: str, page: int) -> Dict[str, Any]:
        state_path = self.page_paths(pdf_name, page)["state"]
        if state_path.exists():
            return json.loads(state_path.read_text(encoding="utf-8"))
        return {"pdf": pdf_name, "page": page, "mode": None, "layout_role": None}

    def is_saved(self, pdf_name: str, page: int) -> bool:
        state = self.get_state(pdf_name, page)
        return bool(state.get("updated_at"))

    def save_page(self, payload: Dict[str, Any]) -> None:
        pdf_name = payload["pdf"]
        page = int(payload["page"])
        mode = payload.get("mode")
        existing = self.get_state(pdf_name, page)
        layout_role = payload.get("layoutRole", existing.get("layout_role"))
        if layout_role not in (None, "", "toc", "cover", "body"):
            raise ValueError("Layout role must be one of: body, toc, cover")
        normalized_layout_role = None if layout_role in (None, "", "body") else str(layout_role)
        if mode not in ("text", "image", "page-image"):
            raise ValueError("Page mode must be one of: text, image, page-image")
        paths = self.page_paths(pdf_name, page)
        text_mask = decode_png_data_url(payload["textMask"])
        image_mask = decode_png_data_url(payload["imageMask"])
        equation_mask = decode_png_data_url(payload["equationMask"])
        with self.lock:
            cv2.imwrite(str(paths["text_mask"]), text_mask)
            cv2.imwrite(str(paths["image_mask"]), image_mask)
            cv2.imwrite(str(paths["equation_mask"]), equation_mask)
            state = {
                **existing,
                "pdf": pdf_name,
                "page": page,
                "mode": mode,
                "layout_role": normalized_layout_role,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            paths["state"].write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def decode_png_data_url(data_url: str) -> np.ndarray:
    if "," not in data_url:
        raise ValueError("Invalid data URL")
    payload = data_url.split(",", 1)[1]
    raw = base64.b64decode(payload)
    arr = np.frombuffer(raw, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError("Could not decode PNG")
    return img


def guess_mime(path: Path) -> str:
    if path.suffix == ".png":
        return "image/png"
    if path.suffix == ".json":
        return "application/json"
    if path.suffix == ".html":
        return "text/html; charset=utf-8"
    return "application/octet-stream"


class Handler(BaseHTTPRequestHandler):
    store: AnnotationStore

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
            query = parse_qs(parsed.query)
            if query.get("view", ["editor"])[0] == "rerender":
                self._send_bytes(VIEWER_HTML.encode("utf-8"), "text/html; charset=utf-8")
            else:
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
                "mode": state.get("mode"),
                "layoutRole": state.get("layout_role") or state.get("layoutRole"),
                "pageImageUrl": f"/files/{pdf}/pages/{paths['page_image'].name}",
                "textMaskUrl": f"/files/{pdf}/masks/text/{paths['text_mask'].name}",
                "imageMaskUrl": f"/files/{pdf}/masks/image/{paths['image_mask'].name}",
                "equationMaskUrl": f"/files/{pdf}/masks/equation/{paths['equation_mask'].name}",
                "saved": bool(state.get("updated_at")),
                "pageCount": doc.page_count,
            }
            self._send_json(body)
            return
        if parsed.path == "/api/rerender-page":
            query = parse_qs(parsed.query)
            pdf = query.get("pdf", [None])[0]
            page = int(query.get("page", ["1"])[0])
            if not pdf:
                self._send_json({"error": "missing pdf"}, status=400)
                return
            if self.store.rerender_root is None:
                self._send_json({"error": "rerender root not configured"}, status=400)
                return
            paths = self.store.rerender_paths(pdf, page)
            meta: Dict[str, Any] = {}
            if paths["meta"].exists():
                meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
            original = self.store.page_paths(pdf, page)
            body = {
                "pdf": pdf,
                "page": page,
                "originalPageUrl": f"/files/{pdf}/pages/{original['page_image'].name}",
                "rerenderUrl": f"/rerender-files/{pdf}/{paths['rerender'].name}",
                "comparisonUrl": f"/rerender-files/{pdf}/{paths['comparison'].name}",
                "equationCrops": meta.get("equation_crops", []),
                "acceptedLines": meta.get("accepted_lines", 0),
                "rejectedLines": meta.get("rejected_lines", 0),
                "source": meta.get("source"),
            }
            self._send_json(body)
            return
        if parsed.path.startswith("/files/"):
            rel = parsed.path.removeprefix("/files/")
            full = self.store.annotation_root / rel
            if not full.exists() or not full.is_file():
                self.send_error(404)
                return
            self._send_bytes(full.read_bytes(), guess_mime(full))
            return
        if parsed.path.startswith("/rerender-files/"):
            if self.store.rerender_root is None:
                self.send_error(404)
                return
            rel = parsed.path.removeprefix("/rerender-files/")
            full = self.store.rerender_root / rel
            if not full.exists() or not full.is_file():
                self.send_error(404)
                return
            self._send_bytes(full.read_bytes(), guess_mime(full))
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
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--pdf-dir", type=Path, default=repo_root / "pdfs")
    parser.add_argument(
        "--pages-root",
        type=Path,
        default=repo_root / "pages",
        help="Root containing pre-rendered page image folders",
    )
    parser.add_argument(
        "--annotation-root",
        type=Path,
        default=repo_root / "annotations",
    )
    parser.add_argument("--rerender-root", type=Path, default=repo_root / "renders")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    require_tool("pdftoppm")
    require_tool("pdfinfo")

    store = AnnotationStore(
        args.pdf_dir if args.pages_root is None else None,
        args.annotation_root,
        args.dpi,
        args.pages_root,
        args.rerender_root,
    )
    Handler.store = store
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Serving on http://{args.host}:{args.port}")
    print(f"Annotation root: {args.annotation_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

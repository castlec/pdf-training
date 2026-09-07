#!/usr/bin/env python3
"""Render pdf-training page metadata as fixed-layout HTML.

The renderer consumes ``pdf-training-renderable-page-v1`` pages produced by
``render_compose.py``. It intentionally contains no source-book assumptions:
private projects provide page images, crop assets, content text, and style
metadata outside this repository.
"""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path
from typing import Any


GREEK: dict[str, str] = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "vartheta": "ϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Delta": "Δ",
    "Sigma": "Σ",
    "Omega": "Ω",
}

OPERATORS: dict[str, str] = {
    "cdot": "·",
    "times": "×",
    "pm": "±",
    "mp": "∓",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "neq": "≠",
    "approx": "≈",
    "doteq": "≐",
    "to": "→",
    "rightarrow": "→",
    "Rightarrow": "⇒",
    "leftarrow": "←",
    "angle": "∠",
    "measuredangle": "∡",
    "triangleleft": "◁",
    "sum": "∑",
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def box_style(box: dict[str, Any]) -> str:
    return (
        f'left:{int(box["x"])}px;top:{int(box["y"])}px;'
        f'width:{int(box["w"])}px;height:{int(box["h"])}px;'
    )


def strip_math_delimiters(value: str) -> str:
    text = value.strip()
    if text.startswith("$$") and text.endswith("$$"):
        return text[2:-2].strip()
    if text.startswith("$") and text.endswith("$"):
        return text[1:-1].strip()
    if text.startswith(r"\(") and text.endswith(r"\)"):
        return text[2:-2].strip()
    if text.startswith(r"\[") and text.endswith(r"\]"):
        return text[2:-2].strip()
    return text


class LatexTextParser:
    """Small parser for text-shaped math fragments.

    It handles commands, one-level groups, and sub/superscripts. Structural
    constructs such as fractions are expected to use the MathML renderer.
    """

    def __init__(self, text: str) -> None:
        self.text = strip_math_delimiters(text)
        self.i = 0

    def parse(self, stop: str | None = None) -> str:
        parts: list[str] = []
        while self.i < len(self.text):
            ch = self.text[self.i]
            if stop and ch == stop:
                self.i += 1
                break
            if ch == "\\":
                parts.append(self.parse_command())
            elif ch in "_^":
                tag = "sub" if ch == "_" else "sup"
                self.i += 1
                parts.append(f"<{tag}>{self.parse_script_arg()}</{tag}>")
            elif ch == "{":
                self.i += 1
                parts.append(self.parse("}"))
            elif ch == "}":
                self.i += 1
                if stop:
                    break
            else:
                parts.append(html.escape(ch))
                self.i += 1
        return "".join(parts)

    def parse_command(self) -> str:
        self.i += 1
        start = self.i
        while self.i < len(self.text) and self.text[self.i].isalpha():
            self.i += 1
        name = self.text[start : self.i]
        if not name and self.i < len(self.text):
            ch = self.text[self.i]
            self.i += 1
            return html.escape(ch)
        if name == "vec":
            return f'<span class="math-vec">{self.parse_script_arg()}</span>'
        if name in GREEK:
            return html.escape(GREEK[name])
        if name in OPERATORS:
            return html.escape(OPERATORS[name])
        if name in {"left", "right"}:
            return ""
        return html.escape("\\" + name)

    def parse_script_arg(self) -> str:
        if self.i >= len(self.text):
            return ""
        if self.text[self.i] == "{":
            self.i += 1
            return self.parse("}")
        if self.text[self.i] == "\\":
            return self.parse_command()
        ch = self.text[self.i]
        self.i += 1
        return html.escape(ch)


def latex_text_fragment(value: str) -> str:
    return LatexTextParser(value).parse()


class LatexMathMLParser:
    """Conservative LaTeX-to-MathML converter for common technical notation."""

    def __init__(self, text: str) -> None:
        self.text = strip_math_delimiters(text)
        self.i = 0

    def parse_math(self) -> str:
        return '<math xmlns="http://www.w3.org/1998/Math/MathML">' + self.parse_row() + "</math>"

    def parse_row(self, stop: str | None = None) -> str:
        parts: list[str] = []
        while self.i < len(self.text):
            ch = self.text[self.i]
            if stop and ch == stop:
                self.i += 1
                break
            if ch.isspace():
                self.i += 1
                continue
            atom = self.parse_atom(stop=stop)
            if atom:
                parts.append(self.apply_scripts(atom))
        if len(parts) == 1:
            return parts[0]
        return "<mrow>" + "".join(parts) + "</mrow>"

    def parse_atom(self, *, stop: str | None = None) -> str:
        if self.i >= len(self.text):
            return ""
        ch = self.text[self.i]
        if stop and ch == stop:
            return ""
        if ch == "{":
            self.i += 1
            return self.parse_row(stop="}")
        if ch == "}":
            self.i += 1
            return ""
        if ch == "\\":
            return self.parse_command()
        self.i += 1
        if ch.isalpha():
            return f"<mi>{html.escape(ch)}</mi>"
        if ch.isdigit() or ch in ",.":
            return f"<mn>{html.escape(ch)}</mn>"
        return f"<mo>{html.escape(ch)}</mo>"

    def parse_command(self) -> str:
        self.i += 1
        start = self.i
        while self.i < len(self.text) and self.text[self.i].isalpha():
            self.i += 1
        name = self.text[start : self.i]
        if name == "frac":
            numerator = self.parse_group_or_atom()
            denominator = self.parse_group_or_atom()
            return f"<mfrac>{numerator}{denominator}</mfrac>"
        if name == "sqrt":
            return f"<msqrt>{self.parse_group_or_atom()}</msqrt>"
        if name == "vec":
            return f'<mover accent="true">{self.parse_group_or_atom()}<mo>→</mo></mover>'
        if name in {"left", "right"}:
            return ""
        if name in GREEK:
            return f"<mi>{html.escape(GREEK[name])}</mi>"
        if name in OPERATORS:
            return f"<mo>{html.escape(OPERATORS[name])}</mo>"
        if not name and self.i < len(self.text):
            ch = self.text[self.i]
            self.i += 1
            return f"<mo>{html.escape(ch)}</mo>"
        return f"<mi>{html.escape(name or '?')}</mi>"

    def parse_group_or_atom(self) -> str:
        while self.i < len(self.text) and self.text[self.i].isspace():
            self.i += 1
        if self.i < len(self.text) and self.text[self.i] == "{":
            self.i += 1
            return self.parse_row(stop="}")
        atom = self.parse_atom()
        return self.apply_scripts(atom)

    def apply_scripts(self, atom: str) -> str:
        sub: str | None = None
        sup: str | None = None
        while self.i < len(self.text) and self.text[self.i] in "_^":
            marker = self.text[self.i]
            self.i += 1
            value = self.parse_group_or_atom()
            if marker == "_":
                sub = value
            else:
                sup = value
        if sub is not None and sup is not None:
            return f"<msubsup>{atom}{sub}{sup}</msubsup>"
        if sub is not None:
            return f"<msub>{atom}{sub}</msub>"
        if sup is not None:
            return f"<msup>{atom}{sup}</msup>"
        return atom


def latex_to_mathml(value: str) -> str:
    try:
        return LatexMathMLParser(value).parse_math()
    except Exception:
        escaped = html.escape(strip_math_delimiters(value))
        return f'<math xmlns="http://www.w3.org/1998/Math/MathML"><mtext>{escaped}</mtext></math>'


def asset_path(value: str | None, output_path: Path) -> str | None:
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.relative_to(output_path.parent.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def node_text(node: dict[str, Any]) -> str:
    for key in ("text", "label", "content"):
        value = node.get(key)
        if value:
            return str(value)
    return ""


def render_text_node(node: dict[str, Any]) -> str:
    text = html.escape(node_text(node))
    box = node["bbox"]
    return (
        f'<div class="node text-node text-{html.escape(str(node.get("class", "body")))}" '
        f'data-node-id="{html.escape(str(node["id"]))}" '
        f'style="{box_style(box)}">{text}</div>'
    )


def render_equation_node(node: dict[str, Any]) -> str:
    latex = str(node.get("latex") or "")
    policy = str(node.get("render_policy") or "inline_math")
    box = node["bbox"]
    if policy == "text":
        rendered = latex_text_fragment(latex)
        cls = "equation equation-text"
    else:
        rendered = latex_to_mathml(latex)
        cls = "equation equation-math"
        if policy == "display_math":
            cls += " equation-display"
    return (
        f'<div class="node {cls}" data-node-id="{html.escape(str(node["id"]))}" '
        f'data-latex="{html.escape(latex)}" style="{box_style(box)}">{rendered}</div>'
    )


def render_image_node(node: dict[str, Any], output_path: Path) -> str:
    box = node["bbox"]
    raw_asset = node.get("asset") or node.get("image_asset") or node.get("crop_asset")
    asset = asset_path(str(raw_asset), output_path) if raw_asset else None
    cls = "node image-node"
    if node.get("transparent_background", True):
        cls += " transparent"
    content = (
        f'<img src="{html.escape(asset)}" alt="{html.escape(str(node["id"]))}">'
        if asset
        else '<span class="missing-asset">image</span>'
    )
    return (
        f'<div class="{cls}" data-node-id="{html.escape(str(node["id"]))}" '
        f'style="{box_style(box)}">{content}</div>'
    )


def render_rule_node(node: dict[str, Any]) -> str:
    box = node["bbox"]
    return (
        f'<div class="node rule-node" data-node-id="{html.escape(str(node["id"]))}" '
        f'style="{box_style(box)}"></div>'
    )


def render_node(node: dict[str, Any], output_path: Path) -> str:
    node_type = str(node.get("type") or "")
    if node_type in {"text_fragment", "caption", "heading", "header", "footer", "page_number"}:
        return render_text_node(node)
    if node_type == "equation":
        return render_equation_node(node)
    if node_type in {"image", "diagram_component", "table"}:
        return render_image_node(node, output_path)
    if node_type == "rule":
        return render_rule_node(node)
    return ""


def style_block(page: dict[str, Any]) -> str:
    style = page.get("style") if isinstance(page.get("style"), dict) else {}
    fonts = style.get("fonts") if isinstance(style.get("fonts"), dict) else {}
    body_font = fonts.get("body", "Georgia, 'Times New Roman', serif")
    mono_font = fonts.get("mono", "'Iosevka', 'Cascadia Code', monospace")
    return f"""
<style>
:root {{
  --page-w: {int(page.get("width") or 1)}px;
  --page-h: {int(page.get("height") or 1)}px;
  --body-font: {body_font};
  --mono-font: {mono_font};
  --ink: #1f1a14;
  --paper: #fffdf8;
  --review-bg: #f2eadc;
  --rule: #30281d;
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--review-bg); color: var(--ink); font-family: var(--body-font); }}
.toolbar {{ padding: 12px 18px; background: #261b10; color: #fff5df; }}
.toolbar code {{ font-family: var(--mono-font); }}
.page {{ position: relative; width: var(--page-w); height: var(--page-h); background: var(--paper); overflow: hidden; }}
.page-wrap {{ padding: 18px; }}
.node {{ position: absolute; overflow: visible; }}
.text-node {{ white-space: pre-wrap; line-height: 1.18; font-size: 30px; }}
.text-body {{ font-size: 30px; }}
.text-caption {{ font-size: 24px; text-align: center; }}
.text-heading, .text-title {{ font-weight: 700; font-size: 42px; }}
.text-header, .text-footer, .text-page_number {{ font-size: 20px; }}
.equation {{ display: flex; align-items: center; line-height: 1; font-size: 30px; font-family: var(--body-font); }}
.equation-text sub {{ font-size: 0.62em; vertical-align: -0.28em; }}
.equation-text sup {{ font-size: 0.62em; vertical-align: 0.48em; }}
.math-vec {{ text-decoration: overline; text-decoration-thickness: 0.06em; }}
math {{ font-family: var(--body-font); font-style: normal; font-size: 30px; }}
mi {{ font-style: normal; }}
.equation-display math {{ font-size: 34px; }}
.image-node {{ z-index: 0; display: flex; align-items: stretch; justify-content: stretch; }}
.image-node img {{ width: 100%; height: 100%; object-fit: contain; mix-blend-mode: multiply; }}
.missing-asset {{ width: 100%; height: 100%; display: grid; place-items: center; border: 1px dashed #b58d43; color: #8a6420; font: 16px/1.2 var(--mono-font); }}
.rule-node {{ background: var(--rule); min-height: 1px; }}
.render-layer {{ position: absolute; inset: 0; }}
.render-layer.background {{ z-index: 1; }}
.render-layer.content {{ z-index: 2; }}
.review-box {{ position: absolute; border: 1px solid #ef4444; pointer-events: none; }}
.comparison {{ display: grid; grid-template-columns: max-content max-content; gap: 18px; align-items: start; padding: 18px; }}
.comparison-panel {{ background: #fff8ea; border: 1px solid #d5bc8d; padding: 10px; }}
.comparison-panel h2 {{ margin: 0 0 8px; font-size: 18px; }}
.source-page {{ width: var(--page-w); height: var(--page-h); object-fit: contain; background: white; }}
</style>
"""


def render_page_html(page: dict[str, Any], output_path: Path, *, show_boxes: bool = False) -> str:
    backgrounds: list[str] = []
    content: list[str] = []
    boxes: list[str] = []
    for node in page.get("nodes") or []:
        node_type = str(node.get("type") or "")
        rendered = render_node(node, output_path)
        if not rendered:
            continue
        if node_type in {"image", "diagram_component", "table"} or node.get("render_layer") == "background":
            backgrounds.append(rendered)
        else:
            content.append(rendered)
        if show_boxes and "bbox" in node:
            boxes.append(
                f'<div class="review-box" title="{html.escape(str(node.get("id")))}" '
                f'style="{box_style(node["bbox"])}"></div>'
            )
    return f"""<!doctype html>
<html lang="und">
<head>
<meta charset="utf-8">
<title>{html.escape(str(page.get("id") or "Renderable page"))}</title>
{style_block(page)}
</head>
<body>
<div class="toolbar">Renderable page <code>{html.escape(str(page.get("id") or ""))}</code></div>
<main class="page-wrap">
  <section class="page" data-page-id="{html.escape(str(page.get("id") or ""))}">
    <div class="render-layer background">{''.join(backgrounds)}</div>
    <div class="render-layer content">{''.join(content)}{''.join(boxes)}</div>
  </section>
</main>
</body>
</html>
"""


def render_comparison_html(
    page: dict[str, Any],
    output_path: Path,
    *,
    source_image: Path,
    show_boxes: bool = True,
) -> str:
    source = asset_path(str(source_image), output_path) or str(source_image)
    rendered_page = render_page_html(page, output_path, show_boxes=show_boxes)
    page_body = re.search(r'<section class="page"[^>]*>.*?</section>', rendered_page, flags=re.S)
    page_markup = page_body.group(0) if page_body else ""
    return f"""<!doctype html>
<html lang="und">
<head>
<meta charset="utf-8">
<title>Comparison: {html.escape(str(page.get("id") or "Renderable page"))}</title>
{style_block(page)}
</head>
<body>
<div class="toolbar">Comparison <code>{html.escape(str(page.get("id") or ""))}</code></div>
<main class="comparison">
  <section class="comparison-panel">
    <h2>Original</h2>
    <img class="source-page" src="{html.escape(source)}" alt="Original page">
  </section>
  <section class="comparison-panel">
    <h2>Rendered</h2>
    {page_markup}
  </section>
</main>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--page", required=True, type=Path, help="Renderable page JSON")
    parser.add_argument("--out", required=True, type=Path, help="Output render HTML")
    parser.add_argument("--comparison-html", type=Path, help="Optional side-by-side review HTML")
    parser.add_argument("--source-image", type=Path, help="Original page image for comparison")
    parser.add_argument("--show-boxes", action="store_true", help="Overlay node boxes in rendered output")
    args = parser.parse_args()

    page = load_json(args.page)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_page_html(page, args.out, show_boxes=args.show_boxes), encoding="utf-8")
    if args.comparison_html:
        if not args.source_image:
            parser.error("--comparison-html requires --source-image")
        args.comparison_html.parent.mkdir(parents=True, exist_ok=True)
        args.comparison_html.write_text(
            render_comparison_html(
                page,
                args.comparison_html,
                source_image=args.source_image,
                show_boxes=args.show_boxes,
            ),
            encoding="utf-8",
        )
    print(json.dumps({"page_id": page.get("id"), "nodes": len(page.get("nodes") or [])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

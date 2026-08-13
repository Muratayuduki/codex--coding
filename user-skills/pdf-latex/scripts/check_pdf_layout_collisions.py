#!/usr/bin/env python3
"""Detect visual collisions in PDF diagrams using Poppler outputs.

The checker intentionally avoids non-stdlib Python packages. It uses:
  - pdftocairo -svg  : vector paths for lines and rectangles
  - pdftotext -bbox : word bounding boxes

The strongest signal is line_vs_node: a line segment crossing the interior of a
small rectangle-like node. This catches the UML issue where an edge runs through
a class box while allowing edges that merely attach to the node boundary.
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
PATH_TOKEN_RE = re.compile(rf"[MmLlHhVvZz]|{NUMBER}")
ATTR_RE = re.compile(r'([A-Za-z_:][-A-Za-z0-9_:.]*)="([^"]*)"')
WORD_RE = re.compile(
    rf'<word\s+xMin="({NUMBER})"\s+yMin="({NUMBER})"\s+'
    rf'xMax="({NUMBER})"\s+yMax="({NUMBER})">(.*?)</word>',
    re.DOTALL,
)
PAGE_RE = re.compile(rf'<page\s+width="({NUMBER})"\s+height="({NUMBER})"')


@dataclass(frozen=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    page: int
    x1: float
    y1: float
    x2: float
    y2: float
    stroke_width: float
    dashed: bool
    source: str

    @property
    def length(self) -> float:
        return math.hypot(self.x2 - self.x1, self.y2 - self.y1)

    @property
    def bbox(self) -> "Rect":
        return Rect(
            min(self.x1, self.x2),
            min(self.y1, self.y2),
            max(self.x1, self.x2),
            max(self.y1, self.y2),
            label="segment",
        )


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float
    label: str = ""

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def area(self) -> float:
        return self.width * self.height

    def inset(self, amount: float) -> "Rect | None":
        rect = Rect(self.x0 + amount, self.y0 + amount, self.x1 - amount, self.y1 - amount, self.label)
        if rect.width <= 0 or rect.height <= 0:
            return None
        return rect

    def inflate(self, amount: float) -> "Rect":
        return Rect(self.x0 - amount, self.y0 - amount, self.x1 + amount, self.y1 + amount, self.label)

    def contains_point(self, point: Point, margin: float = 0.0) -> bool:
        return (
            self.x0 - margin <= point.x <= self.x1 + margin
            and self.y0 - margin <= point.y <= self.y1 + margin
        )

    def intersects(self, other: "Rect") -> bool:
        return not (
            self.x1 <= other.x0
            or other.x1 <= self.x0
            or self.y1 <= other.y0
            or other.y1 <= self.y0
        )

    def intersection_area(self, other: "Rect") -> float:
        if not self.intersects(other):
            return 0.0
        return (min(self.x1, other.x1) - max(self.x0, other.x0)) * (
            min(self.y1, other.y1) - max(self.y0, other.y0)
        )

    def as_list(self) -> list[float]:
        return [round(self.x0, 3), round(self.y0, 3), round(self.x1, 3), round(self.y1, 3)]


@dataclass(frozen=True)
class Word:
    page: int
    text: str
    rect: Rect


@dataclass(frozen=True)
class PageVectors:
    page: int
    width: float
    height: float
    svg: str
    rects: list[Rect]
    segments: list[Segment]


def run_command(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def parse_pages(value: str, total_pages: int | None = None) -> list[int]:
    pages: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left)
            end = int(right)
            if end < start:
                raise SystemExit(f"Invalid page range: {part}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    if not pages:
        raise SystemExit("No pages selected.")
    if total_pages is not None:
        pages = {page for page in pages if 1 <= page <= total_pages}
    return sorted(pages)


def get_total_pages(pdf_path: Path) -> int:
    proc = run_command(["pdfinfo", str(pdf_path)])
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise SystemExit("Could not read page count from pdfinfo.")


def parse_attrs(tag: str) -> dict[str, str]:
    return {match.group(1): html.unescape(match.group(2)) for match in ATTR_RE.finditer(tag)}


def parse_transform(value: str | None) -> tuple[float, float, float, float, float, float]:
    if not value:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    match = re.search(r"matrix\(([^)]*)\)", value)
    if not match:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    parts = [float(part.strip()) for part in re.split(r"[,\s]+", match.group(1).strip()) if part.strip()]
    if len(parts) != 6:
        return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    return tuple(parts)  # type: ignore[return-value]


def apply_transform(point: Point, matrix: tuple[float, float, float, float, float, float]) -> Point:
    a, b, c, d, e, f = matrix
    return Point(a * point.x + c * point.y + e, b * point.x + d * point.y + f)


def parse_path_points(d_attr: str, matrix: tuple[float, float, float, float, float, float]) -> tuple[list[Point], bool]:
    tokens = PATH_TOKEN_RE.findall(d_attr)
    points: list[Point] = []
    cursor = Point(0.0, 0.0)
    start: Point | None = None
    closed = False
    i = 0

    def read_float() -> float:
        nonlocal i
        if i >= len(tokens):
            raise ValueError("Unexpected end of path.")
        value = float(tokens[i])
        i += 1
        return value

    while i < len(tokens):
        token = tokens[i]
        i += 1
        if token in ("M", "m", "L", "l"):
            x = read_float()
            y = read_float()
            if token.islower():
                cursor = Point(cursor.x + x, cursor.y + y)
            else:
                cursor = Point(x, y)
            transformed = apply_transform(cursor, matrix)
            points.append(transformed)
            if token in ("M", "m") and start is None:
                start = transformed
        elif token in ("H", "h"):
            x = read_float()
            cursor = Point(cursor.x + x, cursor.y) if token == "h" else Point(x, cursor.y)
            points.append(apply_transform(cursor, matrix))
        elif token in ("V", "v"):
            y = read_float()
            cursor = Point(cursor.x, cursor.y + y) if token == "v" else Point(cursor.x, y)
            points.append(apply_transform(cursor, matrix))
        elif token in ("Z", "z"):
            closed = True
            if start is not None:
                points.append(start)
        else:
            # Unsupported command or stray number. Skip the path by returning no points.
            return ([], closed)
    return (points, closed)


def is_axis_aligned_rect(points: list[Point], closed: bool) -> Rect | None:
    if not closed or len(points) < 5:
        return None
    unique: list[Point] = []
    for point in points:
        if not any(abs(point.x - old.x) < 0.01 and abs(point.y - old.y) < 0.01 for old in unique):
            unique.append(point)
    if len(unique) != 4:
        return None
    xs = sorted({round(point.x, 3) for point in unique})
    ys = sorted({round(point.y, 3) for point in unique})
    if len(xs) != 2 or len(ys) != 2:
        return None
    return Rect(xs[0], ys[0], xs[1], ys[1], label="rect")


def convert_page_to_svg(pdf_path: Path, page: int, tmpdir: Path) -> str:
    output = tmpdir / f"page-{page}.svg"
    # pdftocairo writes exactly the output path for SVG, without adding .svg.
    run_command(["pdftocairo", "-svg", "-f", str(page), "-l", str(page), str(pdf_path), str(output)])
    return output.read_text(encoding="utf-8", errors="replace")


def parse_svg_vectors(svg: str, page: int) -> PageVectors:
    page_match = re.search(rf'<svg[^>]*width="({NUMBER})pt"[^>]*height="({NUMBER})pt"', svg)
    width = float(page_match.group(1)) if page_match else 0.0
    height = float(page_match.group(2)) if page_match else 0.0
    body = svg.split("</defs>", 1)[-1]
    rects: list[Rect] = []
    segments: list[Segment] = []

    for match in re.finditer(r"<path\b[^>]*>", body):
        tag = match.group(0)
        attrs = parse_attrs(tag)
        if attrs.get("fill") != "none" or "stroke" not in attrs:
            continue
        d_attr = attrs.get("d")
        if not d_attr:
            continue
        stroke_width = float(attrs.get("stroke-width", "0.4"))
        matrix = parse_transform(attrs.get("transform"))
        points, closed = parse_path_points(d_attr, matrix)
        if len(points) < 2:
            continue

        rect = is_axis_aligned_rect(points, closed)
        if rect is not None:
            rects.append(rect)

        for left, right in zip(points, points[1:]):
            if math.hypot(right.x - left.x, right.y - left.y) < 0.25:
                continue
            segments.append(
                Segment(
                    page=page,
                    x1=left.x,
                    y1=left.y,
                    x2=right.x,
                    y2=right.y,
                    stroke_width=stroke_width,
                    dashed="stroke-dasharray" in attrs,
                    source=tag[:180],
                )
            )
    return PageVectors(page=page, width=width, height=height, svg=svg, rects=rects, segments=segments)


def extract_words(pdf_path: Path, page: int, tmpdir: Path) -> tuple[float, float, list[Word]]:
    html_path = tmpdir / f"page-{page}.html"
    run_command(["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(pdf_path), str(html_path)])
    content = html_path.read_text(encoding="utf-8", errors="replace")
    page_match = PAGE_RE.search(content)
    width = float(page_match.group(1)) if page_match else 0.0
    height = float(page_match.group(2)) if page_match else 0.0
    words: list[Word] = []
    for match in WORD_RE.finditer(content):
        x0, y0, x1, y1 = (float(match.group(i)) for i in range(1, 5))
        text = html.unescape(re.sub(r"\s+", " ", match.group(5)).strip())
        if not text:
            continue
        words.append(Word(page=page, text=text, rect=Rect(x0, y0, x1, y1, label=text)))
    return width, height, words


def clip_segment_length_to_rect(segment: Segment, rect: Rect) -> float:
    dx = segment.x2 - segment.x1
    dy = segment.y2 - segment.y1
    p = [-dx, dx, -dy, dy]
    q = [segment.x1 - rect.x0, rect.x1 - segment.x1, segment.y1 - rect.y0, rect.y1 - segment.y1]
    u1 = 0.0
    u2 = 1.0
    for pi, qi in zip(p, q):
        if abs(pi) < 1e-9:
            if qi < 0:
                return 0.0
            continue
        t = qi / pi
        if pi < 0:
            if t > u2:
                return 0.0
            if t > u1:
                u1 = t
        else:
            if t < u1:
                return 0.0
            if t < u2:
                u2 = t
    if u2 <= u1:
        return 0.0
    return segment.length * (u2 - u1)


def segment_is_internal_separator(segment: Segment, rect: Rect) -> bool:
    p1 = Point(segment.x1, segment.y1)
    p2 = Point(segment.x2, segment.y2)
    if not (rect.contains_point(p1, margin=0.75) and rect.contains_point(p2, margin=0.75)):
        return False
    horizontal = abs(segment.y1 - segment.y2) < 0.35
    vertical = abs(segment.x1 - segment.x2) < 0.35
    return horizontal or vertical


def segment_is_axis_aligned(segment: Segment) -> bool:
    return abs(segment.y1 - segment.y2) < 0.35 or abs(segment.x1 - segment.x2) < 0.35


def candidate_node_rects(rects: list[Rect], args: argparse.Namespace) -> list[Rect]:
    result = []
    for rect in rects:
        if rect.area < args.min_node_area or rect.area > args.max_node_area:
            continue
        if rect.width < args.min_node_width or rect.height < args.min_node_height:
            continue
        result.append(rect)
    return result


def detect_collisions(
    vectors: PageVectors,
    words: list[Word],
    args: argparse.Namespace,
) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    nodes = candidate_node_rects(vectors.rects, args)

    for segment in vectors.segments:
        if segment.length < args.min_line_length:
            continue
        for node in nodes:
            inset = node.inset(args.node_inset)
            if inset is None:
                continue
            if segment_is_internal_separator(segment, node):
                continue
            crossing_length = clip_segment_length_to_rect(segment, inset)
            if crossing_length >= args.min_crossing_length:
                issues.append(
                    {
                        "page": vectors.page,
                        "type": "line_vs_node",
                        "severity": "error",
                        "crossing_length_pt": round(crossing_length, 3),
                        "line": [round(segment.x1, 3), round(segment.y1, 3), round(segment.x2, 3), round(segment.y2, 3)],
                        "node_bbox": node.as_list(),
                        "dashed": segment.dashed,
                    }
                )

    if args.check_line_text:
        for segment in vectors.segments:
            if segment.length < args.min_line_length:
                continue
            if not args.include_axis_aligned_line_text and segment_is_axis_aligned(segment):
                continue
            if any(segment_is_internal_separator(segment, node) for node in nodes):
                continue
            for word in words:
                rect = word.rect.inflate(args.text_margin)
                crossing_length = clip_segment_length_to_rect(segment, rect)
                if crossing_length >= args.min_text_crossing_length:
                    issues.append(
                        {
                            "page": vectors.page,
                            "type": "line_vs_text",
                            "severity": "warning",
                            "crossing_length_pt": round(crossing_length, 3),
                            "text": word.text,
                            "text_bbox": word.rect.as_list(),
                            "line": [
                                round(segment.x1, 3),
                                round(segment.y1, 3),
                                round(segment.x2, 3),
                                round(segment.y2, 3),
                            ],
                            "dashed": segment.dashed,
                        }
                    )

    if args.check_text_text:
        for index, left in enumerate(words):
            left_rect = left.rect.inflate(args.text_text_margin)
            for right in words[index + 1 :]:
                if abs(left.rect.y0 - right.rect.y0) < 0.01 and abs(left.rect.x1 - right.rect.x0) < 1.5:
                    continue
                area = left_rect.intersection_area(right.rect.inflate(args.text_text_margin))
                if area >= args.min_text_overlap_area:
                    issues.append(
                        {
                            "page": vectors.page,
                            "type": "text_vs_text",
                            "severity": "warning",
                            "overlap_area_pt2": round(area, 3),
                            "left_text": left.text,
                            "right_text": right.text,
                            "left_bbox": left.rect.as_list(),
                            "right_bbox": right.rect.as_list(),
                        }
                    )
    return issues


def write_debug_svg(vectors: PageVectors, issues: list[dict[str, object]], output_path: Path) -> None:
    overlay = [
        '<g id="layout-collision-overlay" font-family="sans-serif" font-size="6" '
        'stroke-linecap="round" stroke-linejoin="round">'
    ]
    for index, issue in enumerate(issues, start=1):
        color = "red" if issue.get("severity") == "error" else "orange"
        if "node_bbox" in issue:
            x0, y0, x1, y1 = issue["node_bbox"]  # type: ignore[misc]
            overlay.append(
                f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" '
                f'fill="none" stroke="{color}" stroke-width="1.5"/>'
            )
        if "text_bbox" in issue:
            x0, y0, x1, y1 = issue["text_bbox"]  # type: ignore[misc]
            overlay.append(
                f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" '
                f'fill="none" stroke="{color}" stroke-width="1.2"/>'
            )
        if "line" in issue:
            x0, y0, x1, y1 = issue["line"]  # type: ignore[misc]
            overlay.append(
                f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" '
                f'stroke="{color}" stroke-width="1.3"/>'
            )
            overlay.append(
                f'<text x="{(x0 + x1) / 2:.3f}" y="{(y0 + y1) / 2 - 3:.3f}" '
                f'fill="{color}" stroke="none">{index}</text>'
            )
    overlay.append("</g>")
    svg = vectors.svg.replace("</svg>", "\n" + "\n".join(overlay) + "\n</svg>")
    output_path.write_text(svg, encoding="utf-8")


def write_markdown(path: Path, issues: list[dict[str, object]], debug_dir: Path | None = None) -> None:
    errors = sum(1 for issue in issues if issue.get("severity") == "error")
    warnings = sum(1 for issue in issues if issue.get("severity") == "warning")
    lines = [
        "# PDF Layout Collision Report",
        "",
        f"- Errors: {errors}",
        f"- Warnings: {warnings}",
        f"- Total: {len(issues)}",
        "",
    ]
    if debug_dir is not None:
        lines.append(f"Debug SVG directory: `{debug_dir}`")
        lines.append("")
    if issues:
        lines.append("| Page | Severity | Type | Metric | Detail |")
        lines.append("|---:|---|---|---:|---|")
        for issue in issues:
            metric = issue.get("crossing_length_pt", issue.get("overlap_area_pt2", ""))
            detail = ""
            if issue["type"] == "line_vs_node":
                detail = f"line={issue['line']} node={issue['node_bbox']}"
            elif issue["type"] == "line_vs_text":
                detail = f"text={issue.get('text', '')!r} line={issue['line']}"
            elif issue["type"] == "text_vs_text":
                detail = f"{issue.get('left_text', '')!r} vs {issue.get('right_text', '')!r}"
            lines.append(
                f"| {issue['page']} | {issue['severity']} | {issue['type']} | {metric} | `{detail}` |"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect line/text/node collisions in a PDF.")
    parser.add_argument("pdf", type=Path, help="PDF path to inspect.")
    parser.add_argument("--pages", help="Pages to inspect, e.g. 8 or 7-10. Default: all pages.")
    parser.add_argument("--json", type=Path, help="Write machine-readable issues JSON.")
    parser.add_argument("--markdown", type=Path, help="Write a Markdown report.")
    parser.add_argument("--debug-dir", type=Path, help="Write SVG pages with red/orange collision overlays.")
    parser.add_argument("--check-line-text", action="store_true", help="Also warn when vector lines cross word boxes.")
    parser.add_argument("--check-text-text", action="store_true", help="Also warn when word boxes overlap.")
    parser.add_argument(
        "--include-axis-aligned-line-text",
        action="store_true",
        help="Include horizontal/vertical structural lines in line/text checks. Noisy for TeX boxes and tables.",
    )
    parser.add_argument("--node-inset", type=float, default=1.0, help="Shrink node rectangles before line crossing checks.")
    parser.add_argument("--text-margin", type=float, default=0.4, help="Inflate word boxes for line/text checks.")
    parser.add_argument("--text-text-margin", type=float, default=0.0, help="Inflate word boxes for text/text checks.")
    parser.add_argument("--min-crossing-length", type=float, default=2.0)
    parser.add_argument("--min-text-crossing-length", type=float, default=0.8)
    parser.add_argument("--min-text-overlap-area", type=float, default=1.0)
    parser.add_argument("--min-line-length", type=float, default=3.0)
    parser.add_argument("--min-node-area", type=float, default=500.0)
    parser.add_argument("--max-node-area", type=float, default=12000.0)
    parser.add_argument("--min-node-width", type=float, default=12.0)
    parser.add_argument("--min-node-height", type=float, default=12.0)
    parser.add_argument(
        "--fail-on",
        choices=("error", "warning", "never"),
        default="error",
        help="Exit nonzero on errors, warnings, or never.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    pdf_path = args.pdf
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    for command in ("pdfinfo", "pdftocairo", "pdftotext"):
        if shutil.which(command) is None:
            raise SystemExit(f"Required command not found: {command}")

    total_pages = get_total_pages(pdf_path)
    pages = parse_pages(args.pages, total_pages) if args.pages else list(range(1, total_pages + 1))
    all_issues: list[dict[str, object]] = []
    debug_dir = args.debug_dir
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_name:
        tmpdir = Path(temp_name)
        for page in pages:
            svg = convert_page_to_svg(pdf_path, page, tmpdir)
            vectors = parse_svg_vectors(svg, page)
            _, _, words = extract_words(pdf_path, page, tmpdir)
            issues = detect_collisions(vectors, words, args)
            all_issues.extend(issues)
            if debug_dir is not None and issues:
                write_debug_svg(vectors, issues, debug_dir / f"page-{page:03d}.svg")

    if args.json:
        args.json.write_text(json.dumps(all_issues, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        write_markdown(args.markdown, all_issues, debug_dir)

    errors = sum(1 for issue in all_issues if issue.get("severity") == "error")
    warnings = sum(1 for issue in all_issues if issue.get("severity") == "warning")
    print(f"Checked {len(pages)} page(s): {errors} error(s), {warnings} warning(s), {len(all_issues)} total issue(s).")
    if errors:
        for issue in all_issues[:10]:
            if issue.get("severity") == "error":
                print(
                    f"page {issue['page']}: {issue['type']} "
                    f"crossing={issue.get('crossing_length_pt')}pt line={issue.get('line')} "
                    f"node={issue.get('node_bbox')}"
                )
    if args.fail_on == "warning" and all_issues:
        return 1
    if args.fail_on == "error" and errors:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

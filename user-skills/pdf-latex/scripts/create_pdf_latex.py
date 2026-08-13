#!/usr/bin/env python3
"""Generate a non-official Tetsuryoku-style handout PDF with LuaLaTeX."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


LEGACY_REQUIRED_CONTENT_KEYS = (
    "title_right",
    "title_left",
    "problem_title",
    "problem_source",
    "problem_body",
    "mini_title",
    "main_body",
    "point_title",
    "point_body",
)

LEGACY_OPTIONAL_CONTENT_KEYS = (
    "reference_title",
    "reference_source",
    "reference_body",
)

LEGACY_CONTENT_KEYS = LEGACY_REQUIRED_CONTENT_KEYS + LEGACY_OPTIONAL_CONTENT_KEYS

DOCUMENT_CONTENT_KEYS = (
    "title_right",
    "title_left",
    "document_body",
)

REQUIRED_COMMANDS = ("lualatex",)

FORBIDDEN_EVIDENCE_SECTION_LABELS = (
    "根拠",
    "このプリントの根拠",
    "作成根拠",
    "公式根拠",
    "参考根拠",
    "参照根拠",
    "出典根拠",
    "根拠にした資料",
    "根拠にしたローカル資料",
    "参考資料",
    "参考文献",
    "参考URL",
    "参考リンク",
    "参照資料",
    "出典一覧",
    "引用",
    "Citation",
    "Citations",
    "Source",
    "Source Notes",
    "Reference",
    "Sources",
    "References",
    "Bibliography",
)

REQUIRED_DIAGRAM_EXPLANATION_LABELS = (
    "図の読み方",
    "図解",
    "図から分かること",
    "読み取り方",
    "解答手順",
)

DIAGRAM_EXPLANATION_CONTEXT_CHARS = 1600

DIAGRAM_EXPLANATION_MIN_CHARS = 32

DIAGRAM_EXPLANATION_BODY_KEYWORDS = (
    "図",
    "番号",
    "矢印",
    "ステップ",
    "責務",
    "手順",
    "流れ",
    "対応",
    "段階",
)

DIAGRAM_IMAGE_PATTERN = re.compile(r"\\diagramimage(?:\[[^\]]*\])?\{[^}]+\}")

DIAGRAM_NUMBER_REFERENCE_PATTERN = re.compile(r"図(?:の)?\s*[0-9０-９一二三四五六七八九十]")

LABELED_EXPLANATION_BLOCK_PATTERN = re.compile(
    r"\\begin\{(?P<env>keypoints|solutionblock)\}\[(?P<label>[^\]]+)\]"
    r"(?P<body>.*?)"
    r"\\end\{(?P=env)\}",
    re.DOTALL,
)

SECTION_HEADING_PATTERN = re.compile(
    r"\\(?:section|subsection|subsubsection|paragraph)\*?\{(?P<label>[^}]+)\}"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a non-official Tetsuryoku-style Japanese study handout PDF."
    )
    parser.add_argument("--output", required=True, help="Output .tex path. The PDF is written next to it.")
    parser.add_argument("--content-json", help="JSON file containing all content fields.")
    parser.add_argument(
        "--plain-text",
        action="store_true",
        help="Escape legacy flat content fields as plain text. Not supported with document_body.",
    )
    parser.add_argument("--no-compile", action="store_true", help="Write only the .tex file.")
    parser.add_argument(
        "--allow-evidence-sections",
        action="store_true",
        help="Allow visible source/reference/evidence sections only when the user explicitly requests them.",
    )
    parser.add_argument(
        "--document-body",
        "--body-tex",
        dest="document_body",
        help="TeX source for the document body. Prefer this for multi-problem handouts.",
    )
    for key in LEGACY_CONTENT_KEYS:
        parser.add_argument(f"--{key.replace('_', '-')}", help=f"TeX source for {key}.")
    return parser


def load_content(args: argparse.Namespace) -> dict[str, str]:
    if args.content_json is not None:
        content_path = Path(args.content_json)
        data = json.loads(content_path.read_text(encoding="utf-8"))
        return load_content_from_mapping(data, args.plain_text, args.allow_evidence_sections)

    values = vars(args)
    if values["document_body"] is not None:
        if args.plain_text:
            raise SystemExit("--plain-text is only supported with legacy flat fields; document_body must be TeX source.")
        missing_document_args = [
            f"--{key.replace('_', '-')}"
            for key in DOCUMENT_CONTENT_KEYS
            if values[key] is None
        ]
        if missing_document_args:
            raise SystemExit(f"Missing required document content arguments: {', '.join(missing_document_args)}")
        return {key: require_text(values[key], key) for key in DOCUMENT_CONTENT_KEYS}

    return load_legacy_content_from_cli(values, args.plain_text, args.allow_evidence_sections)


def load_content_from_mapping(data: object, plain_text: bool, allow_evidence_sections: bool) -> dict[str, str]:
    if not isinstance(data, dict):
        raise SystemExit("content-json must contain a JSON object.")
    if "document_body" in data:
        if plain_text:
            raise SystemExit("--plain-text is only supported with legacy flat fields; document_body must be TeX source.")
        missing = [key for key in DOCUMENT_CONTENT_KEYS if key not in data]
        if missing:
            raise SystemExit(f"Missing required content-json keys: {', '.join(missing)}")
        return {key: require_text(data[key], key) for key in DOCUMENT_CONTENT_KEYS}

    missing = [key for key in LEGACY_REQUIRED_CONTENT_KEYS if key not in data]
    if missing:
        raise SystemExit(f"Missing required content-json keys: {', '.join(missing)}")
    legacy_content = {key: require_text(data[key], key) for key in LEGACY_REQUIRED_CONTENT_KEYS}
    add_optional_legacy_content(legacy_content, data, allow_evidence_sections)
    return build_legacy_document_content(escape_legacy_content(legacy_content, plain_text))


def load_legacy_content_from_cli(
    values: dict[str, object],
    plain_text: bool,
    allow_evidence_sections: bool,
) -> dict[str, str]:
    content: dict[str, str] = {}
    missing: list[str] = []
    for key in LEGACY_REQUIRED_CONTENT_KEYS:
        value = values[key]
        if value is None:
            missing.append(f"--{key.replace('_', '-')}")
            continue
        content[key] = require_text(value, key)

    if missing:
        missing_args = ", ".join(missing)
        raise SystemExit(f"Missing required content arguments: {missing_args}")

    add_optional_legacy_content(content, values, allow_evidence_sections)
    return build_legacy_document_content(escape_legacy_content(content, plain_text))


def add_optional_legacy_content(
    content: dict[str, str],
    values: dict[str, object],
    allow_evidence_sections: bool,
) -> None:
    optional_values = {
        key: values[key]
        for key in LEGACY_OPTIONAL_CONTENT_KEYS
        if key in values and values[key] not in (None, "")
    }
    if not optional_values:
        return
    missing_optional = [key for key in LEGACY_OPTIONAL_CONTENT_KEYS if key not in optional_values]
    if missing_optional:
        raise SystemExit(
            "Legacy reference fields must be supplied together: "
            + ", ".join(LEGACY_OPTIONAL_CONTENT_KEYS)
        )
    if not allow_evidence_sections:
        raise SystemExit(
            "Legacy reference fields create visible source/reference/evidence sections. "
            "Use document_body without those sections, or rerun with --allow-evidence-sections "
            "only when the user explicitly asks for them."
        )
    for key, value in optional_values.items():
        content[key] = require_text(value, key)


def require_text(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise SystemExit(f"{key} must be a string.")
    if value == "":
        raise SystemExit(f"{key} must not be empty.")
    return value


def escape_legacy_content(content: dict[str, str], plain_text: bool) -> dict[str, str]:
    if not plain_text:
        return content
    return {key: escape_latex(value) for key, value in content.items()}


def escape_latex(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "{": r"\{",
        "}": r"\}",
        "$": r"\$",
        "&": r"\&",
        "#": r"\#",
        "_": r"\_",
        "%": r"\%",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements[character] if character in replacements else character for character in value)


def build_legacy_document_content(content: dict[str, str]) -> dict[str, str]:
    document_body = f"""\\begin{{problemcard}}[{content["problem_source"]}]{{{content["problem_title"]}}}
{content["problem_body"]}
\\end{{problemcard}}

\\begin{{handoutcolumns}}
  \\ascboxZ{{{content["mini_title"]}}}
{content["main_body"]}
  \\answerspace[5]

  \\begin{{keypoints}}[{content["point_title"]}]
{content["point_body"]}
  \\end{{keypoints}}
\\end{{handoutcolumns}}
"""
    if all(key in content for key in LEGACY_OPTIONAL_CONTENT_KEYS):
        document_body += f"""
\\begin{{solutionblock}}[{content["reference_title"]}]
出典：{content["reference_source"]}

{content["reference_body"]}
\\end{{solutionblock}}
"""
    return {
        "title_right": content["title_right"],
        "title_left": content["title_left"],
        "document_body": document_body,
    }


def render_template(template_path: Path, content: dict[str, str]) -> str:
    rendered = template_path.read_text(encoding="utf-8")
    replacements = {
        "__TITLE_RIGHT__": content["title_right"],
        "__TITLE_LEFT__": content["title_left"],
        "__DOCUMENT_BODY__": content["document_body"],
    }
    for token, replacement in replacements.items():
        rendered = rendered.replace(token, replacement)

    unresolved = [token for token in replacements if token in rendered]
    if unresolved:
        raise SystemExit(f"Template rendering left unresolved tokens: {', '.join(unresolved)}")
    return rendered


def validate_document_body(document_body: str, allow_evidence_sections: bool) -> None:
    if allow_evidence_sections:
        validate_diagram_usage(document_body)
        return
    validate_forbidden_evidence_sections(document_body)
    validate_diagram_usage(document_body)


def validate_forbidden_evidence_sections(document_body: str) -> None:
    labels = [block["label"] for block in collect_labeled_explanation_blocks(document_body)]
    labels.extend(match.group("label") for match in SECTION_HEADING_PATTERN.finditer(document_body))
    forbidden_labels = [label for label in labels if is_forbidden_evidence_label(label)]
    if not forbidden_labels:
        return
    joined_labels = ", ".join(forbidden_labels)
    raise SystemExit(
        "Visible source/reference/evidence sections are disabled by default: "
        f"{joined_labels}. Remove the section, or use --allow-evidence-sections only when "
        "the user explicitly asks for it."
    )


def validate_diagram_usage(document_body: str) -> None:
    diagram_positions = [match.start() for match in DIAGRAM_IMAGE_PATTERN.finditer(document_body)]
    if not diagram_positions:
        return
    explanation_blocks = [
        block
        for block in collect_labeled_explanation_blocks(document_body)
        if is_diagram_explanation_label(block["label"])
    ]
    for diagram_position in diagram_positions:
        has_nearby_explanation = any(
            abs(diagram_position - block["position"]) <= DIAGRAM_EXPLANATION_CONTEXT_CHARS
            and has_diagram_explanation_body(block["body"])
            for block in explanation_blocks
        )
        if has_nearby_explanation:
            continue
        expected_labels = ", ".join(REQUIRED_DIAGRAM_EXPLANATION_LABELS)
        raise SystemExit(
            "Diagram images must be tied to the problem explanation. "
            "Add a nearby keypoints or solutionblock section with a specific body that explains "
            "multiple figure numbers, arrows, steps, responsibilities, or flow details. Accepted labels: "
            f"{expected_labels}."
        )


def collect_labeled_explanation_blocks(document_body: str) -> list[dict[str, str | int]]:
    return [
        {
            "position": match.start(),
            "env": match.group("env"),
            "label": match.group("label"),
            "body": match.group("body"),
        }
        for match in LABELED_EXPLANATION_BLOCK_PATTERN.finditer(document_body)
    ]


def is_forbidden_evidence_label(label: str) -> bool:
    normalized_label = label.casefold()
    return any(
        forbidden_label.casefold() in normalized_label
        for forbidden_label in FORBIDDEN_EVIDENCE_SECTION_LABELS
    )


def is_diagram_explanation_label(label: str) -> bool:
    return any(required_label in label for required_label in REQUIRED_DIAGRAM_EXPLANATION_LABELS)


def has_diagram_explanation_body(body: str) -> bool:
    stripped_body = body.strip()
    if len(stripped_body) < DIAGRAM_EXPLANATION_MIN_CHARS:
        return False
    keyword_count = sum(keyword in stripped_body for keyword in DIAGRAM_EXPLANATION_BODY_KEYWORDS)
    has_number_reference = DIAGRAM_NUMBER_REFERENCE_PATTERN.search(stripped_body) is not None
    return keyword_count >= 3 or (has_number_reference and keyword_count >= 2)


def require_commands() -> None:
    for command in REQUIRED_COMMANDS:
        command_path = shutil.which(command)
        if command_path is None:
            raise SystemExit(f"Required command not found: {command}")


def compile_pdf(tex_path: Path) -> None:
    require_commands()
    command = [
        "lualatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        tex_path.name,
    ]
    for _ in range(2):
        subprocess.run(command, cwd=tex_path.parent, check=True)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    output_path = Path(args.output).expanduser().resolve()
    if output_path.suffix != ".tex":
        raise SystemExit("--output must end with .tex")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    skill_dir = Path(__file__).resolve().parents[1]
    template_path = skill_dir / "assets" / "template.tex"
    content = load_content(args)
    validate_document_body(content["document_body"], args.allow_evidence_sections)
    output_path.write_text(render_template(template_path, content), encoding="utf-8")

    if not args.no_compile:
        compile_pdf(output_path)


if __name__ == "__main__":
    main()

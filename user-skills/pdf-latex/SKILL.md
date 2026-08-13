---
name: pdf-latex
description: Create non-official Tetsuryoku-style Japanese study handout or unit-based problem-set PDFs with LuaLaTeX, including worked examples, practice problems, one-point checkpoints, teacher/student dialogue explanations, and optional drawio-made diagram images. Use only when the user explicitly invokes `$pdf-latex` or clearly asks to use this skill for a JIS B5, boxed-problem, reference-book-like LaTeX handout PDF; do not implicitly apply it to ordinary LaTeX, generic PDFs, resumes, reports, or documents.
---

# PDF LaTeX

## Overview

Create a Japanese JIS B5 study-print PDF with a header line, boxed problem areas, point boxes, explanatory blocks, optional diagram images, and note space. This skill is not a command that pastes the Qiita article source verbatim; it uses a maintained template and a generator script so the result can be validated and compiled repeatably.

The visual style is non-official and only "Tetsuryoku-style". Do not claim affiliation with, endorsement by, or exact reproduction of any school, company, or publisher.

## Workflow

1. Confirm the output path, right header, left header, and the problem-set body.
2. For new problem sets, organize `document_body` by unit with `\unitsection{...}`. Inside each unit, repeat `workedexample -> exampledialogue` for all worked examples, then place all practice problems, such as `例題1 -> 対話1 -> 例題2 -> 対話2 -> 問題1 -> 問題2`.
3. Use one `workedexample` or `practiceproblem` per item. The number of worked examples and practice problems is variable-length, but each item must have exactly one main checkpoint and must introduce or test only one new concept.
4. Put `exampledialogue` immediately after each `workedexample`. Use `\teacher{...}` and `\student{...}` so the learner can understand the concept from the dialogue alone.
5. Put practice explanations immediately after the corresponding problem when an answer or solution is included. Use `\answerspace[n]` when the reader needs space to solve or annotate.
6. Use `handoutcolumns` only for dense explanation or drills. Do not put many unrelated problems into one giant box.
7. Use `\code{...}` for API paths, table names, columns, and snake_case so long code-like text remains visually distinct.
8. When APIs, DBs, UML, data flow, state changes, or architecture are easier to understand visually, use the `drawio` skill to create an editable `.drawio` plus a PDF or PNG export. The figure must explain the problem, not merely illustrate the topic: add numbered labels or callouts that map to the answer steps.
9. Treat `document_body` as TeX source. Use `--plain-text` only for the legacy flat-field interface.
10. Generate the PDF with `scripts/create_pdf_latex.py`.
11. Let the script run `lualatex` twice. Do not skip or hide compile errors.
12. When the PDF contains diagrams, UML, ER, flow/state/sequence charts, dense tables, or long code/API paths, run `scripts/check_pdf_layout_collisions.py` on the generated PDF. Treat `line_vs_node` errors as layout failures that must be fixed before final delivery. Use `--check-line-text` when line labels or dense annotations may be colliding with connector lines.
13. Report the generated `.tex`, `.pdf`, diagram asset paths when used, layout collision report paths when generated, and any compile failure log path.

## Problem Set Structure

For each unit, build a short learning progression:

1. `\unitsection{単元名}`
2. `workedexample` blocks for the ideas the teacher explains
3. `practiceproblem` blocks that ask the learner to use those ideas

Keep one concept per item. Before writing the TeX, list the checkpoint for each item in working notes and split any item whose checkpoint contains multiple verbs such as "define and calculate", "compare and prove", or "read and transform". The generator does not enforce one-concept design, so the author must check it before compiling.

Use variable-length sequences naturally. A unit may contain one worked example and one practice problem, or several of each. Once practice problems start in a unit, do not return to new worked examples in the same unit; start a new `\unitsection` instead.

## Standalone Learning Design

When the PDF must work without the original source material, design it as a self-contained mini lesson before writing TeX.

1. Define the learner, the purpose, and the assumed knowledge first. If a beginner would not know a word, the PDF must teach that word before it appears in a problem.
2. Use a short scenario for each unit or item. State who is building what and why, such as "a student is building an ordering feature for a restaurant app because staff need to reduce manual order mistakes".
3. Prefer this sequence for beginner material: `登場人物 -> 基礎解説 -> workedexample（例題） -> exampledialogue（解答例と解説） -> practiceproblem（練習問題） -> answerspace -> solutionblock（解答例と解説）`. Omit only the parts that the user explicitly says are unnecessary.
4. Make every example a real problem. A `workedexample` must contain an explicit question, enough conditions to solve it, and a concrete expected answer. A topic summary is not an example.
5. Practice problems must be answerable without guessing the intent. Each problem should include a situation, task, and question labels such as `問1`, `問2`, and `問3`.
6. Worked examples need both an answer and an explanation. Practice problems need both an answer and an explanation when solutions are included. The answer states what to write; the explanation states why it is correct.
7. Audit prerequisite terms before compiling. For system-design handouts, define terms such as API, Path, HTTP method, request parameter, request body, response, class, property, method, table, primary key, foreign key, and relation before asking the learner to use them.
8. Keep the same flow and granularity across units. If the first unit uses story, basic explanation, example, answer, and explanation, later units should not suddenly jump straight into abstract prompts.
9. If the user provides a model text, extract the pedagogical structure, granularity, and learner-facing flow, then recreate them for the new subject without reusing source phrasing.
10. Respect user-specified punctuation in learner-facing Japanese text. If the user asks for `、` and `。`, use them in prose, question text, answer blocks, and diagram labels. Code paths, commands, and filenames may keep their literal punctuation.
11. Diagrams must teach the same concept as the nearby problem. Numbered callouts such as `1：` are easier to read in Japanese learning material than sentence-like labels when punctuation is constrained.
12. Verify the final PDF as a learning artifact, not only as a compiled file. Use `pdftotext handout.pdf -` or an equivalent text extraction command, then check for missing prerequisite definitions, missing answer or explanation blocks, unintended punctuation, and ambiguous labels such as repeated "問題" headings with no concrete question.

## Body Macros

Use these macros in `document_body`:

```tex
\unitsection{一次関数}

\begin{workedexample}[出典：自作]{例題1}{傾きは, x が1増えたときの y の増加量であることを確認する.}
次の直線の傾きを求める.
\[
  y = 2x + 3
\]
\end{workedexample}

\begin{exampledialogue}
\teacher{この例題では, 直線の式から傾きだけを読み取る練習をします.}
\student{傾きはどこを見ればよいですか.}
\teacher{\(y = ax + b\) の \(a\) を見ます. 今回は \(a = 2\) なので, 傾きは2です.}
\student{x が1増えると, y が2増えるという意味ですね.}
\end{exampledialogue}

\begin{practiceproblem}[出典：自作]{問題1}{式 \(y = ax + b\) から傾きだけを読み取る.}
次の直線の傾きを求める.
\[
  y = -3x + 5
\]
\end{practiceproblem}
\answerspace[4]

\diagramimage[APIとDBの関係]{figures/api-db-flow.pdf}

\begin{solutionblock}[図の読み方]
図1の番号1はリクエスト送信, 矢印2はDB参照を表す. 本文の解答手順1,2と対応させて読む.
\end{solutionblock}

\answerspace[6]
\code{/conversations/{conversation_id}/messages}
```

For new problem sets, prefer `unitsection`, `workedexample`, `practiceproblem`, and `exampledialogue`. The older `problemcard -> keypoints -> solutionblock -> answerspace` pattern is still valid for legacy handouts and custom layouts.

## Generator

Use the script from the skill directory. Prefer `--content-json` with `document_body` for multi-problem handouts:

```bash
python3 <skill-dir>/scripts/create_pdf_latex.py --output handout.tex --content-json content.json
```

The JSON object for the preferred interface must contain: `title_right`, `title_left`, and `document_body`.

You can also pass the body directly:

```bash
python3 <skill-dir>/scripts/create_pdf_latex.py --output handout.tex --title-right "右ヘッダー" --title-left "左ヘッダー" --body-tex "\begin{problemcard}[出典：自作]{例題1}問題文\end{problemcard}"
```

Legacy flat fields are still accepted for older calls: `problem_title`, `problem_source`, `problem_body`, `mini_title`, `main_body`, `point_title`, `point_body`, `reference_title`, `reference_source`, and `reference_body`.

The legacy `reference_title`, `reference_source`, and `reference_body` fields are optional. They create a visible source/reference/evidence block and require `--allow-evidence-sections`. Do not use them unless the user explicitly asks for such a section.

Add `--plain-text` only when using the legacy flat fields as ordinary text rather than TeX source.

Add `--allow-evidence-sections` only when the user explicitly requests visible source, reference, evidence, or citation sections.

Use `--no-compile` only when the user explicitly wants a `.tex` file without building a PDF.

## Requirements

- Require LuaLaTeX and a current TeX Live environment.
- Require these TeX packages/classes: `jlreq`, `luatexja`, `luwa-ul`, `KKsymbols`, `keisennote`, `multicol`, `amsmath`, `amssymb`, `fancyhdr`, `enumitem`, `xparse`, `xurl`, `graphicx`, `multicolrule`, `tcolorbox`, `calc`, `varwidth`, `geometry`, and TikZ.
- For layout collision checks, require Poppler CLI tools: `pdfinfo`, `pdftocairo`, and `pdftotext`. The checker uses only the Python standard library plus these commands; it does not require PyMuPDF, Shapely, Pillow, or other Python packages.
- Check dependencies with `kpsewhich` when compilation fails before editing the template.

## Layout Collision Check

Use the checker after PDF generation when visual overlap would affect learning or interview preparation. The checker inspects the final PDF rather than the TeX source, so it catches issues that only appear after rendering.

```bash
python3 <skill-dir>/scripts/check_pdf_layout_collisions.py handout.pdf \
  --markdown layout_collision_report.md \
  --json layout_collision_report.json \
  --debug-dir layout_collision_debug
```

Use `--pages 7-10` to focus on changed pages. By default, the checker reports connector lines that cross small node-like rectangles, such as UML class boxes. A typical failure is:

```text
line_vs_node crossing=70.697pt line=[...] node=[...]
```

This means a line crosses the interior of a diagram node by that many points. Fix by rerouting the line, moving the nodes, splitting the figure, or converting a direct connector into an outer-lane polyline. Re-run the checker until `line_vs_node` errors are zero.

For possible line-label or annotation collisions, add `--check-line-text`. This can produce warnings around intentional labels, so treat warnings as review items rather than automatic failures. Use the SVGs in `layout_collision_debug/` to see the flagged line and box overlays.

## Readability Rules

- A unit-based problem set should read in this order: unit title, repeat `workedexample -> exampledialogue` as needed, then practice problems with answer space or solutions.
- Each worked example or practice problem must show a single `確認ポイント`. If a checkpoint contains two concepts, split the item.
- The teacher/student dialogue should teach the concept, not only state the answer. Make the student ask the likely beginner question and make the teacher answer with the minimum idea needed for that example.
- Break long material into short blocks with visible labels. Avoid one large problem box containing many unrelated questions.
- Keep API paths and identifiers in `\code{...}`. Put HTTP methods outside the macro, such as `POST \code{/conversations}`. If a path is too long, rewrite the prose or split the example rather than allowing unreadable wrapping.
- Do not add visible meta sections such as `このプリントの根拠`, `作成根拠`, `公式根拠`, `Source Notes`, `Sources`, or `References` unless the user explicitly requests them. Keep source checks and reasoning in the working notes, not in the learner-facing PDF.
- Do not add decorative diagrams. Use `drawio` diagrams only when relationships, structure, branching, or data movement become clearer than prose.
- A diagram must be coupled to the exercise. It should answer one of these learner questions: "where should I look?", "what happens first?", "which component is responsible?", or "why is this answer correct?"
- Every inserted diagram must be followed or preceded by a nearby short `keypoints` or `solutionblock` section labeled `図の読み方`, `図解`, `図から分かること`, `読み取り方`, or `解答手順`. The body of that block must specifically explain the figure numbers, arrows, steps, responsibilities, or flow; a label-only or one-word explanation is not enough. The generator rejects each `\diagramimage` that is not coupled to such a nearby explanation block.
- Prefer diagrams with numbered steps, responsibility labels, and answer-relevant arrows. Avoid generic system overviews that can be removed without changing the explanation.
- For drawio diagrams, keep the editable `.drawio` near the TeX output and insert a LaTeX-readable PDF or PNG export. Prefer PDF for vector quality; use PNG only when PDF export is unavailable.
- For TikZ or inserted diagram figures, avoid connector lines that pass through node interiors. If a connector must travel across the figure, route it around the outside using explicit bend points or split the figure. Validate the final PDF with `scripts/check_pdf_layout_collisions.py`; successful compilation alone is not sufficient evidence that a diagram is visually usable.
- Prefer short paragraphs. Use itemize/enumerate only when the list structure matters.
- The layout follows general textbook readability findings: visual hierarchy and typographic cues help learning, and clear design favors headings, whitespace, and controlled line length.

## Source Notes

- The design is based on the public Qiita article "３秒でできる鉄緑っぽいプリント作成（非公式）" by KKTeX: https://qiita.com/KKTeX/items/559d524eeb51a3528051
- The article states that it is non-official and requires current TeX Live plus LuaLaTeX.
- Portions inspired by `ascolorbox` must retain the MIT License notice included in `assets/template.tex`.
- Readability references: https://journals.uc.edu/index.php/vl/article/view/5267, https://www.journals.uc.edu/index.php/vl/article/view/5483, https://www.cleo.on.ca/en/publications/clear-design-tips/all

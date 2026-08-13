from __future__ import annotations

import re
import unittest
from pathlib import Path


CODEX_HOME = Path(__file__).resolve().parents[1]
SKILL_ROOT = CODEX_HOME / "user-skills" / "best-human-ui"
REFERENCE_ROOT = SKILL_ROOT / "references"
PRINCIPLE_FILES = {
    "model.md": 11,
    "interaction.md": 42,
    "presentation.md": 47,
}
ENTRY_PATTERN = re.compile(r"^## (?P<number>\d+)\. (?P<title>.+)$", re.MULTILINE)
EXPECTED_RELATED_SOURCES = (
    ("https://www.sociomedia.co.jp/318", {3}),
    ("https://www.sociomedia.co.jp/295", {6}),
    ("https://www.sociomedia.co.jp/298", {8}),
    ("https://www.sociomedia.co.jp/326", {9}),
    ("https://www.sociomedia.co.jp/3950", {9}),
    ("https://www.sociomedia.co.jp/349", {16}),
    ("https://www.sociomedia.co.jp/259", {17}),
    ("https://www.sociomedia.co.jp/2858", {18}),
    ("https://www.sociomedia.co.jp/207", {20}),
    ("https://www.sociomedia.co.jp/331", {22}),
    ("https://www.sociomedia.co.jp/8653", {23}),
    ("https://www.sociomedia.co.jp/7279", {23}),
    (
        "https://www.nngroup.com/articles/response-times-3-important-limits/",
        {65},
    ),
    ("https://www.sociomedia.co.jp/7304", {70}),
    ("https://www.sociomedia.co.jp/4152", {78, 92, 93}),
    ("https://www.sociomedia.co.jp/4114", {79}),
)


class BestHumanUiSkillTest(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        expected = (
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "agents" / "openai.yaml",
            REFERENCE_ROOT / "task-index.md",
            REFERENCE_ROOT / "model.md",
            REFERENCE_ROOT / "interaction.md",
            REFERENCE_ROOT / "presentation.md",
            REFERENCE_ROOT / "related-sources.md",
        )
        for path in expected:
            with self.subTest(path=path):
                self.assertTrue(path.is_file(), path)

    def test_skill_frontmatter_contains_only_name_and_description(self) -> None:
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"\A---\n(?P<frontmatter>.*?)\n---\n", text, re.DOTALL)
        self.assertIsNotNone(match)
        keys = re.findall(r"^(\w[\w-]*):", match.group("frontmatter"), re.MULTILINE)
        self.assertEqual(keys, ["name", "description"])
        self.assertIn("name: best-human-ui", match.group("frontmatter"))

    def test_openai_metadata_enables_implicit_invocation(self) -> None:
        text = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "Best Human UI"', text)
        self.assertIn("$best-human-ui", text)
        self.assertRegex(text, r"(?m)^policy:\n  allow_implicit_invocation: true$")

    def test_principles_are_complete_and_unique(self) -> None:
        actual_numbers: list[int] = []
        for filename, expected_count in PRINCIPLE_FILES.items():
            text = (REFERENCE_ROOT / filename).read_text(encoding="utf-8")
            entries = list(ENTRY_PATTERN.finditer(text))
            with self.subTest(filename=filename):
                self.assertEqual(len(entries), expected_count)
            actual_numbers.extend(int(entry.group("number")) for entry in entries)
        self.assertEqual(sorted(actual_numbers), list(range(1, 101)))
        self.assertEqual(len(actual_numbers), len(set(actual_numbers)))

    def test_every_principle_has_required_fields(self) -> None:
        required_fields = ("公式タグ", "使う場面", "判断", "確認", "出典")
        for filename in PRINCIPLE_FILES:
            text = (REFERENCE_ROOT / filename).read_text(encoding="utf-8")
            entries = list(ENTRY_PATTERN.finditer(text))
            for index, entry in enumerate(entries):
                end = entries[index + 1].start() if index + 1 < len(entries) else len(text)
                body = text[entry.end() : end]
                with self.subTest(filename=filename, number=entry.group("number")):
                    for field in required_fields:
                        self.assertRegex(body, rf"(?m)^- {field}: .+$")
                    self.assertRegex(
                        body,
                        r"(?m)^- 出典: https://www\.sociomedia\.co\.jp/\d+$",
                    )

    def test_task_index_has_23_tags_and_covers_every_principle(self) -> None:
        text = (REFERENCE_ROOT / "task-index.md").read_text(encoding="utf-8")
        mappings = re.findall(r"(?m)^- `(?P<tag>shig-[^`]+)`: (?P<numbers>[0-9, ]+)$", text)
        self.assertEqual(len(mappings), 23)
        self.assertEqual(len({tag for tag, _ in mappings}), 23)
        covered = {
            int(number)
            for _, numbers in mappings
            for number in numbers.split(", ")
        }
        self.assertEqual(covered, set(range(1, 101)))

        indexed_assignments = {
            tag: {int(number) for number in numbers.split(", ")}
            for tag, numbers in mappings
        }
        documented_assignments = {tag: set() for tag in indexed_assignments}
        for filename in PRINCIPLE_FILES:
            principle_text = (REFERENCE_ROOT / filename).read_text(encoding="utf-8")
            entries = list(ENTRY_PATTERN.finditer(principle_text))
            for index, entry in enumerate(entries):
                end = (
                    entries[index + 1].start()
                    if index + 1 < len(entries)
                    else len(principle_text)
                )
                body = principle_text[entry.end() : end]
                tag_line = re.search(r"(?m)^- 公式タグ: (.+)$", body)
                self.assertIsNotNone(tag_line)
                number = int(entry.group("number"))
                for tag in re.findall(r"`(shig-[^`]+)`", tag_line.group(1)):
                    self.assertIn(tag, documented_assignments)
                    documented_assignments[tag].add(number)
        self.assertEqual(documented_assignments, indexed_assignments)

    def test_related_sources_are_bounded_to_16_pages(self) -> None:
        text = (REFERENCE_ROOT / "related-sources.md").read_text(encoding="utf-8")
        entry_matches = list(re.finditer(r"(?m)^## REL-(\d{2}) .+$", text))
        entries = [match.group(1) for match in entry_matches]
        urls = re.findall(r"(?m)^- 出典: (https://\S+)$", text)
        self.assertEqual(entries, [f"{number:02d}" for number in range(1, 17)])
        self.assertEqual(len(urls), 16)
        self.assertEqual(
            sum(url.startswith("https://www.sociomedia.co.jp/") for url in urls),
            15,
        )
        self.assertEqual(
            urls.count(
                "https://www.nngroup.com/articles/response-times-3-important-limits/"
            ),
            1,
        )
        actual_sources: list[tuple[str, set[int]]] = []
        for index, entry in enumerate(entry_matches):
            end = (
                entry_matches[index + 1].start()
                if index + 1 < len(entry_matches)
                else len(text)
            )
            body = text[entry.end() : end]
            with self.subTest(related_source=entry.group(1)):
                self.assertRegex(body, r"(?m)^- 関連SHIG: .+$")
                self.assertRegex(body, r"(?m)^- 要約: .+$")
                self.assertRegex(body, r"(?m)^- 出典: https://\S+$")
                related_line = re.search(r"(?m)^- 関連SHIG: ([0-9, ]+)$", body)
                self.assertIsNotNone(related_line)
                related_numbers = {
                    int(number) for number in related_line.group(1).split(", ")
                }
                self.assertTrue(related_numbers)
                self.assertTrue(related_numbers <= set(range(1, 101)))
                source_line = re.search(r"(?m)^- 出典: (https://\S+)$", body)
                self.assertIsNotNone(source_line)
                actual_sources.append((source_line.group(1), related_numbers))
        self.assertEqual(tuple(actual_sources), EXPECTED_RELATED_SOURCES)

    def test_references_exclude_embedded_images_and_html(self) -> None:
        for path in sorted(REFERENCE_ROOT.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertIn("収録日: 2026-07-16", text)
                self.assertNotRegex(text, r"<\/?(?:p|img|div|span)\b")
                self.assertNotIn("![", text)
                if len(text.splitlines()) > 100:
                    first_entry = re.search(r"(?m)^## (?:\d+\.|REL-\d+)", text)
                    self.assertIsNotNone(first_entry)
                    self.assertIn("## 目次", text[: first_entry.start()])


if __name__ == "__main__":
    unittest.main()

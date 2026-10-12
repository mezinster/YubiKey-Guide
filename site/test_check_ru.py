import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_ru  # noqa: E402

FENCE = "`" * 3


class CheckRuTests(unittest.TestCase):
    def test_heading_ids(self):
        html = '<h1 id="t">T</h1><h2 id="a">A</h2><h3 id="b-c">B</h3><p id="x">'
        self.assertEqual(check_ru.heading_ids(html), ["a", "b-c"])

    def test_code_blocks_are_dedented(self):
        md = f"Text\n\n    {FENCE}sh\n    gpg -K\n    {FENCE}\n\n{FENCE}\nls\n{FENCE}\n"
        self.assertEqual(check_ru.code_blocks(md), [f"{FENCE}sh\ngpg -K\n{FENCE}", f"{FENCE}\nls\n{FENCE}"])

    def site(self, root, lang_build, lang_site, page, md, html):
        (root / lang_build).mkdir(parents=True, exist_ok=True)
        (root / lang_build / page).write_text(md, encoding="utf-8")
        path = check_ru.html_path(root / lang_site, page)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")

    def compare_one(self, en_md, en_html, ru_md, ru_html):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.site(root, "b", "s", "piv.md", en_md, en_html)
            self.site(root, "br", "sr", "piv.md", ru_md, ru_html)
            return check_ru.compare(root / "b", root / "br", root / "s", root / "sr", ["piv.md"])

    def test_matching_pages_pass(self):
        md = f"## A\n\n{FENCE}\nls\n{FENCE}\n"
        self.assertEqual(self.compare_one(md, '<h2 id="a">', md.replace("## A", "## А {#a}"),
                                          '<h2 id="a">'), [])

    def test_missing_heading_and_changed_code_are_reported(self):
        problems = self.compare_one(f"## A\n\n{FENCE}\nls\n{FENCE}\n", '<h2 id="a"><h2 id="b">',
                                    f"## А\n\n{FENCE}\nls -l\n{FENCE}\n", '<h2 id="a">')
        self.assertEqual(problems, ["ru/piv.md: heading #b is missing",
                                    "ru/piv.md: code block 1 differs from the English one"])

    def test_missing_translation_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "br").mkdir()
            self.assertEqual(check_ru.compare(root / "b", root / "br", root / "s", root / "sr", ["piv.md"]),
                             ["ru/piv.md: no translation"])


if __name__ == "__main__":
    unittest.main()

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402

UP = "https://github.com/drduh/YubiKey-Guide"


class TocTests(unittest.TestCase):
    def test_strips_list_up_to_rule(self):
        text = "Intro.\n\n- [A](#a)\n  - [B](#b)\n\n---\n\n# A\n\nBody\n"
        self.assertEqual(build.strip_toc(text), "Intro.\n\n# A\n\nBody\n")

    def test_without_toc_is_unchanged(self):
        self.assertEqual(build.strip_toc("# A\n\nBody\n"), "# A\n\nBody\n")


class LinkTests(unittest.TestCase):
    def test_relative_link_between_sources(self):
        self.assertEqual(build.rewrite_link("recovery/README.md", "README.md"), "recovery.md")
        self.assertEqual(build.rewrite_link("../README.md#x", "piv/README.md"), "guide.md#x")

    def test_upstream_blob_links_to_sources(self):
        for branch in ("main", "master"):
            self.assertEqual(build.rewrite_link(f"{UP}/blob/{branch}/SECENV.md#y", "README.md"), "secenv.md#y")

    def test_upstream_readme_tab(self):
        self.assertEqual(build.rewrite_link(f"{UP}?tab=readme-ov-file#identity", "SECENV.md"), "guide.md#identity")

    def test_relative_link_to_other_repo_file(self):
        self.assertEqual(build.rewrite_link("./yk.cnf", "piv/README.md"),
                         "https://github.com/mezinster/YubiKey-Guide/blob/master/piv/yk.cnf")

    def test_media_stays_relative(self):
        self.assertEqual(build.rewrite_link("media/schema_gpg.png", "README.md"), "media/schema_gpg.png")

    def test_unchanged_links(self):
        for target in ("#anchor", f"{UP}/blob/main/config/gpg.conf", f"{UP}/issues/445", UP,
                       "https://www.yubico.com/genuine/", "mailto:a@example.org"):
            self.assertEqual(build.rewrite_link(target, "README.md"), target)


class AlertTests(unittest.TestCase):
    def test_single_line_alert(self):
        text = "Before\n\n> [!WARNING]\n> Erases the device!\n\nAfter\n"
        self.assertEqual(build.convert_alerts(text),
                         'Before\n\n!!! warning "Warning"\n    Erases the device!\n\nAfter\n')

    def test_multiline_alert(self):
        text = "> [!TIP]\n> Use quotes:\n>\n> `export X='a \"b\"'`\n"
        self.assertEqual(build.convert_alerts(text),
                         '!!! tip "Tip"\n    Use quotes:\n\n    `export X=\'a "b"\'`\n')

    def test_every_alert_type(self):
        for kind, (admonition, title) in build.ALERTS.items():
            out = build.convert_alerts(f"> [!{kind}]\n> x\n")
            self.assertEqual(out, f'!!! {admonition} "{title}"\n    x\n')

    def test_plain_quote_is_untouched(self):
        self.assertEqual(build.convert_alerts("> just a quote\n"), "> just a quote\n")


class AdaptTests(unittest.TestCase):
    def test_code_blocks_are_untouched(self):
        fence = "`" * 3  # spelled out so this file can sit inside a Markdown code block
        text = f"[r](recovery/README.md)\n\n{fence}console\n$ echo '[r](recovery/README.md)'\n> [!NOTE]\n{fence}\n"
        out = build.adapt(text, "SECENV.md")
        self.assertIn("[r](recovery.md)\n", out)
        self.assertIn(f"$ echo '[r](recovery/README.md)'\n> [!NOTE]\n{fence}", out)
        self.assertEqual(out.count("\n"), text.count("\n"))

    def test_guide_gets_title_and_loses_toc(self):
        out = build.adapt("Intro.\n\n- [A](#a)\n\n---\n\n# A\n", "README.md")
        self.assertTrue(out.startswith("---\ntitle: Guide\n---\n\nIntro.\n"))
        self.assertNotIn("- [A](#a)", out)


class BuildTests(unittest.TestCase):
    def test_build_from_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "b"
            build.build(out, None)
            self.assertEqual(sorted(p.name for p in out.glob("*.md")),
                             ["download.md", "guide.md", "index.md", "piv.md", "recovery.md", "secenv.md"])
            self.assertTrue((out / "media" / "schema_gpg.png").is_file())
            guide = (out / "guide.md").read_text(encoding="utf-8")
            self.assertIn("](recovery.md)", guide)
            self.assertNotIn("> [!", guide)
            self.assertNotIn("- [Purchase YubiKey](#purchase-yubikey)", guide)
            self.assertIn("# Purchase YubiKey", guide)

    def test_sources_are_not_modified(self):
        before = (build.ROOT / "README.md").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            build.build(Path(tmp) / "b", None)
        self.assertEqual((build.ROOT / "README.md").read_bytes(), before)


def release(tag, date, pre=False, draft=False, body="", assets=None):
    return {"tag_name": tag, "name": tag, "draft": draft, "prerelease": pre,
            "published_at": f"{date}T10:00:00Z", "html_url": f"https://github.com/x/r/releases/tag/{tag}",
            "body": body, "assets": assets if assets is not None else [
                {"name": "ykrecover-windows-x64.zip", "size": 30165643,
                 "browser_download_url": f"https://github.com/x/r/releases/download/{tag}/ykrecover-windows-x64.zip"}]}


class DownloadTests(unittest.TestCase):
    def test_latest_first_with_zip_and_size(self):
        page = build.render_download([release("v0.1.2-beta", "2026-10-11", pre=True),
                                      release("v0.1.3-beta", "2026-10-12", pre=True)])
        self.assertLess(page.index("## v0.1.3-beta"), page.index("v0.1.2-beta"))
        self.assertIn("## v0.1.3-beta (pre-release)", page)
        self.assertIn("[ykrecover-windows-x64.zip](https://github.com/x/r/releases/download/v0.1.3-beta/"
                      "ykrecover-windows-x64.zip) (30.2 MB)", page)
        self.assertIn("Published 2026-10-12.", page)
        self.assertIn("## Earlier releases", page)
        self.assertIn("| [v0.1.2-beta](https://github.com/x/r/releases/tag/v0.1.2-beta) | 2026-10-11 |", page)

    def test_drafts_are_skipped(self):
        page = build.render_download([release("v9", "2026-12-01", draft=True), release("v1", "2026-10-01")])
        self.assertNotIn("v9", page)
        self.assertIn("## v1\n", page)
        self.assertNotIn("Earlier releases", page)

    def test_notes_headings_are_demoted(self):
        page = build.render_download([release("v1", "2026-10-01", body="# Big\n\nText\n\n## Small")])
        self.assertIn("### Release notes", page)
        self.assertIn("#### Big", page)
        self.assertIn("##### Small", page)
        self.assertNotIn("\n# Big", page)

    def test_no_published_release(self):
        for releases in (None, [], [release("v9", "2026-12-01", draft=True)]):
            page = build.render_download(releases)
            self.assertIn("# Download", page)
            self.assertIn("https://github.com/mezinster/YubiKey-Guide/releases", page)

    def test_build_writes_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "b"
            build.build(out, [release("v1", "2026-10-01")])
            self.assertIn("## v1", (out / "download.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

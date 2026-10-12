"""The backup report (stderr) is identical in both implementations."""

import unittest

from tests.helpers import FIXTURES, LINUX, repeated_key_folder, run_impl, short_tempdir

FOLDERS = ["tests/fixtures/flexible/asc backup", "tests/fixtures/ed25519", "tests/fixtures/ed25519/export",
           "tests/fixtures/split", "tests/fixtures/unprotected", "tests/fixtures/gnupg1", "tests/fixtures/multikey",
           "tests/fixtures/mixed", "tests/fixtures/noauth/export", "tests/fixtures/flexible/", "tests/fixtures"]


def report(stderr: str) -> str:
    lines, inside = [], False
    for line in stderr.splitlines():
        if line.startswith("Backup files in "):
            inside = True
        elif inside and not line.startswith("  "):
            break
        if inside:
            lines.append(line)
    return "\n".join(lines)


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class InventoryParityTests(unittest.TestCase):
    def test_reports_match(self):
        for folder in FOLDERS:
            with self.subTest(folder):
                py = run_impl("python", ["--backup", folder, "--dry-run"])
                sh = run_impl("bash", ["--backup", folder, "--dry-run"])
                self.assertTrue(report(py.stderr), py.stderr)
                self.assertEqual(report(sh.stderr), report(py.stderr))
                self.assertEqual(sh.returncode, py.returncode, sh.stderr)

    def assert_same_run(self, folder):
        py = run_impl("python", ["--backup", str(folder), "--dry-run"])
        sh = run_impl("bash", ["--backup", str(folder), "--dry-run"])
        self.assertEqual((sh.returncode, sh.stdout, sh.stderr), (py.returncode, py.stdout, py.stderr))
        return py

    def test_repeated_key_needs_the_passphrase(self):
        py = self.assert_same_run(repeated_key_folder(self))
        self.assertEqual(py.returncode, 0, py.stderr)
        self.assertIn("<CERTIFY_PASS>", py.stdout)

    def test_only_stubs(self):
        root = short_tempdir(self, "stubs")
        for f in (FIXTURES / "flexible" / "asc backup" / "stub").iterdir():
            (root / f.name).write_bytes(f.read_bytes())
        py = self.assert_same_run(root)
        self.assertIn(f"error: no secret keys in {root}: only card stubs or public keys", py.stderr)

    def test_empty_folder(self):
        root = short_tempdir(self, "empty")
        py = self.assert_same_run(root)
        self.assertIn(f"error: no backup found in {root} (looked for OpenPGP key files and GnuPG homes)", py.stderr)

"""The backup report (stderr) is identical in both implementations."""

import unittest

from tests.helpers import FIXTURES, LINUX, bare_first_folder, bom_crlf_folder, repeated_key_folder, run_impl, short_tempdir

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

    def test_repeated_key_first_copy_wins(self):
        py = self.assert_same_run(repeated_key_folder(self))
        self.assertEqual(py.returncode, 0, py.stderr)
        self.assertNotIn("<CERTIFY_PASS>", py.stdout)

    def test_protected_file_wins_over_an_earlier_bare_file(self):
        py = self.assert_same_run(bare_first_folder(self))
        self.assertEqual(py.returncode, 0, py.stderr)
        self.assertIn("STDIN <CERTIFY_PASS>\nSTDIN <ADMIN_PIN>", py.stdout)
        self.assertRegex(py.stderr, r"a-bare\.gpg: key \w+: .* \(not needed\)")
        self.assertRegex(py.stderr, r"b-protected\.gpg: key \w+: .* \(used\)")

    def test_bom_armor_loads(self):
        py = self.assert_same_run(bom_crlf_folder(self))
        self.assertEqual(py.returncode, 0, py.stderr)
        self.assertIn("  bom.asc: key ", py.stderr)
        self.assertIn("(used)", py.stderr)

    def test_unreadable_files_and_no_candidate(self):
        root = short_tempdir(self, "unr")
        (root / "junk.asc").write_bytes(b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n\nnot base64 at all\n")
        stub = FIXTURES / "flexible" / "asc backup" / "stub" / "flex.priv_stub.asc"
        (root / "stub.asc").write_bytes(stub.read_bytes())
        py = self.assert_same_run(root)
        self.assertEqual(py.returncode, 2)
        self.assertIn(f"error: no usable secret keys in {root} (see the report above)", py.stderr)

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

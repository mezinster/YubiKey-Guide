import unittest
from pathlib import Path

from tests.helpers import FIXTURES, PASSPHRASE, fixture_info, short_tempdir
from ykrecover import EXIT_BACKUP, RecoveryError, backup
from ykrecover.gpg import Home

COLONS_TWO_KEYS = """\
sec:u:255:22:D35238B02045AC9D:1791717022:::u:::cESCA:::+::ed25519:::0:
fpr:::::::::9D1EEF5129527741D5D71C30D35238B02045AC9D:
grp:::::::::1BD940563F0ED08451AB0FA1929B36AD4BE43F7C:
uid:u::::1791717022::230162C5BA27CB835FEFA182F7362D77F77BB1A7::Test <t@example.org>::::::::::0:
ssb:u:255:22:A3C895A8B9C0084C:1791717024:1854789024:::::s:::+::ed25519::
fpr:::::::::289425A50E5E40018F9BBD22A3C895A8B9C0084C:
grp:::::::::EEA8DE772E836F74E1BB4AC797F79DB808D4A3BD:
ssb:u:4096:1:266DF3F59D5DF0EF:1791717025::::::e:::#::::
fpr:::::::::B3B43A994B5B2F01BF225D2A266DF3F59D5DF0EF:
grp:::::::::3E79726AA188E01C2C4436695167BF9F1F9917D0:
sec:u:255:22:1111111111111111:1791717022:::u:::cESCA:::#::ed25519:::0:
fpr:::::::::AAAA1111111111111111111111111111111111111:
"""


class ParseTests(unittest.TestCase):
    def test_parses_keys_and_subkeys(self):
        first, second = backup.parse_secret_keys(COLONS_TWO_KEYS)
        self.assertEqual(first.key_fp, "9D1EEF5129527741D5D71C30D35238B02045AC9D")
        self.assertEqual(first.key_id, "D35238B02045AC9D")
        self.assertEqual(first.uids, ("Test <t@example.org>",))
        self.assertTrue(first.has_certify_secret)
        sign, enc = first.subkeys
        self.assertEqual((sign.usage, sign.algo, sign.expires), ("s", "ed25519", 1854789024))
        self.assertEqual(sign.keygrip, "EEA8DE772E836F74E1BB4AC797F79DB808D4A3BD")
        self.assertEqual((enc.usage, enc.algo, enc.expires), ("e", "rsa4096", 0))
        self.assertEqual((sign.secret, enc.secret), (True, False))
        self.assertFalse(second.has_certify_secret)

    def test_choose_key(self):
        bundles = backup.parse_secret_keys(COLONS_TWO_KEYS)
        with self.assertRaises(backup.AmbiguousKey) as ctx:
            backup.choose_key(bundles)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(len(ctx.exception.bundles), 2)
        self.assertEqual(backup.choose_key(bundles, "02045ac9d").key_id, "D35238B02045AC9D")
        with self.assertRaises(RecoveryError):
            backup.choose_key(bundles, "FFFF")


class DetectTests(unittest.TestCase):
    def test_detects_each_format(self):
        for kind in ("export", "subkeys-only", "gnupghome"):
            with self.subTest(kind):
                found = backup.detect(FIXTURES / "ed25519" / kind)
                self.assertEqual([c.kind for c in found], [kind])

    def test_parent_folder_needs_a_choice(self):
        found = backup.detect(FIXTURES / "ed25519")
        self.assertEqual([c.kind for c in found], ["export", "gnupghome", "subkeys-only"])
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(backup.choose(found, "gnupghome").kind, "gnupghome")

    def test_missing_folder(self):
        with self.assertRaises(RecoveryError) as ctx:
            backup.detect(Path("/nonexistent/backup"))
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)


class HomeRegistryTests(unittest.TestCase):
    def test_exit_hook_removes_homes_left_behind(self):
        from ykrecover import gpg
        kept, done = Home.create("test"), Home.create("test")
        self.addCleanup(kept.destroy)
        done.destroy()
        self.assertIn(kept.path, gpg._LIVE_HOMES)
        self.assertNotIn(done.path, gpg._LIVE_HOMES)
        gpg.destroy_all_homes()
        self.assertFalse(kept.path.exists())
        self.assertNotIn(kept.path, gpg._LIVE_HOMES)


class HomeCreateTests(unittest.TestCase):
    def test_unusable_work_base_is_a_backup_error(self):
        from unittest import mock
        from ykrecover import osdeps
        with mock.patch.object(osdeps, "work_base", side_effect=PermissionError("denied")):
            with self.assertRaises(RecoveryError) as ctx:
                Home.create("staging")
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertIn("denied", str(ctx.exception))

    def test_error_names_the_base_once_computed(self):
        from unittest import mock
        from ykrecover import osdeps
        missing = Path("/nonexistent-ykr-base")
        with mock.patch.object(osdeps, "work_base", side_effect=[missing, Path("/elsewhere")]) as base:
            with self.assertRaises(RecoveryError) as ctx:
                Home.create("staging")
        self.assertEqual(base.call_count, 1)
        self.assertIn(f"in {missing}:", str(ctx.exception))


class PreflightTests(unittest.TestCase):
    def test_preflight_reports_missing_gpg4win(self):
        from unittest import mock
        from ykrecover import EXIT_USAGE, osdeps
        from ykrecover.migrate import Options, Session

        class UI:
            def warn(self, msg): pass

        session = Session(Options(backup=Path("."), dry_run=True), UI())
        with mock.patch.object(osdeps, "available", lambda name: name != "gpg"):
            with self.assertRaises(RecoveryError) as ctx:
                session.preflight()
        self.assertEqual(ctx.exception.code, EXIT_USAGE)
        self.assertIn("gpg", str(ctx.exception))


class UpdateStubsHintTests(unittest.TestCase):
    def test_hint_names_the_platform_command(self):
        from unittest import mock
        from ykrecover import osdeps
        from ykrecover.migrate import update_stubs_command, update_stubs_where
        for platform, expected in (("linux", "python3 -m ykrecover --cli update-stubs"),
                                   ("win32", "ykrecover.exe --cli update-stubs")):
            with mock.patch.object(osdeps.sys, "platform", platform):
                self.assertEqual(update_stubs_command(), expected)
        with mock.patch.object(osdeps.sys, "platform", "linux"):
            self.assertIn("recovery folder", update_stubs_where())
            self.assertIn("yubikey-recovery --cli update-stubs", update_stubs_where())
        with mock.patch.object(osdeps.sys, "platform", "win32"):
            self.assertEqual(update_stubs_where(), "")


class PreflightWarningTests(unittest.TestCase):
    """Non-dry-run preflight warnings, with the tools and the environment faked."""

    def preflight(self, workdir=None, tools=None):
        import tempfile
        from unittest import mock
        from ykrecover import migrate, osdeps
        from ykrecover.migrate import Options, Session

        base = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(base, ignore_errors=True))
        self.addCleanup(osdeps.set_workdir, None)
        osdeps.set_workdir(workdir or base)
        warnings = []

        class UI:
            def warn(self, msg): warnings.append(msg)

        patches = [mock.patch.object(osdeps, "available", lambda name: True),
                   mock.patch.object(migrate, "gpg_version", lambda: (2, 4)),
                   mock.patch.object(osdeps, "network_up", lambda: []),
                   mock.patch.object(osdeps, "memory_backed", lambda path: True)]
        if tools is not None:
            patches += [mock.patch.object(osdeps.sys, "platform", "win32"),
                        mock.patch.object(osdeps, "tool", lambda name: [tools[name]])]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        return base, warnings, lambda: Session(Options(backup=Path(".")), UI()).preflight()

    def test_old_leftover_folders_are_reported(self):
        import os
        import time
        base, warnings, preflight = self.preflight()
        old, fresh = base / "ykr-staging-x", base / "ykr-card-y"
        old.mkdir()
        fresh.mkdir()
        (base / "ykt-other").mkdir()
        hour_ago = time.time() - 3600
        os.utime(old, (hour_ago, hour_ago))
        preflight()
        self.assertEqual(warnings, [f"left over from an earlier run: {old}; it may hold key material — "
                                    "delete it securely"])

    def test_disk_warning_names_a_chosen_work_folder(self):
        from unittest import mock
        from ykrecover import osdeps
        from ykrecover.migrate import Options, Session
        base, warnings, preflight = self.preflight()

        class UI:
            def warn(self, msg): warnings.append(msg)

        with mock.patch.object(osdeps, "memory_backed", lambda path: False):
            Session(Options(backup=Path("."), workdir=base), UI()).preflight()
            preflight()  # no --workdir: the base is the default one
        self.assertEqual(warnings, [f"work folder {base} is not a RAM disk; make sure it is an encrypted volume",
                                    f"temporary key copies are on disk at {base}; "
                                    "use --workdir with a RAM disk or encrypted volume to avoid that"])

    def test_mixed_gnupg_installs_are_reported_on_windows(self):
        one, two = str(Path("/gpg4win/bin")), str(Path("/other/bin"))
        tools = {"gpg": str(Path(one) / "gpg.exe"), "gpgconf": str(Path(one) / "gpgconf.exe"),
                 "gpg-connect-agent": str(Path(two) / "gpg-connect-agent.exe")}
        _, warnings, preflight = self.preflight(tools=tools)
        preflight()
        self.assertEqual(warnings, [f"GnuPG tools come from different folders (gpg in {one}, gpgconf in {one}, "
                                    f"gpg-connect-agent in {two}); use the ones from a single Gpg4win installation"])

    def test_one_gnupg_install_is_not_reported(self):
        folder = str(Path("/gpg4win/bin"))
        tools = {name: str(Path(folder) / f"{name}.exe") for name in ("gpg", "gpgconf", "gpg-connect-agent")}
        _, warnings, preflight = self.preflight(tools=tools)
        preflight()
        self.assertEqual(warnings, [])

    def test_fresh_folders_are_not_reported(self):
        base, warnings, preflight = self.preflight()
        (base / "ykr-staging-x").mkdir()
        preflight()
        self.assertEqual(warnings, [])


class CleanupWarningTests(unittest.TestCase):
    def test_cleanup_warns_when_the_staging_home_survives(self):
        from unittest import mock
        from ykrecover import gpg, osdeps
        from ykrecover.migrate import Options, Session

        warnings = []

        class UI:
            def warn(self, msg): warnings.append(msg)

        session = Session(Options(backup=Path("."), dry_run=True), UI())
        session.staging = Home.create("test")
        path = session.staging.path
        self.addCleanup(lambda: (gpg._LIVE_HOMES.discard(path), Home(path).destroy()))
        with mock.patch.object(osdeps, "secure_delete", lambda p: False):
            session.cleanup()
        self.assertEqual(warnings, [f"could not remove the temporary folder {path}; delete it manually"])
        self.assertIn(path, gpg._LIVE_HOMES)
        self.assertIsNone(session.staging)

    def test_write_card_warns_when_card_homes_survive(self):
        from unittest import mock
        from ykrecover import gpg, osdeps
        from ykrecover.migrate import CardResult, Options, Session

        warnings = []

        class UI:
            def warn(self, msg): warnings.append(msg)

        session = Session(Options(backup=Path("."), dry_run=True), UI())
        made = []
        real_create = Home.create

        def create(label):
            made.append(real_create(label))
            return made[-1]

        def cleanup():
            for h in made:
                gpg._LIVE_HOMES.discard(h.path)
                Home(h.path).destroy()
        self.addCleanup(cleanup)
        with mock.patch.object(Home, "create", staticmethod(create)), \
                mock.patch.object(Session, "card_plan", lambda *a: []), \
                mock.patch.object(Session, "_tokens", lambda *a: {}), \
                mock.patch.object(osdeps, "secure_delete", lambda p: False):
            session.staging = Home(Path(self.enterContext(__import__("tempfile").TemporaryDirectory())))
            session.logins = {1: "x"}
            session._write_card(CardResult(index=1, serial="1"))
        self.assertTrue(made)
        for h in made:
            self.assertIn(f"could not remove the temporary folder {h.path}; delete it manually", warnings)


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.home = Home.create("test")
        self.addCleanup(self.home.destroy)

    def load(self, profile: str, kind: str):
        candidate = backup.choose(backup.detect(FIXTURES / profile / kind))
        return backup.choose_key(backup.load(candidate, self.home))

    def test_export_matches_fixture(self):
        info = fixture_info("ed25519")
        bundle = self.load("ed25519", "export")
        self.assertEqual((bundle.key_fp, bundle.key_id), (info["KEY_FP"], info["KEY_ID"]))
        self.assertTrue(bundle.has_certify_secret)
        self.assertEqual({k.usage: k.fpr for k in bundle.subkeys},
                         {"s": info["FPR_S"], "e": info["FPR_E"], "a": info["FPR_A"]})
        self.assertEqual({k.algo for k in bundle.subkeys}, {"ed25519", "cv25519"})

    def test_rsa_algorithm_names(self):
        self.assertEqual({k.algo for k in self.load("rsa4096", "export").subkeys}, {"rsa4096"})

    def test_subkeys_only_has_no_certify_secret(self):
        self.assertFalse(self.load("ed25519", "subkeys-only").has_certify_secret)

    def test_gnupghome_copy(self):
        self.assertEqual(self.load("ed25519", "gnupghome").key_fp, fixture_info("ed25519")["KEY_FP"])

    def test_passphrase_check(self):
        bundle = self.load("ed25519", "export")
        self.assertFalse(backup.check_passphrase(self.home, bundle, "wrong"))
        self.assertTrue(backup.check_passphrase(self.home, bundle, PASSPHRASE))

    def test_source_is_not_modified(self):
        source = FIXTURES / "ed25519" / "gnupghome"
        before = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in source.rglob("*")}
        self.load("ed25519", "gnupghome")
        after = {p: (p.stat().st_mtime_ns, p.stat().st_size) for p in source.rglob("*")}
        self.assertEqual(before, after)


LIST_PACKETS = """\
# off=0 ctb=95 tag=5 hlen=3 plen=966
:secret key packet:
\tversion 4, algo 1, created 1600000000, expires 0
\tpkey[0]: [2048 bits]
\tpkey[1]: [17 bits]
\titer+salt S2K, algo: 7, SHA1 protection, hash: 2, salt: 0011223344556677
\tprotect count: 41943040 (244)
\tprotect IV:  00 01 02 03 04 05 06 07 08 09 0a 0b 0c 0d 0e 0f
\tskey[2]: [v4 protected]
\tkeyid: 0123456789ABCDEF
:user ID packet: "Test <t@example.org>"
:signature packet: algo 1, keyid 0123456789ABCDEF
:secret sub key packet:
\tversion 4, algo 1, created 1600000000, expires 0
\tgnu-dummy, algo: 0, simple checksum, hash: 0
\tprotect IV: 
\tkeyid: AAAA1111BBBB2222
:secret sub key packet:
\tversion 4, algo 1, created 1600000400, expires 0
\tgnu-divert-to-card, algo: 0, simple checksum, hash: 0
\tkeyid: CCCC3333DDDD4444
:secret sub key packet:
\tversion 4, algo 22, created 1600000400, expires 0
\tskey[3]: [255 bits]
\tkeyid: EEEE5555FFFF6666
:public sub key packet:
\tversion 4, algo 1, created 1600000400, expires 0
\tkeyid: 1111222233334444
"""


class PacketParseTests(unittest.TestCase):
    def test_states(self):
        parts = backup.parse_packets(LIST_PACKETS)
        self.assertEqual([(p.main_keyid, p.keyid, p.role, p.state) for p in parts], [
            ("0123456789ABCDEF", "0123456789ABCDEF", "main", "secret"),
            ("0123456789ABCDEF", "AAAA1111BBBB2222", "sub", "missing"),
            ("0123456789ABCDEF", "CCCC3333DDDD4444", "sub", "card-stub"),
            ("0123456789ABCDEF", "EEEE5555FFFF6666", "sub", "secret-unprotected"),
            ("0123456789ABCDEF", "1111222233334444", "sub", "public"),
        ])

    def test_no_key_packets(self):
        self.assertEqual(backup.parse_packets(":signature packet: algo 1, keyid 0123456789ABCDEF\n"), [])


class SniffTests(unittest.TestCase):
    def write(self, name, data: bytes):
        path = short_tempdir(self, "sniff") / name
        path.write_bytes(data)
        return path

    def test_armored_private_and_public(self):
        self.assertTrue(backup.looks_like_key_file(self.write("a.asc", b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n")))
        self.assertTrue(backup.looks_like_key_file(self.write("b.txt", b"-----BEGIN PGP PUBLIC KEY BLOCK-----\n")))

    def test_bom_and_crlf_armor_is_a_key_file(self):
        data = b"\xef\xbb\xbf\r\n  -----BEGIN PGP PRIVATE KEY BLOCK-----\r\n\r\n"
        self.assertTrue(backup.looks_like_key_file(self.write("windows.asc", data)))

    def test_binary_packets(self):
        self.assertTrue(backup.looks_like_key_file(self.write("new.gpg", bytes([0xC5, 0x10]) + b"\x04" * 16)))
        self.assertTrue(backup.looks_like_key_file(self.write("old.gpg", bytes([0x95, 0x01, 0x00]))))
        self.assertTrue(backup.looks_like_key_file(self.write("pub.gpg", bytes([0x99, 0x01, 0x00]))))

    def test_other_files(self):
        self.assertFalse(backup.looks_like_key_file(self.write("sig.asc", b"-----BEGIN PGP SIGNATURE-----\n")))
        self.assertFalse(backup.looks_like_key_file(self.write("rev.rev", b"This is a revocation certificate\n")))
        self.assertFalse(backup.looks_like_key_file(self.write("kbx", b"\x00\x00\x00\x20\x01")))
        self.assertFalse(backup.looks_like_key_file(self.write("big.asc", b"-----BEGIN PGP PRIVATE KEY BLOCK-----"
                                                                           + b"x" * (1 << 20))))


class WalkTests(unittest.TestCase):
    def test_walk_finds_nested_files_and_skips_hidden_and_deep(self):
        root = short_tempdir(self, "walk")
        key = b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
        for rel in ("top.asc", "a/one.asc", "a/b/c/three.asc", "a/b/c/d/four.asc", ".hidden/x.asc", "a/notes.txt"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(key if rel.endswith(".asc") else b"notes")
        homes, files = backup.walk(root)
        self.assertEqual(homes, [])
        self.assertEqual([p.relative_to(root).as_posix() for p in files], ["a/b/c/three.asc", "a/one.asc", "top.asc"])

    def test_walk_skips_random_seed(self):
        root = short_tempdir(self, "seed")
        (root / "random_seed").write_bytes(bytes([0x95, 0x01, 0x00]) + b"\x00" * 597)  # passes the tag sniff
        (root / "key.asc").write_bytes(b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n")
        self.assertEqual(backup.walk(root), ([], [root / "key.asc"]))

    def test_gnupg2_home_is_a_candidate_and_its_rings_are_not_key_files(self):
        homes, files = backup.walk(FIXTURES / "ed25519" / "gnupghome")
        self.assertEqual([h.kind for h in homes], ["gnupghome"])
        self.assertEqual(files, [])

    def test_gnupg1_home_contributes_its_rings_as_key_files(self):
        homes, files = backup.walk(FIXTURES / "gnupg1")
        self.assertEqual(homes, [])
        self.assertEqual([p.name for p in files], ["pubring.gpg", "secring.gpg"])


if __name__ == "__main__":
    unittest.main()

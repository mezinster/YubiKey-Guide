import os
import re
import subprocess
import unittest
from unittest import mock
from pathlib import Path

from tests.helpers import (FIXTURES, LINUX, MALLORY, PASSPHRASE, RECOVERY, REAL_GPG, REVOKED_UID,
                           bare_first_folder, bom_crlf_folder, fixture_info, import_unprotected, loose_subkey_folder,
                           partial_import_folder, repeated_key_folder, revoked_uid_folder, short_tempdir)
from ykrecover import EXIT_BACKUP, RecoveryError, backup, osdeps
from ykrecover.gpg import Home, gpg_version

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
    def test_main_key_expiry_and_algo(self):
        colons = COLONS_TWO_KEYS.replace("sec:u:255:22:D35238B02045AC9D:1791717022:::",
                                         "sec:e:255:22:D35238B02045AC9D:1791717022:1791800000::", 1)
        first, second = backup.parse_secret_keys(colons)
        self.assertEqual((first.expires, first.algo), (1791800000, "ed25519"))
        self.assertEqual(second.expires, 0)

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
        self.assertEqual([c.kind for c in found], ["export", "gnupghome"])
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertEqual(backup.choose(found, "gnupghome").kind, "gnupghome")

    def test_missing_folder(self):
        with self.assertRaises(RecoveryError) as ctx:
            backup.detect(Path("/nonexistent/backup"))
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)


class InventoryTests(unittest.TestCase):
    def test_flexible_layout(self):
        root = FIXTURES / "flexible" / "asc backup"
        inv = backup.inventory(root)
        kid = fixture_info("flexible")["KEY_ID"]
        self.assertEqual([(c.kind, c.key_id, [f.name for f in c.files]) for c in inv.candidates],
                         [("export", kid, ["flex.priv.asc"])])
        self.assertEqual(backup.report(inv), "\n".join([
            f"Backup files in {root}:",
            f"  private/flex.priv.asc: key {kid}: main secret, 2 subkeys secret (used)",
            f"  pub/flex.pub.asc: key {kid}: main public, 2 subkeys public (ignored)",
            f"  stub/flex.priv_stub.asc: key {kid}: main card stub, 2 subkeys card stub (ignored)",
            f"  stub/flex.sub_priv_stub.asc: key {kid}: main missing, 2 subkeys card stub (ignored)",
            f"  subkeys/flex.sub_priv.asc: key {kid}: main missing, 2 subkeys secret (not needed)",
        ]))

    def test_guide_export_folder_is_unchanged(self):
        inv = backup.inventory(FIXTURES / "ed25519" / "export")
        (c,) = inv.candidates
        self.assertEqual((c.kind, [f.name for f in c.files]), ("export", [f"secret-{fixture_info('ed25519')['KEY_ID']}-Certify.key"]))
        self.assertEqual(c.path, FIXTURES / "ed25519" / "export")

    def test_same_key_in_two_folders_is_one_candidate(self):
        found = backup.detect(FIXTURES / "ed25519")
        self.assertEqual([c.kind for c in found], ["export", "gnupghome"])

    def test_split_files_use_both(self):
        (c,) = backup.detect(FIXTURES / "split")
        self.assertEqual((c.kind, [f.name for f in c.files]), ("export", ["subs-only.asc", "main-only.asc"]))

    def test_unprotected_candidate(self):
        (c,) = backup.detect(FIXTURES / "unprotected")
        self.assertTrue(c.unprotected)
        self.assertEqual(len(c.bare), 4)
        self.assertEqual(backup.detect(FIXTURES / "split")[0].bare, ())
        self.assertFalse(backup.detect(FIXTURES / "split")[0].unprotected)

    def test_mixed_candidate_lists_its_bare_parts(self):
        # A protected main key beside subkeys exported with no passphrase: built from the unprotected fixture.
        info, home, root = fixture_info("unprotected"), short_tempdir(self, "mx"), short_tempdir(self, "mixed")
        env = {**os.environ, "GNUPGHOME": str(home)}

        def gpg(*args, stdin=""):
            subprocess.run([REAL_GPG, "--batch", "--pinentry-mode", "loopback", *args], input=stdin, text=True,
                           env=env, check=True, capture_output=True)

        import_unprotected(home)
        gpg("--armor", "--output", str(root / "subs.asc"), "--export-secret-subkeys", info["KEY_FP"])
        gpg("--passphrase-fd", "0", "--passwd", info["KEY_FP"], stdin=PASSPHRASE + "\n")
        gpg("--passphrase-fd", "0", "--armor", "--output", str(root / "main.asc"), "--export-secret-keys",
            info["KEY_FP"] + "!", stdin=PASSPHRASE + "\n")
        (c,) = backup.detect(root)
        self.assertEqual((c.kind, sorted(f.name for f in c.files)), ("export", ["main.asc", "subs.asc"]))
        self.assertFalse(c.unprotected)
        self.assertEqual(sorted(c.bare), sorted(info[k][-16:] for k in ("FPR_S", "FPR_E", "FPR_A")))

    def assert_staging_protected(self, candidate, protected):
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        (bundle,) = backup.load(candidate, staging, self.fail)
        self.assertEqual(backup.check_passphrase(staging, bundle, "not-the-passphrase"), not protected)

    def test_repeated_key_first_copy_wins(self):
        # one file holding the same key with no passphrase and then protected: gpg keeps the first copy
        root = repeated_key_folder(self)
        (c,) = backup.detect(root)
        info = fixture_info("unprotected")
        self.assertEqual(([f.name for f in c.files], c.unprotected), (["both.gpg"], True))
        self.assertEqual(sorted(c.bare), sorted(info[k][-16:] for k in ("KEY_FP", "FPR_S", "FPR_E", "FPR_A")))
        self.assert_staging_protected(c, False)

    def test_protected_copy_wins_over_an_earlier_bare_file(self):
        root = bare_first_folder(self)
        inv = backup.inventory(root)
        (c,) = inv.candidates
        self.assertEqual(([f.name for f in c.files], c.unprotected, c.bare), (["b-protected.gpg"], False, ()))
        self.assertEqual([(f.path.name, f.status) for f in inv.files],
                         [("a-bare.gpg", "not needed"), ("b-protected.gpg", "used")])
        self.assert_staging_protected(c, True)

    def test_bom_armor_is_read_from_a_stripped_copy(self):
        root = bom_crlf_folder(self)
        original = (root / "bom.asc").read_bytes()
        inv = backup.inventory(root)
        (c,) = inv.candidates
        self.assertEqual([f.name for f in c.files], ["bom.asc"])
        self.assertEqual(backup.report(inv).splitlines()[1],
                         f"  bom.asc: key {fixture_info('unprotected')['KEY_ID']}: main secret (no passphrase), "
                         "3 subkeys secret (no passphrase) (used)")
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        (bundle,) = backup.load(c, staging, self.fail)
        self.assertTrue(bundle.has_certify_secret)
        self.assertEqual((root / "bom.asc").read_bytes(), original)
        copies = [p for p in staging.path.iterdir() if p.is_file() and p.read_bytes() == original[3:]]
        self.assertEqual(len(copies), 1)
        if os.name != "nt":
            self.assertEqual(copies[0].stat().st_mode & 0o777, 0o600)

    def test_gnupg1_home(self):
        (c,) = backup.detect(FIXTURES / "gnupg1")
        self.assertEqual((c.kind, [f.name for f in c.files]), ("export", ["secring.gpg"]))

    def test_multi_key_file_gives_one_candidate_per_key(self):
        info = fixture_info("multikey")
        found = backup.detect(FIXTURES / "multikey")
        self.assertEqual(sorted(c.key_id for c in found), sorted([info["KEY_FP"][-16:], info["KEY2_FP"][-16:]]))
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found)
        self.assertIn("pass --source or --key", str(ctx.exception))
        self.assertEqual(backup.choose(found, key=info["KEY2_FP"]).key_id, info["KEY2_FP"][-16:])

    def test_only_stubs_is_unrecoverable(self):
        root = short_tempdir(self, "stubs")
        stub = FIXTURES / "flexible" / "asc backup" / "stub" / "flex.priv_stub.asc"
        (root / "stub.asc").write_bytes(stub.read_bytes())
        inv = backup.inventory(root)
        self.assertEqual(inv.candidates, ())
        self.assertIn("not recoverable from this folder (card stubs / public key only)", backup.report(inv))

    def test_unreadable_key_looking_file_is_reported(self):
        root = short_tempdir(self, "junk")
        (root / "junk.asc").write_bytes(b"-----BEGIN PGP PRIVATE KEY BLOCK-----\n\nnot base64 at all\n")
        (root / "key.asc").write_bytes((FIXTURES / "unprotected" / "key.asc").read_bytes())
        inv = backup.inventory(root)
        self.assertEqual(len(inv.candidates), 1)
        self.assertIn("  junk.asc: not a readable key file (ignored)", backup.report(inv))

    def test_no_match_lists_what_was_found(self):
        info = fixture_info("multikey")
        found = backup.detect(FIXTURES / "multikey")
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose(found, key="DEADBEEFDEADBEEF")
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        text = str(ctx.exception)
        self.assertTrue(text.startswith("no backup matches --source or --key; found:\n"), text)
        for fp in (info["KEY_FP"], info["KEY2_FP"]):
            self.assertIn(f"  export: {FIXTURES / 'multikey'} (key {fp[-16:]})", text)

    def test_dash_relative_path_is_not_an_option(self):
        import os
        root = short_tempdir(self, "dash")
        (root / "-dash").mkdir()
        (root / "-dash" / "key.asc").write_bytes((FIXTURES / "unprotected" / "key.asc").read_bytes())
        cwd = os.getcwd()
        os.chdir(root)
        self.addCleanup(os.chdir, cwd)
        (c,) = backup.detect(Path("-dash"))
        self.assertEqual([str(f) for f in c.files], [os.path.join("-dash", "key.asc")])
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        (bundle,) = backup.load(c, staging, self.fail)
        self.assertTrue(bundle.has_certify_secret)

    def test_loads_split_files_into_one_key(self):
        (c,) = backup.detect(FIXTURES / "split")
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        (bundle,) = backup.load(c, staging, self.fail)
        self.assertTrue(bundle.has_certify_secret)
        self.assertTrue(all(k.secret for k in bundle.subkeys))


def unsigned_warning(uid, fpr):
    return f'user ID "{uid}" of key {fpr} has no valid self-signature; it will not be published with the key'


class PartialImportTests(unittest.TestCase):
    """gpg exits non-zero when it skips part of a backup file; the import is judged by the secret keys it read."""

    def load(self, shape, key=None):
        root = partial_import_folder(self, shape)
        candidate = backup.choose(backup.detect(root), key=key)
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        warnings = []
        bundles = backup.load(candidate, staging, warn=warnings.append)
        return root / "partial.gpg", bundles, warnings

    def real_import(self, path):
        """What gpg itself does with the file: (exit code, secret key ids it imported)."""
        home = short_tempdir(self, "ri")
        env = {**os.environ, "GNUPGHOME": str(home)}
        done = subprocess.run([REAL_GPG, "--batch", "--import", str(path)], env=env, capture_output=True)
        listing = subprocess.run([REAL_GPG, "--batch", "--with-colons", "-K"], env=env, capture_output=True,
                                 text=True).stdout
        return done.returncode, [line.split(":")[4] for line in listing.splitlines() if line.startswith("sec:")]

    def test_file_gpg_imports_in_part_loads_with_a_warning(self):
        ed, bad = fixture_info("ed25519")["KEY_ID"], fixture_info("unprotected")["KEY_ID"]
        path, bundles, warnings = self.load("two-keys", key=ed)
        self.assertEqual(self.real_import(path), (2, [ed]))
        (bundle,) = bundles
        self.assertEqual(bundle.key_id, ed)
        self.assertTrue(bundle.has_certify_secret)
        self.assertTrue(all(k.secret for k in bundle.subkeys))
        self.assertEqual(len(warnings), 1, warnings)
        self.assertTrue(warnings[0].startswith(f"gpg skipped part of {path}: key {bad}: "), warnings)

    def load_whole_key(self, shape):
        info = fixture_info("ed25519")
        path, bundles, warnings = self.load(shape)
        (bundle,) = bundles
        self.assertEqual(bundle.key_fp, info["KEY_FP"])
        self.assertTrue(bundle.has_certify_secret)
        self.assertTrue(bundle.subkeys and all(k.secret for k in bundle.subkeys))
        self.assertEqual(bundle.uids, ("Recovery Test (ed25519) <test@example.org>",))
        backup.check_user_ids(bundle)
        self.assertEqual(bundle.unsigned_uids, ())
        self.assertEqual(warnings, [])
        return path

    def test_secret_block_without_user_id_self_signature(self):
        # the reported Gpg4win case: plain GnuPG 2.5 imports the main key's secret but not the subkeys' and exits
        # non-zero; GnuPG 2.4 stops at that block. With --allow-non-selfsigned-uid both take the whole key, and the
        # public block that follows supplies the user ID's self-signature.
        path = self.load_whole_key("user")
        code, imported = self.real_import(path)
        self.assertNotEqual(code, 0)

    def test_public_block_without_user_id_self_signature(self):
        self.load_whole_key("bad-public")

    def test_key_with_no_self_signed_user_id_is_refused(self):
        info = fixture_info("ed25519")
        path, bundles, warnings = self.load("unsigned")
        self.assertEqual(self.real_import(path), (2, []))
        (bundle,) = bundles
        self.assertEqual(warnings, [])
        with self.assertRaises(RecoveryError) as ctx:
            backup.check_user_ids(bundle)
        self.assertEqual((ctx.exception.code, str(ctx.exception)),
                         (EXIT_BACKUP, f"key {info['KEY_FP']} has no user ID with a valid self-signature in the "
                                       "backup; re-export the key from a keyring that holds it"))

    def test_unsigned_extra_user_id_is_reported(self):
        info = fixture_info("ed25519")
        _, bundles, warnings = self.load("forged")
        (bundle,) = bundles
        self.assertEqual(warnings, [])
        self.assertEqual(bundle.uids, ("Recovery Test (ed25519) <test@example.org>", MALLORY))
        self.assertEqual(bundle.unsigned_uids, (MALLORY,))
        backup.check_user_ids(bundle)
        self.assertEqual(backup.unsigned_uid_warnings(bundle), [unsigned_warning(MALLORY, info["KEY_FP"])])

    def test_revoked_or_expired_user_id_is_not_unsigned(self):
        colons = ("sec:-:255:22:D35238B02045AC9D:1791717022:::-:::cESCA:::+::ed25519:::0:\n"
                  "fpr:::::::::9D1EEF5129527741D5D71C30D35238B02045AC9D:\n"
                  "uid:-::::1791717022::230162C5BA27CB835FEFA182F7362D77F77BB1A7::Good <g@example.org>::::::::::0:\n"
                  "uid:r::::::330162C5BA27CB835FEFA182F7362D77F77BB1A7::Revoked <r@example.org>::::::::::0:\n"
                  "uid:e::::::430162C5BA27CB835FEFA182F7362D77F77BB1A7::Expired <e@example.org>::::::::::0:\n"
                  "uid:-::::::530162C5BA27CB835FEFA182F7362D77F77BB1A7::Unsigned <u@example.org>::::::::::0:\n")
        (bundle,) = backup.parse_secret_keys(colons)
        self.assertEqual(len(bundle.uids), 4)
        self.assertEqual(bundle.unsigned_uids, ("Unsigned <u@example.org>",))

    def test_revoked_user_id_is_not_reported(self):
        candidate = backup.choose(backup.detect(revoked_uid_folder(self)))
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        (bundle,) = backup.load(candidate, staging, self.fail)
        self.assertIn(REVOKED_UID, bundle.uids)
        self.assertEqual(backup.unsigned_uid_warnings(bundle), [])

    def test_rejected_secret_block_is_an_import_error(self):
        root = partial_import_folder(self, "rejected")
        self.assertEqual(self.real_import(root / "partial.gpg"), (2, []))
        candidate = backup.choose(backup.detect(root))
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        with self.assertRaises(RecoveryError) as ctx:
            backup.load(candidate, staging, self.fail)
        self.assertEqual(ctx.exception.code, EXIT_BACKUP)
        self.assertTrue(str(ctx.exception).startswith(f"cannot import {root / 'partial.gpg'}: gpg: "),
                        str(ctx.exception))

    def test_the_skipped_key_is_not_loaded(self):
        bad = fixture_info("unprotected")["KEY_ID"]
        _, bundles, warnings = self.load("two-keys", key=bad)
        self.assertEqual(len(warnings), 1)
        with self.assertRaises(RecoveryError) as ctx:
            backup.choose_key(bundles, bad)
        self.assertEqual((ctx.exception.code, str(ctx.exception)), (EXIT_BACKUP, f"no key matches --key {bad}"))

    def test_skipped_line_is_the_first_problem_gpg_names(self):
        stderr = ("gpg: keybox '/x/pubring.kbx' created\n"
                  "gpg: key AAAA1111BBBB2222: public key \"A <a@example.org>\" imported\n"
                  "gpg: key AAAA1111BBBB2222: secret key imported\n"
                  "gpg: WARNING: something general\n"
                  "gpg: key CCCC3333DDDD4444: no valid user IDs\n"
                  "gpg: this may be caused by a missing self-signature\n"
                  "gpg: Total number processed: 2\n")
        self.assertEqual(backup.skipped_line(stderr, 2), "key CCCC3333DDDD4444: no valid user IDs")
        self.assertEqual(backup.skipped_line(stderr.replace("key CCCC", "kex CCCC"), 2), "WARNING: something general")
        self.assertEqual(backup.skipped_line("gpg: Total number processed: 1\n", 2), "gpg exited with status 2")
        accepted = 'gpg: key CCCC3333DDDD4444: accepted non self-signed user ID "C <c@example.org>"\n'
        self.assertEqual(backup.skipped_line(accepted + stderr, 2), "key CCCC3333DDDD4444: no valid user IDs")


class LooseSubkeyTests(unittest.TestCase):
    """A subkey's secret saved as a key of its own (no user ID): gpg refuses to import it alone ("no user ID")."""

    def setUp(self):
        self.info = fixture_info("rsa4096")
        self.loose = f"sk_{self.info['FPR_E'][-16:]}.gpg"

    def load(self, root):
        candidate = backup.choose(backup.detect(root))
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        (bundle,) = backup.load(candidate, staging, self.fail)
        return candidate, bundle

    def test_inventory_files_it_under_its_key(self):
        root = loose_subkey_folder(self)
        inv = backup.inventory(root)
        (candidate,) = inv.candidates
        self.assertEqual((candidate.kind, candidate.key_id), ("subkeys-only", self.info["KEY_ID"]))
        self.assertEqual(candidate.files, (root / "public.asc", root / self.loose))
        self.assertEqual(candidate.attached, (root / self.loose,))
        self.assertEqual(backup.report(inv), "\n".join([
            f"Backup files in {root}:",
            f"  public.asc: key {self.info['KEY_ID']}: main public, 3 subkeys public (used)",
            f"  {self.loose}: key {self.info['KEY_ID']}: 1 subkey secret, saved without its key (used)"]))

    def test_loads_as_the_subkey_of_its_key(self):
        for armored in (False, True):
            with self.subTest(armored=armored):
                _, bundle = self.load(loose_subkey_folder(self, armored=armored))
                self.assertEqual(bundle.key_fp, self.info["KEY_FP"])
                self.assertFalse(bundle.has_certify_secret)
                self.assertEqual({k.fpr: k.secret for k in bundle.subkeys},
                                 {self.info["FPR_S"]: False, self.info["FPR_E"]: True, self.info["FPR_A"]: False})
                self.assertEqual(bundle.uids, ("Recovery Test (rsa4096) <test@example.org>",))

    def test_passphrase_unlocks_the_subkey(self):
        candidate, bundle = self.load(loose_subkey_folder(self))
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        backup.load(candidate, staging, self.fail)
        self.assertFalse(backup.check_passphrase(staging, bundle, "wrong"))
        self.assertTrue(backup.check_passphrase(staging, bundle, PASSPHRASE))

    def test_without_its_key_the_error_says_what_is_missing(self):
        root = loose_subkey_folder(self, public=False)
        candidate = backup.choose(backup.detect(root))
        staging = Home.create("t")
        self.addCleanup(staging.destroy)
        with self.assertRaises(RecoveryError) as ctx:
            backup.load(candidate, staging, self.fail)
        self.assertEqual((ctx.exception.code, str(ctx.exception)), (EXIT_BACKUP, backup.LOOSE_KEY.format(
            root / self.loose, self.info["FPR_E"][-16:])))


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
        return backup.choose_key(backup.load(candidate, self.home, self.fail))

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


class EcdhRepairTests(unittest.TestCase):
    """GnuPG 2.5 misfiles imported ECDH subkeys that have no passphrase (see backup.GNUPG25_DROPPED). Real imports
    show it only on GnuPG 2.5; the misfiled state is also made by hand, so the tests mean something on 2.4 too."""

    bash = False

    def setUp(self):
        self.home = Home.create("test")
        self.addCleanup(self.home.destroy)

    def repair_error(self, bare=(), gnupg_25=None):
        """Runs the repair; returns its error, or None. gnupg_25 overrides the GnuPG 2.5 test."""
        version = mock.patch.object(backup, "gpg_version", return_value=(2, 5) if gnupg_25 else (2, 4))
        try:
            if gnupg_25 is None:
                backup.repair_ecdh_imports(self.home, bare)
            else:
                with version:
                    backup.repair_ecdh_imports(self.home, bare)
        except RecoveryError as err:
            self.assertEqual(err.code, EXIT_BACKUP)
            return str(err)
        return None

    def import_raw(self, folder: Path):
        """Imports the backup's key files as load does, without the repair."""
        candidate = backup.choose(backup.detect(folder))
        for path in candidate.files:
            result = self.home.run(["gpg", "--batch", "--import", "--", str(path)])
            self.assertEqual(result.returncode, 0, result.stderr)
        return candidate

    def listing(self) -> str:
        return self.home.run([*backup.LIST_SECRET[:-1], "--with-key-data", backup.LIST_SECRET[-1]]).stdout

    def grips(self) -> set:
        return {line.split(":")[9] for line in self.listing().splitlines() if line.startswith("grp:")}

    def key_files(self) -> set:
        return {p.stem for p in (self.home.path / "private-keys-v1.d").glob("*.key")}

    def decrypt_failures(self) -> list:
        """Encrypt subkeys a message to them alone does not decrypt with."""
        bindings = backup._bindings(self.home)
        return [s.fpr for s in backup.ecdh_subkeys(self.listing()) if not backup.decrypts(self.home, s, bindings)]

    def ecdh_key(self, curves, made=(), expire="never") -> Path:
        """A folder with one key, made with no passphrase (now, or at the --faked-system-time made): an ed25519 main
        key and an encrypt subkey per curve, in that order."""
        home, root = short_tempdir(self, "ek"), short_tempdir(self, "ekf")
        env = {**os.environ, "GNUPGHOME": str(home)}

        def gpg(*args):
            return subprocess.run([REAL_GPG, "--batch", *made, "--pinentry-mode", "loopback", "--passphrase", "",
                                   *args], env=env, check=True, capture_output=True).stdout

        gpg("--quick-gen-key", "Recovery Test (ecdh) <test@example.org>", "ed25519", "cert", "never")
        fpr = next(line.split(b":")[9] for line in gpg("--with-colons", "--list-keys").splitlines()
                   if line.startswith(b"fpr:")).decode()
        for curve in curves:
            gpg("--quick-add-key", fpr, curve, "encr", expire)
        (root / "key.asc").write_bytes(gpg("--armor", "--export-secret-keys", fpr))
        return root

    def misfile(self, sub, name: str) -> None:
        """What GnuPG 2.5's import writes: the curve as a dotted OID, the KDF parameters as q, a wrong file name."""
        keys = self.home.path / "private-keys-v1.d"
        data = (keys / f"{sub.grip}.key").read_bytes()
        public_q = re.compile(rb"\(q\s*#" + rb"\s*".join(re.escape(c.encode()) for c in sub.q) + rb"#\)")
        data, n = public_q.subn(f"(q #{sub.kdf}#)".encode(), data, count=1)
        data, m = re.subn(rb"\(curve\s+[^()]*\)", f'(curve "{sub.curve}")'.encode(), data, count=1)
        self.assertEqual((n, m), (1, 1))
        (keys / f"{sub.grip}.key").unlink()
        (keys / f"{name}.key").write_bytes(data)

    def corrupt_d(self, path: Path) -> None:
        """Changes the secret d: the "(d #..#)" of GnuPG 2.5, the last skey value of 2.4's openpgp-native form."""
        data = path.read_bytes()
        last = list(re.finditer(rb"(?:\(d|_)\s*#\s*([0-9A-Fa-f])", data))[-1]
        digit = b"1" if last.group(1) != b"1" else b"2"
        path.write_bytes(data[:last.start(1)] + digit + data[last.end(1):])

    def misfile_all(self, folder: Path, names) -> set:
        """Imports folder, repairs it, then misfiles its ECDH subkeys (listing order) under names."""
        self.assertIsNone(self.repair_error(self.import_raw(folder).bare))
        grips = self.grips()
        subs = backup.ecdh_subkeys(self.listing())
        self.home.kill_agents()
        for sub, name in zip(subs, names):
            self.misfile(sub, name)
        self.assertTrue(all(s.flag == "#" for s in backup.ecdh_subkeys(self.listing())))
        return grips

    def assert_repaired(self, grips: set):
        self.assertEqual(self.key_files(), grips)
        self.assertTrue(all(s.flag == "+" for s in backup.ecdh_subkeys(self.listing())), self.listing())
        self.assertEqual(self.decrypt_failures(), [])

    def assert_imports_cleanly(self, folder: Path):
        candidate = self.import_raw(folder)
        self.assertIsNone(self.repair_error(candidate.bare))
        self.assert_repaired(self.grips())

    def test_load_repairs_the_unprotected_fixture(self):
        if self.bash:
            self.skipTest("backup.load is Python's")
        candidate = backup.choose(backup.detect(FIXTURES / "unprotected"))
        bundle = backup.choose_key(backup.load(candidate, self.home, self.fail))
        self.assertEqual([k.secret for k in bundle.subkeys], [True] * 3,
                         f"gpg {'.'.join(map(str, gpg_version()))} staging listing:\n{self.listing()}\n"
                         f"key files: {sorted(self.key_files())}")
        self.assert_repaired(self.grips())

    def test_unprotected_fixture(self):
        self.assert_imports_cleanly(FIXTURES / "unprotected")

    def test_ecdh_subkeys_of_two_curves(self):
        # On GnuPG 2.5 both have the KDF parameters 03010807: only the curve tells their misfiled files apart.
        self.assert_imports_cleanly(self.ecdh_key(["nistp256", "cv25519"]))

    def test_two_cv25519_subkeys_load_or_name_the_gnupg_25_drop(self):
        folder = self.ecdh_key(["cv25519", "cv25519"])
        if gpg_version() == (2, 5):
            error = self.repair_error(self.import_raw(folder).bare)
            self.assertIn("GnuPG 2.5 drops additional unprotected ECDH subkeys", error or "")
        else:
            self.assert_imports_cleanly(folder)

    def test_misfiled_key_is_refiled(self):
        grips = self.misfile_all(FIXTURES / "unprotected", ["0" * 40])
        self.assertIsNone(self.repair_error())
        self.assert_repaired(grips)

    def test_misfiled_keys_go_to_the_subkey_of_their_curve(self):
        # the cv25519 file sorts first, the nistp256 subkey is listed first
        grips = self.misfile_all(self.ecdh_key(["nistp256", "cv25519"]), ["1" * 40, "0" * 40])
        self.assertIsNone(self.repair_error())
        self.assert_repaired(grips)

    def test_an_expired_subkey_is_checked_at_its_binding_time(self):
        made = ("--faked-system-time", "20200101T000000!")
        grips = self.misfile_all(self.ecdh_key(["cv25519"], made, "1y"), ["0" * 40])
        self.assertEqual([s.validity for s in backup.ecdh_subkeys(self.listing())], ["e"])
        self.assertIsNone(self.repair_error())
        self.assert_repaired(grips)

    def test_a_subkey_that_does_not_decrypt_is_an_error(self):
        self.misfile_all(self.ecdh_key(["nistp256"]), ["0" * 40])
        self.corrupt_d(self.home.path / "private-keys-v1.d" / ("0" * 40 + ".key"))
        (sub,) = backup.ecdh_subkeys(self.listing())
        self.assertIn(f"subkey {sub.fpr} does not decrypt", self.repair_error() or "")

    def test_two_claimants_of_one_misfiled_file_is_the_drop_error(self):
        # GnuPG 2.5's state for two cv25519 subkeys: the first misfiled, the second dropped (no file at all)
        if gpg_version() == (2, 5):
            self.skipTest("a real import makes this state: see test_two_cv25519_subkeys_load_or_name_the_gnupg_25_drop")
        self.import_raw(self.ecdh_key(["cv25519", "cv25519"]))
        first, second = backup.ecdh_subkeys(self.listing())
        self.home.kill_agents()
        self.misfile(first, "0" * 40)
        (self.home.path / "private-keys-v1.d" / f"{second.grip}.key").unlink()
        self.assertEqual(self.repair_error(), backup.GNUPG25_DROPPED.format(f"{first.fpr} {second.fpr}"))

    def test_dropped_subkey_is_named_on_gnupg_25(self):
        # the GnuPG 2.5 state of a dropped subkey: the backup supplies it with no passphrase, gpg has no secret
        candidate = self.import_raw(FIXTURES / "unprotected")
        self.assertIsNone(self.repair_error(candidate.bare))
        (sub,) = backup.ecdh_subkeys(self.listing())
        self.home.kill_agents()
        (self.home.path / "private-keys-v1.d" / f"{sub.grip}.key").unlink()
        self.assertEqual(self.repair_error(candidate.bare, gnupg_25=True), backup.GNUPG25_DROPPED.format(sub.fpr))
        self.assertIsNone(self.repair_error(candidate.bare, gnupg_25=False))  # left to the stub check

    def test_ecdh_subkeys_parse(self):
        if self.bash:
            self.skipTest("Python's parser")
        listing = ("sec:-:255:22:AAAA:1:::-:::cC:::+::ed25519:::0:\npkd:0:80:092B:\n"
                   "ssb:-:255:18:BBBB:7::::::e:::#::cv25519::\nfpr:::::::::F1:\ngrp:::::::::G1:\n"
                   "pkd:0:88:0A2B060104019755010501:\npkd:1:263:40AB:\npkd:2:32:03010807:\n"
                   "ssb:-:255:22:CCCC:1::::::a:::+::ed25519::\nfpr:::::::::F2:\ngrp:::::::::G2:\npkd:1:263:40CD:\n")
        self.assertEqual(backup.ecdh_subkeys(listing),
                         [backup.EcdhSubkey("BBBB", "F1", "G1", "1.3.6.1.4.1.3029.1.5.1", "40AB", "03010807", "#",
                                            "-", 7)])
        self.assertEqual(backup.oid_text("082A8648CE3D030107"), "1.2.840.10045.3.1.7")


@unittest.skipUnless(LINUX, "the bash implementation is Linux-only")
class BashEcdhRepairTests(EcdhRepairTests):
    """The same, repaired by recoverCard.sh's repair_ecdh_imports."""

    bash = True

    def repair_error(self, bare=(), gnupg_25=None):
        script = (RECOVERY / "recoverCard.sh").read_text()
        block = script[script.index("# GnuPG 2.5 ECDH repair"):script.index("load_candidate() {")]
        if gnupg_25 is not None:
            block += f"gnupg_25() {{ {'true' if gnupg_25 else 'false'}; }}\n"
        result = subprocess.run(["bash", "-c", f"EXIT_BACKUP={EXIT_BACKUP}\n" + 'die() { echo "$2" >&2; exit "$1"; }\n'
                                 + block + 'set -o nounset -o pipefail -o errexit; repair_ecdh_imports "$1" "$2"',
                                 "bash", str(self.home.path), " ".join(bare)], capture_output=True, text=True)
        if result.returncode == 0:
            return None
        self.assertEqual(result.returncode, EXIT_BACKUP, result.stderr)
        return result.stderr.strip()


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

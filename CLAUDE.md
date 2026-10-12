# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Documentation project: a guide to using YubiKey with GnuPG for encryption, signing and SSH. `README.md` (~2,300 lines) is the main content. Also present: `SECENV.md` (air-gapped Alpine Linux setup, not linked from README), `piv/` (separate PIV certificate guide + `generateCerts.sh`), `config/` (gpg, gpg-agent, scdaemon, dirmngr confs), `scripts/` (`generateKeys.sh`, `resetCard.sh`, `switchKeys.sh`), `nix/` (LiveCD flake).

## Build Commands

No build system. CI (`.github/workflows/lint.yml`) runs the checks below; run them locally after editing docs or scripts.

```bash
# Markdown lint, incl. broken #anchor links (MD051). Config + file globs: .markdownlint-cli2.jsonc
npx --yes markdownlint-cli2@0.23.3

# Every README heading must appear in the hand-written ToC
python3 .github/scripts/check_toc.py README.md

# Scripts (shellcheck via `pipx install shellcheck-py`). CI gates on errors only; existing
# SC2155/SC2086/SC3045 warnings are upstream — don't add new ones.
shellcheck --severity=error scripts/*.sh piv/*.sh recovery/recoverCard.sh recovery/tests/fixtures/make_fixtures.sh

# Recovery toolset tests (Python + bash, fake card hardware)
cd recovery && python3 -m unittest discover -s tests -t . -v

# Build NixOS Live ISO
nix build --experimental-features "nix-command flakes" nix#nixosConfigurations.yubikeyLive.x86_64-linux.config.system.build.isoImage

# Test ISO in QEMU
qemu-system-x86_64 -enable-kvm -m 4G -smp 2 \
  -drive readonly=on,media=cdrom,format=raw,file=result/iso/yubikeyLive.iso

# Format flake.nix (alejandra)
nix fmt

# Update flake inputs
nix flake update --commit-lock-file

# Docs site (GitHub Pages, MkDocs Material); generated from the Markdown files above, never edit site/.build
python3 -m unittest discover -s site -v
python3 site/build.py && mkdocs build --strict -f site/mkdocs.yml   # pip install -r site/requirements.txt first
```

## README Conventions

- The table of contents (lines ~3–62) is hand-maintained and mirrors every heading. Adding/renaming a heading requires updating it, using GitHub slug anchors (lowercase, spaces→`-`, punctuation dropped, e.g. `#use-sgpg-agentssh`).
- ` ```bash ` blocks are plain copy-paste commands with no `$` prompt; ` ```console ` blocks show `$ cmd` followed by sample output.
- Shell variables are exported once early and reused in later sections: `$GNUPGHOME`, `$IDENTITY`, `$KEY_TYPE`, `$KEY_EXPIRATION`, `$CERTIFY_PASS`, `$KEY_FP`, `$KEY_ID`, `$ADMIN_PIN`/`$USER_PIN`. Keep names consistent across the file ("Updating keys" re-exports them). Pass passphrases as `printf '%s' "$CERTIFY_PASS" | gpg --batch --passphrase-fd 0 ...`.
- Platform variants use bold labels (`**macOS**`, `**OpenBSD**`, `**NixOS**`, …), not headings. Preserve coverage: Debian Live (primary), Arch, Fedora, macOS/MacPorts, OpenBSD, NixOS, Windows/WSL, Tails.
- Links to repo files use absolute drduh upstream URLs (`github.com/drduh/YubiKey-Guide/blob/main/...`), not relative paths.

## Scripts and Nix

- `generateKeys.sh` and `resetCard.sh` are bash (`#!/usr/bin/env bash`); `switchKeys.sh` is `#!/bin/sh`. Only `resetCard.sh` sends raw APDUs via `gpg-connect-agent`.
- `nix/flake.nix` pins `nixos-26.05` (`system.stateVersion` stays "25.05" intentionally) and builds an air-gapped XFCE ISO for x86_64 and aarch64. It embeds `README.md`, `config/gpg.conf` and `config/gpg-agent.conf` — editing those changes the ISO.
- `recovery/` holds two implementations of the same tool (`ykrecover/` Python + Tkinter, `recoverCard.sh` bash). They must print byte-identical `--dry-run` plans (`recovery/tests/golden/`); change Python first, regenerate with `UPDATE_GOLDEN=1`, then make bash match.
- The Python recovery tool also builds for Windows (`recovery/packaging/`, PyInstaller; CI job `windows`, release workflow on `v*` tags). Platform differences live only in `recovery/ykrecover/osdeps.py`; call `osdeps.run()` instead of `subprocess` so tools resolve to Gpg4win/bundled ykman on Windows.

## Repo Workflow

- `.gitignore` is `.*` — new dotfiles/dirs are silently ignored unless whitelisted with a `!` line (as done for `.github/` and `.markdownlint-cli2.jsonc`).
- Fork of `drduh/YubiKey-Guide`. Local `master` syncs from upstream `main` via merge commits; only `origin` (mezinster fork) is configured as a remote.
- `site/` builds the GitHub Pages site (`.github/workflows/pages.yml`, on pushes to master, manual runs and after each release). `site/build.py` copies README.md, SECENV.md, piv/README.md and recovery/README.md into `site/.build` and adapts links and alerts there; a new top-level doc must be added to `PAGES` in build.py and to `nav` in `site/mkdocs.yml`.

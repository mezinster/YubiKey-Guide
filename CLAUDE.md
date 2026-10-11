# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Documentation project: a guide to using YubiKey with GnuPG for encryption, signing and SSH. `README.md` (~2,300 lines) is the main content. Also present: `SECENV.md` (air-gapped Alpine Linux setup, not linked from README), `piv/` (separate PIV certificate guide + `generateCerts.sh`), `config/` (gpg, gpg-agent, scdaemon, dirmngr confs), `scripts/` (`generateKeys.sh`, `resetCard.sh`, `switchKeys.sh`), `nix/` (LiveCD flake).

## Build Commands

No build system, CI or tests — verify commands and anchors by hand.

```bash
# Lint scripts (shellcheck installed via `pipx install shellcheck-py`).
# Existing upstream code has SC2155/SC2086/SC3045 warnings — only fix ones your edit introduces.
shellcheck scripts/*.sh piv/*.sh

# Lint Markdown (Node 14 locally, so pin markdownlint-cli 0.31.1). Disabled rules match intentional
# conventions: long prose lines, multiple top-level `#` sections, <details> HTML, bold platform labels.
# ~18 pre-existing findings remain upstream — only fix ones your edit introduces.
npx -p markdownlint-cli@0.31.1 markdownlint --disable MD013 MD025 MD033 MD036 MD041 -- README.md SECENV.md piv/README.md

# Build NixOS Live ISO
nix build --experimental-features "nix-command flakes" nix#nixosConfigurations.yubikeyLive.x86_64-linux.config.system.build.isoImage

# Test ISO in QEMU
qemu-system-x86_64 -enable-kvm -m 4G -smp 2 \
  -drive readonly=on,media=cdrom,format=raw,file=result/iso/yubikeyLive.iso

# Format flake.nix (alejandra)
nix fmt

# Update flake inputs
nix flake update --commit-lock-file
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

## Repo Workflow

- Fork of `drduh/YubiKey-Guide`. Local `master` syncs from upstream `main` via merge commits; only `origin` (mezinster fork) is configured as a remote.

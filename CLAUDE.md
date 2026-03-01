# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is **YubiKey-Guide** — a comprehensive guide to using YubiKey as a smart card for GnuPG-based encryption, signing, and SSH authentication. It is primarily a documentation and configuration project (not a traditional software project), forked from drduh/YubiKey-Guide.

The main content is in `README.md` (~2,300 lines) covering the full YubiKey setup lifecycle: secure environment preparation, GPG key generation, subkey creation, transferring keys to YubiKey, and ongoing use for SSH/email/signing.

## Key Files

- **README.md** — The primary guide (setup, key management, SSH, email, maintenance)
- **SECENV.md** — Supplementary guide for air-gapped Alpine Linux environments
- **config/gpg.conf** — Hardened GPG configuration (AES256, SHA512, smartcard)
- **config/gpg-agent.conf** — GPG agent config for PIN entry, SSH, cache TTL
- **scripts/generate.sh** — Automated GPG key generation (RSA4096/ed25519, passphrase generation)
- **scripts/reset-yubikey** — Factory reset via APDU commands
- **scripts/switch-to-backup-yubikey** — Multi-YubiKey switching utility
- **nix/flake.nix** — NixOS flake for building a LiveCD ISO with YubiKey tools

## Build Commands

There is no traditional build system. The Nix flake builds a LiveCD ISO:

```bash
# Build NixOS Live ISO image
nix build --experimental-features "nix-command flakes" nix#nixosConfigurations.yubikeyLive.x86_64-linux.config.system.build.isoImage

# Update Nix flake dependencies
nix flake update --commit-lock-file

# Test ISO with QEMU
qemu-system-x86_64 -enable-kvm -m 4G -smp 2 -drive format=raw,file=result/iso/nixos.iso
```

## Content Architecture

The README follows a sequential workflow:
1. **Environment Setup** — Platform-specific install instructions (Debian, Fedora, Arch, macOS, OpenBSD, NixOS)
2. **Key Generation** — Certify key → Subkeys (Sign, Encrypt, Authenticate) → Verification → Backup
3. **YubiKey Configuration** — PIN setup, attribute setting, key transfer
4. **Operational Use** — Encryption/decryption, signatures, SSH, GitHub, agent forwarding, email clients
5. **Maintenance** — Key renewal, rotation, YubiKey reset

## Editing Guidelines

- All Markdown content uses fenced code blocks with shell commands users copy-paste into terminals. Ensure commands are correct and complete.
- The guide supports multiple platforms; platform-specific instructions are clearly sectioned. When editing, preserve platform coverage.
- Scripts in `scripts/` use POSIX-compatible shell and interact with `gpg-connect-agent` via APDU commands.
- The Nix flake (`nix/flake.nix`) targets NixOS 25.05 and builds an XFCE-based LiveCD.

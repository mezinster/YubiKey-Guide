# YubiKey Guide

This site is the guide to using a [YubiKey](https://www.yubico.com/) with GnuPG for encryption, signing and SSH. It also documents **ykrecover**, a tool that loads backed-up keys onto new YubiKeys.

## The guide

- [Guide](guide.md): prepare an offline environment, create the keys, move them to YubiKeys, and use them for GnuPG and SSH.
- [Secure environment](secenv.md): build an air-gapped Alpine Linux system for key work.
- [PIV](piv.md): use the YubiKey's PIV application with certificates.

## The recovery tool

[ykrecover](recovery.md) reads an existing key backup, in the layout the guide produces or in most other layouts. It then copies, renews or rotates the subkeys onto one or more new YubiKeys. It runs on Linux (Python or bash) and on Windows, from a [download](download.md).

## Source

The guide is written by [drduh](https://github.com/drduh/YubiKey-Guide). This site is built from the fork [mezinster/YubiKey-Guide](https://github.com/mezinster/YubiKey-Guide), which adds the recovery tool. Pages are generated from the repository's Markdown files on every change.

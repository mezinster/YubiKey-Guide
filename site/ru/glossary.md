# Russian translation: rules and glossary

This file is not published. It records how the pages in `site/ru/` are translated, so that every
translation and every later update reads the same way.

## Header

Every page starts with front matter naming its English source and git's blob hash of that source at
translation time (`python3 site/build.py --hash <source>`):

```yaml
---
source: README.md
source_hash: <hash>
---
```

When the English source changes, the page shows an "outdated" banner until it is translated again and
the hash is updated.

## Never translated

- fenced code blocks (`site/check_ru.py` checks they are identical to the English ones) and inline code;
- commands, options, file and folder names, environment variables, key IDs, fingerprints, URLs;
- product names: YubiKey, GnuPG/GPG, OpenPGP, PIV, ykman, Gpg4win, Debian, Tails, macOS, Windows.

## Structure

- Same headings, in the same order and at the same level. Every heading ends with the English
  heading's anchor: `# Покупка YubiKey {#purchase-yubikey}`. The anchor is the GitHub slug of the
  English heading text: lower case, spaces to `-`, punctuation dropped (as in the English table of
  contents). `site/check_ru.py` checks that every English anchor exists.
- GitHub alerts stay alerts (`> [!NOTE]`, `> [!WARNING]` ...): only their text is translated.
- Lists, tables, images, `<details>`/`<summary>` blocks, footnotes and bold platform labels
  (**Linux**, **macOS** ...) keep their place. Link targets stay as they are; link text is translated.
- The guide's hand-written table of contents is left out: the site shows its own.

## Style

- Formal "вы"; short sentences; neutral technical Russian, no anglicisms where a common Russian term exists.
- Imperative for instructions: «Выполните», «Скопируйте», «Подключите».
- UI labels of programs stay in English, with a translation in parentheses on first use if helpful.

## Glossary

| English | Russian |
| - | - |
| Certify key | ключ сертификации (Certify) |
| Subkey | подключ |
| Signature / Encryption / Authentication subkey | подключ подписи / шифрования / аутентификации |
| passphrase | парольная фраза |
| PIN, Admin PIN, User PIN | PIN, PIN администратора, PIN пользователя |
| Reset Code | код сброса |
| air-gapped | изолированный от сети |
| live image / live environment | live-образ / live-среда |
| backup | резервная копия |
| encrypted volume | зашифрованный том |
| public key / private key | открытый ключ / закрытый ключ |
| revocation certificate | сертификат отзыва |
| expiration | срок действия |
| smart card | смарт-карта |
| touch policy | политика касания |
| keyring | связка ключей |
| fingerprint | отпечаток |
| key stub | заглушка ключа |
| identity (user ID) | идентификатор пользователя (user ID) |
| renew / rotate (subkeys) | продлить / заменить (подключи) |
| recovery tool | утилита восстановления |
| clone / renew / rotate (ykrecover modes) | clone / renew / rotate (режимы не переводятся) |

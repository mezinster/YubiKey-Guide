# Russian version of the docs site

## Goal

Add Russian as a second language to `https://mezinster.github.io/YubiKey-Guide/`. Every page gets a Russian version under `/ru/`:

- Home
- Guide
- Secure environment
- PIV
- Recovery tool
- Download

The site interface (menus, search, buttons) is in Russian on those pages, and a language switcher links the two versions. English stays the default and the only source of truth. When an English source changes, its Russian page is marked as outdated until it is translated again.

## Decisions taken

| Question | Decision |
| - | - |
| Who translates | Claude translates; the owner reviews in the PR. |
| Keeping it current | Each translation records a hash of the English text it was made from. A changed source gives the Russian page an "outdated" banner and a CI warning, never a failed build. |
| Pages | All six. |
| Approach | Two MkDocs builds (English, Russian) published as one site. Material's `extra.alternate` provides the language switcher. No new dependencies. |

## Files

| File | Content |
| - | - |
| `site/ru/home.md` | Russian home page (translation of `site/home.md`) |
| `site/ru/guide.md` | translation of `README.md` |
| `site/ru/secenv.md` | translation of `SECENV.md` |
| `site/ru/piv.md` | translation of `piv/README.md` |
| `site/ru/recovery.md` | translation of `recovery/README.md` |
| `site/ru/download.json` | Russian strings for the generated download page |
| `site/ru/glossary.md` | terms used across the translation (not published) |
| `site/mkdocs.ru.yml` | Russian build config |

### Translation file header

Every translated page starts with front matter:

```yaml
---
source: README.md
source_hash: 3b18e512dba79e4c8300dd08aeb37f8e728b8dad
---
```

- `source` is the English source path, relative to the repo root.
- `source_hash` is git's blob hash of that file at translation time: SHA-1 of `blob <size>\0<content>`, the value `git hash-object <file>` prints. `build.py` computes it with `hashlib`, so it does not need git.
- `site/ru/home.md` records `source: site/home.md`.

### Translation rules (recorded in `site/ru/glossary.md`)

- **Never translated:**
  - code blocks and inline code;
  - commands, file names, environment variables, key IDs;
  - URLs;
  - product names (YubiKey, GnuPG, OpenPGP, ykman, Gpg4win).
- **Headings keep the English anchor** through `attr_list`: `## Покупка YubiKey {#purchase-yubikey}`. The id is the slug GitHub gives the English heading. So `#updating-keys` and every other in-page or cross-page link works in both languages.
- **Glossary terms**, used consistently:

  | English | Russian |
  | - | - |
  | Certify key | ключ сертификации (Certify) |
  | Subkey | подключ |
  | passphrase | парольная фраза |
  | Admin PIN / User PIN | PIN администратора / PIN пользователя |
  | air-gapped | изолированная от сети |
  | backup | резервная копия |
  | revocation certificate | сертификат отзыва |
  | smart card | смарт-карта |

  Further terms are added to the glossary as they come up.
- **Structure stays aligned.** Admonitions, `<details>` blocks, lists, tables, images and footnotes keep the same structure and order as the English text, so a reader can compare the two side by side.
- **Writing style.** Formal "вы". Short sentences; neutral technical Russian.

## Build (`site/build.py`)

- **Languages.** `build(out, releases)` builds both languages:
  - English into `out/` (unchanged);
  - Russian into `out/ru/` from `site/ru/*.md`.
- **Same adaptations as English.** Russian pages get the same treatment: link rewriting, alert conversion, list re-indentation, and heading demotion with a front-matter title for the multi-H1 pages.
  - Russian titles: «Руководство», «Безопасная среда», «PIV».
  - The rewriting maps sources to Russian pages: links that point at a source (`recovery/README.md`, an upstream `blob/.../SECENV.md`) go to the Russian page with the same file name.
  - `media/` is copied into the Russian build too, so images resolve.
- **Outdated pages.** When a page's `source_hash` differs from the current hash of its source:
  - The page gets a warning admonition at the top: «Перевод устарел: английский текст изменился после перевода. Актуальная версия — [на английском](…).» The link points to the matching English page.
  - The script prints `warning: ru/<page> is out of date with <source>` to stderr. When it runs under GitHub Actions (`GITHUB_ACTIONS=true`), it uses the annotation form `::warning file=site/ru/<page>::…`.
  - The build still succeeds.
- **Missing or invalid header.** A translation file without valid `source`/`source_hash` front matter is an error (exit 1). So is a translation whose source is not in `PAGES`.
- **Download page.** `render_download` takes the language's strings: English as today, Russian from `site/ru/download.json` (keys `title`, `intro`, `published`, `notes`, `earlier`, `release`, `date`, `prerelease`, `all`, `none`). Release names and notes stay as GitHub has them.
- **New command.** `python3 site/build.py --hash FILE` prints a file's blob hash. It is used when updating a translation's header.

## Site configs

### `site/mkdocs.ru.yml`

A copy of the English config, with these differences:

- `INHERIT` is not used. The file is standalone and duplicates the theme and extension settings, because MkDocs resolves `INHERIT` paths relative to the child file and the nav differs anyway.
- `docs_dir: .build/ru`, `site_dir: .site/ru`.
- `site_url: https://mezinster.github.io/YubiKey-Guide/ru/`.
- `theme.language: ru`.
- The `search` plugin with `lang: [ru, en]`.
- `nav` with Russian labels:

  | Label | Page |
  | - | - |
  | Главная | `index.md` |
  | Руководство | `guide.md` |
  | Безопасная среда | `secenv.md` |
  | PIV | `piv.md` |
  | Утилита восстановления | `recovery.md` |
  | Загрузка | `download.md` |

### Both configs

Both configs add the same `extra.alternate`:

- English, link `/YubiKey-Guide/`, `lang: en`.
- Русский, link `/YubiKey-Guide/ru/`, `lang: ru`.

### Strict builds and anchors

- Both builds run `--strict`, with anchor validation.
- The Russian build validates against the Russian pages' explicit ids.
- A unit test checks that every heading id on an English page also exists on its Russian page. Fixed headings in the Russian files must carry an explicit `{#…}`.

### Build order and paths

- **Build order.** English first, then Russian.
- **Paths in the workflow and lint.**
  - `mkdocs build --strict -f site/mkdocs.yml` writes `site/.site/`.
  - `mkdocs build --strict -f site/mkdocs.ru.yml` writes `site/.site/ru/`.
- **Clean.** The English build must not wipe `site/.site/ru/`. So build Russian second, or build into separate folders and merge before upload.
- **Upload.** `site/.site` is uploaded as one artifact.

## Workflows

- **`pages.yml`:** the build step runs `build.py` once, then both `mkdocs build` commands.
- **Path filter:** the push `paths` already include `site/**`, which covers translations.
- **`lint.yml`:** the docs-site step runs both strict builds.

## Testing (`site/test_build.py`)

- **Blob hash.** It matches `git hash-object` for a sample file. The test computes the expected value with git when git is available; otherwise it uses a hard-coded known pair.
- **Outdated detection.**
  - A matching hash gives no banner and no warning.
  - A changed source gives the banner with the English link, plus the warning line.
  - A missing header gives exit 1.
- **Russian link rewriting.** Links resolve to Russian pages, and image paths work from `ru/`.
- **Anchor parity.** Every heading id (explicit `{#…}` or slug) in each built English page exists in the built Russian page.
- **Russian download page.** It uses the strings from `download.json`, with the same no-release fallback.
- **Build layout.** The build writes `out/ru/*.md` and `out/ru/media`.
- **Strict builds of both configs, locally and in CI.** Spot-check the language switcher, Russian search and an outdated-page banner. Produce the banner by building with a modified hash in a temporary copy.

## Out of scope

- Automatic re-translation (no API calls in CI).
- Languages other than Russian.
- Translating `docs/superpowers`, `CLAUDE.md` or code comments.
- Russian strings inside the ykrecover tool itself.

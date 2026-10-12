# GitHub Pages documentation site

## Goal

Publish this repository's documentation as a website at `https://mezinster.github.io/YubiKey-Guide/`. The site has two parts:

- the YubiKey guide;
- the ykrecover recovery tool, including a download page for its Windows releases.

A GitHub Actions workflow builds and deploys the site, so it stays current without manual steps.

## Decisions taken

| Question | Decision |
| - | - |
| Audience and scope | The guide (README, secure environment, PIV) plus a recovery tool section with its docs and release downloads. |
| Generator | MkDocs with the Material theme. |
| Single source | The existing Markdown files stay the only source. The build copies and adapts them into a temporary folder and never edits them. Upstream merges from drduh/YubiKey-Guide stay conflict-free. |
| Rebuild triggers | A push to master that touches a docs source or the site files (this includes upstream syncs), a published release, and a manual run. |

## Pages

| Nav entry | Source | Notes |
| - | - | - |
| Home | `site/home.md` (new) | Says what the guide is and what ykrecover is, links into both, and credits drduh/YubiKey-Guide as upstream. |
| Guide | `README.md` | One page. The hand-written table of contents (from the first `- [` list up to the first `---`) is removed; Material's page table of contents replaces it. The page title is set to "Guide", because the file opens with prose and has 28 top-level `#` sections. |
| Secure environment | `SECENV.md` | |
| PIV | `piv/README.md` | |
| Recovery tool | `recovery/README.md` | |
| Download | generated | The newest ykrecover release first, with its zip link, size, date and notes. Earlier releases follow in a short table. Pre-releases are labelled as such. |

The guide stays a single page. Splitting it per `#` section would require rewriting every `#anchor` link into a `page#anchor` link. That is left for later, if the page proves too long.

## Build (`site/build.py`, stdlib only)

The script prepares the MkDocs source folder `site/.build/` (git-ignored by the repo's `.*` rule):

1. **Copy sources.** Each source in the Pages table becomes its page file. The pages are `index.md`, `guide.md`, `secenv.md`, `piv.md`, `recovery.md` and `download.md`. `media/` is copied too.
2. **Guide adjustments.** The script strips the hand-written table of contents and prepends `title:` front matter.
3. **Rewrite links between sources** to the matching site page, keeping any `#anchor`. Links between sources use either a relative path (`recovery/README.md`) or an absolute upstream URL (`https://github.com/drduh/YubiKey-Guide/blob/<branch>/README.md#x`, `…?tab=readme-ov-file#identity`).
4. **Rewrite relative links to other repo files** (for example `./yk.cnf` in `piv/README.md`) to `https://github.com/mezinster/YubiKey-Guide/blob/master/<path>`, so that they work on the site. Absolute links to drduh's repo files (`scripts/`, `config/`, `templates/`), issues and other sites are left unchanged.
5. **Convert GitHub alert blocks** (`> [!NOTE]`, `[!TIP]`, `[!IMPORTANT]`, `[!WARNING]`, `[!CAUTION]`; 29 of them) into Material admonitions (`!!! note` and so on). Their content is kept, and indentation is applied.
6. **Write the download page** from a releases JSON file passed with `--releases FILE`:
   - The file is the output of `gh api repos/{owner}/{repo}/releases`.
   - Drafts are skipped.
   - When the option is missing, the page says that releases are listed on GitHub and links there, so local builds work offline.

Release notes are inserted as Markdown and limited to their own section. Asset sizes are shown in MB.

## Site config (`site/mkdocs.yml`)

- `docs_dir: .build`, `site_dir: .site`.
- `site_url: https://mezinster.github.io/YubiKey-Guide/`, `repo_url` points to the fork, and `edit_uri` is empty (pages are generated).
- **Material theme**
  - light/dark palette toggle;
  - `navigation.sections` and `navigation.top`;
  - `toc.follow`;
  - `search.suggest` and `search.highlight`;
  - `content.code.copy`.
- **Markdown extensions**
  - `admonition`, `pymdownx.details` and `pymdownx.superfences`;
  - `attr_list`;
  - `md_in_html` (for the existing `<details>` blocks);
  - `toc` with `permalink`, and with GitHub-style slugs (`pymdownx.slugs.slugify`, case lowered) so that existing anchors such as `#use-sgpg-agentssh` still resolve.
- `nav` lists the six pages in the order of the Pages table.
- `validation` sets links, absolute links and anchors to `warn`, and the build runs with `strict: true`. So warnings, broken internal links or anchors, and missing files fail it.

`site/requirements.txt` pins `mkdocs-material` to an exact current 9.x version (it pulls in a compatible `mkdocs` and `pymdown-extensions`). CI installs it with `pip install -r`.

## Workflow (`.github/workflows/pages.yml`)

**Triggers**
- `push` to `master`, limited by `paths`: `README.md`, `SECENV.md`, `piv/README.md`, `recovery/README.md`, `media/**`, `site/**` and the workflow file itself.
- `release` (`published`).
- `workflow_dispatch`.

**Settings**
- **Permissions:** `contents: read`, `pages: write`, `id-token: write`.
- **Concurrency:** group `pages`, so that a newer run replaces a queued one.

**Job `build`**
1. Checkout, then `setup-python` 3.12, then `pip install -r site/requirements.txt`.
2. `gh api repos/${{ github.repository }}/releases > releases.json` (with the job's `GITHUB_TOKEN`).
3. `python3 site/build.py --releases releases.json`.
4. `mkdocs build -f site/mkdocs.yml`.
5. `actions/upload-pages-artifact` from `site/.site`.

**Job `deploy`**
- Uses the `github-pages` environment.
- Runs `actions/deploy-pages`.

**Pull requests**
The existing `lint` workflow gains a step that runs the same build without deploying:

```text
pip install -r site/requirements.txt
python3 site/build.py
mkdocs build --strict -f site/mkdocs.yml
```

A broken link or config fails the PR before it reaches master.

## One-time setup (outside the repo; done only with the owner's approval)

- Enable Pages with build type "GitHub Actions": `gh api -X POST repos/mezinster/YubiKey-Guide/pages -f build_type=workflow`.
- Set the repo homepage to `https://mezinster.github.io/YubiKey-Guide/`. It currently points to drduh's site.

## Repo conventions

- `.gitignore` already ignores `site/.build` and `site/.site` through `.*`. Nothing new needs whitelisting.
- `site/home.md` is added to the markdownlint globs.
- `CLAUDE.md` gets a short "Docs site" note: how to build locally, and that the site is generated from the existing files.

## Testing

- **`site/test_build.py` (unittest, stdlib)**
  - table-of-contents stripping;
  - each link rewrite rule (relative, absolute upstream with `blob/main`, `blob/master` and `?tab=readme-ov-file`, other repo files, external);
  - alert conversion, including multi-line and nested content;
  - the download page from a sample releases JSON (latest first, pre-release label, drafts skipped);
  - the offline fallback.
- **CI**
  - The `lint` workflow runs `python3 -m unittest discover -s site`.
  - The `lint` workflow also runs the strict MkDocs build.
- **Locally**
  - Run `mkdocs serve -f site/mkdocs.yml` after `python3 site/build.py`.
  - Check by eye: the guide's anchors from the old table of contents, admonitions, the `<details>` block, images and code copy buttons.

## Out of scope

- Splitting the guide into several pages.
- Versioned docs.
- Translations.
- A custom domain.
- Analytics.
- Publishing the `docs/superpowers` specs and plans.

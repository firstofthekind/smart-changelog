# Smart-Changelog

Automate changelog maintenance across GitHub Actions and GitLab CI pipelines with a single, conflict-free file.

## Why Smart-Changelog?
- ✅ One canonical `CHANGELOG.md` managed by CI to avoid merge conflicts
- 🔗 Pulls ticket metadata directly from Jira
- 🤖 Optional OpenAI enrichment for polished descriptions and smarter categorisation
- 🔁 Idempotent updates keyed off ticket IDs
- 🛠️ Drop-in for any repository (Java, Kotlin, Python, you name it)

## Installation
```bash
pip install smart-changelog
```

## Usage
Update the changelog from your CLI or inside CI:
```bash
smart-changelog update [--dry-run] [--verbose] [--ai] [--ticket TICKETID]
```

### Options
- `--dry-run`: Print the updated changelog instead of writing it.
- `--verbose`: Enable debug logging.
- `--ai`: Allow OpenAI (`gpt-4o-mini`) to refine Jira summaries and suggest categories when `OPENAI_API_KEY` is configured.
- `--ticket`: Force a specific ticket ID. Useful for backfills or hotfixes.

## Environment Variables
| Variable | Purpose |
| --- | --- |
| `JIRA_URL` | Base URL to your Jira instance (e.g. `https://yourcompany.atlassian.net`). |
| `JIRA_TOKEN` | (Optional) Jira bearer token with read access. |
| `JIRA_EMAIL` / `JIRA_API_TOKEN` | Alternative to `JIRA_TOKEN`; supply your Atlassian email plus API token for Basic auth. |
| `OPENAI_API_KEY` | Optional API key for description enrichment and category suggestions. |
| `SMART_CHANGELOG_TEMPLATE` | Optional path to a custom Jinja2 template for version sections. |
| `CI_COMMIT_AUTHOR` / `GIT_AUTHOR_NAME` | Used to attribute changelog entries. |
| `CI_COMMIT_BRANCH`, `GITHUB_REF_NAME`, etc. | Used to determine the target branch. |

## Versioning with `manifest.yaml`
Smart-Changelog uses `manifest.yaml` to determine the release section to update. The file must contain a
`version` mapping with `major` and `minor` numbers (and optionally `prerelease`). Whenever you bump the
version in `manifest.yaml`, the next run automatically creates or updates the matching changelog section.

```yaml
version:
  major: 1
  minor: 105
  patch: ${CI_PIPELINE_IID}
  prerelease: ""
```

## How It Works
1. Detect the latest commit or merge title and extract the Jira ticket (pattern `[A-Z]+-\d+`).
2. Determine the change type (`feat`, `fix`, `chore`, `refactor`) to target the right section (AI can override when enabled).
3. Fetch ticket details from Jira and optionally enrich summaries / categories via OpenAI.
4. Update `CHANGELOG.md` under the current manifest version section using a template (default or custom).
5. Stage, commit, and push the changelog if changes are found (skip when `--dry-run`).

### Customising the version template

The default version block lives at `smart_changelog/templates/version_block.md.j2` and renders a section
per category whenever entries exist. You can override it by setting `SMART_CHANGELOG_TEMPLATE` to the path
of your own Jinja2 template. The template receives:

```jinja
{{ version }}     # e.g. 1.6
{{ date }}        # ISO date string
{{ sections }}    # list of {key, heading, entries, start_marker, end_marker}
```

`start_marker` / `end_marker` comments (e.g. `<!-- section:feature -->`) must be included so the updater can
parse and merge existing entries across runs.

## CI/CD Integration
### GitHub Actions
```yaml
name: Update Changelog
on:
  push:
    branches: [ main ]
jobs:
  changelog:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Setup Python
        uses: actions/setup-python@v5
        with:
          python-version: 3.12
      - name: Install Smart-Changelog
        run: |
          pip install .
          rm -rf build smart_changelog.egg-info
      - name: Run Smart-Changelog
        env:
          JIRA_URL: ${{ secrets.JIRA_URL }}
          JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
          JIRA_EMAIL: ${{ secrets.JIRA_EMAIL }}
          JIRA_API_TOKEN: ${{ secrets.JIRA_API_TOKEN }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          SMART_CHANGELOG_SKIP_COMMIT=1 smart-changelog update
      - name: Commit and push
        run: |
          git config user.email "bot@company.com"
          git config user.name "SmartChangelog Bot"
          git add CHANGELOG.md
          git commit -m "chore: update changelog" || echo "No changes"
          git push
```

### GitLab CI/CD
```yaml
stages:
  - changelog

update_changelog:
  stage: changelog
  image: python:3.12
  script:
    - pip install .
    - SMART_CHANGELOG_SKIP_COMMIT=1 smart-changelog update
    - git config user.email "bot@company.com"
    - git config user.name "SmartChangelog Bot"
    - git add CHANGELOG.md
    - git commit -m "chore: update changelog [skip ci]" || echo "No changes"
    - git push origin HEAD:main
  only:
    - main
```

## Java/Kotlin Build Integration
Add a Gradle task to bridge your build tooling with the CLI:
```kotlin
// build.gradle.kts
tasks.register("updateChangelog") {
    group = "automation"
    description = "Run Smart-Changelog to refresh CHANGELOG.md"
    doLast {
        exec {
            commandLine("smart-changelog", "update")
        }
    }
}
```
Then call `./gradlew updateChangelog` locally or in CI to keep the changelog up-to-date.

## Local Development Tips
- Use `--dry-run` when iterating locally to inspect the generated output.
- Set `SMART_CHANGELOG_SKIP_COMMIT=1` to bypass the built-in auto-commit/push step when experimenting.
- Combine with feature branch protections so `main` remains the single source of truth.


## License
Distributed under the MIT License. See `LICENSE` for details.

# GitHub account setup

**Reviewed:** 2026-09-08. Integration: `github`. Install the `github` extra from the
source checkout (`uv pip install -e ".[github]"`). Begin with
[environment setup](environments.md).

## Obtain a token

Sign into [GitHub](https://github.com), then open your profile **Settings →
Developer settings → Personal access tokens → Fine-grained tokens → Generate new
token**. Set a name, expiration and resource owner. Choose **Only select
repositories** and the specific repositories required. Organization policies may
require owner approval before the token can access its repositories.
[Official token creation and restrictions](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

Use `GITHUB_TOKEN`. Do not substitute an SSH key, Git password, GitHub App private
key or GitHub OAuth client secret. An app installation access token can be supplied
by a host that manages its issuance and expiry; ZeoCore does not issue one from an
app's private key in this integration. The initial identity check below is intended
for a personal access token; use a repository read for installation tokens.

Select repository **Metadata: read**, **Contents: read** for repository content,
**Issues: read/write** if creating or modifying issues, and **Pull requests:
read/write** for PR work. Grant write access only for implemented operations you
need. Organization/account creation operations may need additional permissions
not covered by a repository-limited token; consult each REST endpoint's fine-grained
permission requirements instead of adding broad classic `repo` permission by habit.

## Test account track

GitHub has no sandbox host assumed by this integration. Use a dedicated test
repository, such as `YOUR-OWNER/zeocore-integration-test`, and a token restricted to
that repository. A separate automation/test identity provides stronger separation
where your organization permits it. Keep the test token unable to access production
repositories. If using one identity, different tokens alone are insufficient unless
their repository grants are distinct.

Create a small README in the test repository through GitHub's UI. Save this as
`check_github.py` and substitute the intended repository name:

```python
from zeo_core.integrations.github import GitHubIntegration

service = GitHubIntegration()
configured = service.initialize()
if not configured.success:
    raise SystemExit("GitHub initialization failed; check selected token and configuration")
result = service.get_repo("YOUR-OWNER/zeocore-integration-test")
if not result.success:
    raise SystemExit("Repository read failed; check owner, permissions and approval")
print("Selected GitHub repository read succeeded")
```

```bash
python -m zeo_core.integrations.environments --mode test   --root "$ZEO_ENV_ROOT" --integration github --secret GITHUB_TOKEN   -- python /absolute/path/check_github.py
```

For CI use `ZEO_TEST_GITHUB_TOKEN`. A bare `GITHUB_TOKEN` from the parent, including
a CI job token, is deliberately not inherited. Map the intended test secret into
the selected namespace explicitly.

## Production account track

Create a new token limited to the actual production repositories and approved
operations. Choose the correct organization/resource owner, get required approval,
and store it as `ZEO_PRODUCTION_GITHUB_TOKEN`. Use a separate production resource
configuration or script with the intended repository. Run its harmless repository
read with `--mode production --secret GITHUB_TOKEN` before enabling writes.
Do not reuse the test repository name or a token with both environments' grants.

## Bounded E2E and cleanup

In the test repository only, create an issue titled `ZEO TEST <run-id>`, read it
back and check its exact title/body, update it and read it again, then close it.
For PR tests, use a disposable branch and PR inside the test repository; verify
head/base explicitly and close the PR without merging. Delete only the branch
created by that run. Record repository and object IDs, never token contents.
The integration's unit tests use controlled HTTP responses; this account exercise
is the separate live check.

## Troubleshooting and rotation

A 401 means expired/revoked/incorrect credentials. A 403 can mean token permission,
organization approval, SSO or rate limiting; inspect the returned status and the
token's access page. A 404 can mask an inaccessible private repository. A fine-grained
token can remain pending organization approval even after it was successfully created.
Repair the selected grant instead of trying the production key in a test run.

Create a replacement token with the same intended scope, update its namespace,
restart and rerun the repository read, then revoke the old token in Developer
settings. For host-issued installation tokens, repair the host's refresh mechanism.

## GitHub App installation tokens (for an isolated principal)

`zeo_core.integrations.github.app_tokens` mints installation tokens for a
principal that holds its own GitHub App key, such as the merger. Under the
operator's ruling the merger is a zeocore app, and its key is kept outside
ZEOconnect. Install the `github-app` extra.

- `InstallationTokens(app_id=..., installation_id=..., key_source=...)` signs
  the App's RS256 JWT and exchanges it for a token scoped to exactly one
  repository and exactly the permissions asked for. GitHub's answer is checked
  to be no broader (only its implicit `metadata: read` is allowed), and a
  broader token is revoked at once.
- The request is sent once and never retried. A dropped answer is
  `ambiguous`: any token issued expires unused within the hour.
- The key comes only from an `AppKeySource`. `KeychainAppKeySource` reads the
  running user's own Keychain item, so run it as the merger's own OS user. It
  never reads the key from the environment or a file in a repository.

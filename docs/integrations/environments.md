# Test and production environments

**Reviewed:** 2026-09-09. Applies to every integration in the [setup index](README.md).

Use one application process per environment. The same application runs in either
track; the launcher supplies the chosen credentials, config and working directory.
No provider credential is obtained by setting `mode=test` alone.

## Test track

Choose one of these two explicit test backends:

| Backend | Command | What it proves |
|---|---|---|
| Live test account | `--mode test` | Real provider behavior against the account/resources you provisioned |
| Fixture | `--mode test --fixture` | Your application's behavior with explicitly injected test transports/services |

Live test accounts can send real mail, publish public posts and incur charges.
Where no vendor sandbox is available, use the dedicated test account or isolated
resources described in that provider's guide. Never point the test credential at
your production newsletter audience just to make a check pass.

Fixture mode supplies no provider environment credentials and uses its own state
directory. It does not invent a successful response, implement a universal mock
of every API, or block network syscalls. Your application must inject controlled
clients/transports, as the supplied offline examples and test suites do. A missing
fixture fails normally. Use network-denied CI/container infrastructure when you
need an operating-system guarantee of no egress.

## Production track

Use `--mode production`, production credentials, and a separately reviewed
production resource configuration. `--fixture` is rejected with production mode.
There is no fallback from a missing test key to a production key, from a missing
production key to a test key, or from either namespace to a bare ambient key.
For managed LLM services, missing credentials or SDK failures also cannot select
another provider or a mock. Production is an explicit launch decision each time.

## Install and prepare

Follow the repository [quickstart](../../QUICKSTART.md) to use final Python 3.12
and an activated environment. Install only the optional extras named in your
provider's guide. The base package includes the launcher, HubSpot and Kit clients.
From a source checkout, `uv pip install -e .` installs the current code.

Choose a state root outside the repository. In an activated shell:

```bash
export ZEO_ENV_ROOT="$HOME/.local/share/zeocore-environments"
python -m zeo_core.integrations.environments --mode test   --root "$ZEO_ENV_ROOT" --integration kit.marketing --prepare
python -m zeo_core.integrations.environments --mode production   --root "$ZEO_ENV_ROOT" --integration kit.marketing --prepare
```

`--prepare` creates directories and an empty `config/integrations.yaml`, prints
only non-secret locations, and makes no provider request. Running it again keeps
your existing configuration. It refuses symlinks that join environment directories.

```text
<root>/test/config/integrations.yaml
<root>/test/credentials/
<root>/test/work/                     # app CWD and relative outputs
<root>/test/tmp/
<root>/production/config/integrations.yaml
<root>/production/credentials/
<root>/production/work/
<root>/production/tmp/
<root>/fixtures/test/...             # separate fixture caches and outputs
```

Google and Bluesky use credential files under the selected `credentials/` tree.
ZEOconnect uses a distinct Keychain service for each resolved state root and mode.
Managed runs do not import old Google tokens, shared user config or the old hosted
session automatically. Config and credential path overrides must remain inside
the selected state directory, including after resolving symlinks. Copy the correct
new client-secret file into that directory; never move a production refresh token
into the test track. `HOME` is preserved, not redirected.

## Supply keys without putting them in command history

Each guide tells you exactly where to obtain the credential. The launcher can
prompt for it without echoing it:

```bash
python -m zeo_core.integrations.environments --mode test   --root "$ZEO_ENV_ROOT" --integration kit.marketing --secret KIT_API_KEY   -- python /absolute/path/check_kit.py
```

At the prompt paste the **test account** key. The key goes only to the child
process; the launcher does not save it, print it, or modify the parent shell.
Use an absolute script path because the child's working directory changes.
`python -m your_installed_app` also works. Commands run without a shell, and the
application's exit status is propagated. Inspect a nonzero exit instead of
reporting a failed provider check as success.

For unattended runs, use your secret manager/CI environment to supply prefixed
variables. `--secret` prompts only when the selected variable is missing. Do not
use interactive prompts in CI; missing credentials should fail. Examples:

| Parent variable | Child receives in the selected track |
|---|---|
| `ZEO_TEST_KIT_API_KEY` | `KIT_API_KEY`, test only |
| `ZEO_PRODUCTION_KIT_API_KEY` | `KIT_API_KEY`, production only |
| `ZEO_TEST_HUBSPOT_ACCESS_TOKEN` | `HUBSPOT_ACCESS_TOKEN`, test only |
| `ZEO_PRODUCTION_NOTION_TOKEN` | `NOTION_TOKEN`, production only |

Apply the same prefix to every variable listed in the provider guides. When both
namespaces contain the same secret value for a provider variable, launch is refused.
Identical Bluesky service URLs are
allowed because different accounts can share that public service. These checks
cannot generally detect two different keys for the same production account.
Check account identity and resource access in the provider UI.

Repeat `--integration` for a process using several providers. Only those providers'
allowed variables are forwarded. Bare keys, opposite-mode keys, ambient proxies,
`PYTHONPATH`, and unrelated provider variables are not inherited. Ambient netrc
lookup is disabled so `requests` cannot replace an explicit provider token with
credentials from `~/.netrc` or a parent `NETRC` override. Local converter
settings such as `ZEO_TEST_ZEO_PANDOC_OUTPUT_DIR` become `ZEO_PANDOC_OUTPUT_DIR`.
Other application settings belong in the selected non-secret config or command
arguments. The launcher does **not** parse `.env` files or load arbitrary shell files.

## Configuration and application code

Edit the selected `config/integrations.yaml`; keep credentials out of it. It uses
the existing provider sections (`notion`, `llm`, `integrations.google`,
etc.) shown in the individual guides. Store resource IDs and expected account
labels separately in a mode-specific file such as `config/scenario.json`, and
have your application read it explicitly. Resource IDs do not magically change
when a key changes. Keep test and production recipients, repositories, calendars,
databases and output paths separate.

The Python launch API is useful to ZeoCreator or another host:

```python
import sys
from pathlib import Path
from zeo_core.integrations.environments import IntegrationEnvironment

environment = IntegrationEnvironment(
    mode="test",
    root=Path.home() / ".local/share/zeocore-environments",
    integrations=("hubspot.marketing", "kit.marketing"),
)
raise SystemExit(environment.run([sys.executable, "/absolute/path/app.py"]))
```

Inside the child, `integration_mode()` and `integration_backend()` from
`zeo_core.integrations.environment` expose the selected track. Include those labels
in your own E2E receipts, together with non-secret account/resource identifiers,
operation, result and cleanup evidence. Client construction/initialization,
provider acceptance and final delivery/render are different facts.

Applications that instantiate integrations directly without this launcher retain
the historical environment/config behavior. They must migrate their entry point
to the managed launcher to obtain these guarantees. Do not switch modes inside
one process or reuse an initialized service across modes. This launcher is an
operational boundary for trusted application code, not an OS sandbox: explicitly
opening an outside file, using arbitrary subprocesses or hardcoding production
credentials/resources can defeat application-level separation.

Third-party SDK default credential chains can also read files under the preserved
`HOME`, including in fixture mode. Dropping `GOOGLE_APPLICATION_CREDENTIALS` from
the child environment does **not** disable Google Application Default Credentials:
on Linux/macOS the SDK can still discover
`~/.config/gcloud/application_default_credentials.json`, and on cloud hosts it
may use an attached service account. See the
[Google ADC search order](https://docs.cloud.google.com/docs/authentication/application-default-credentials).
AWS SDKs can automatically load `~/.aws/credentials` and `~/.aws/config`; see the
[AWS shared-file locations](https://docs.aws.amazon.com/sdkref/latest/guide/file-location.html).
The launcher does not relocate or disable those third-party SDK stores. When your
application uses such SDKs directly, pass the selected credentials explicitly and
avoid default credential discovery. For E2E or fixture runs requiring stronger
isolation, use a dedicated host/container with no ambient production credentials
or production cloud identity, and deny network access for offline fixtures. Mode
selection alone does not provide that isolation.

## Offline and E2E checks

From the repository, run `make verify` for the complete gate. For a simulated
marketing workflow under fixture mode, replace `/absolute/path/zeocore` below:

```bash
python -m zeo_core.integrations.environments --mode test --fixture   --root "$ZEO_ENV_ROOT" --integration kit.marketing   -- python /absolute/path/zeocore/examples/kit_usage.py
python -m zeo_core.integrations.environments --mode test --fixture   --root "$ZEO_ENV_ROOT" --integration hubspot.marketing   -- python /absolute/path/zeocore/examples/hubspot_usage.py
```

The examples explicitly print `SIMULATED`. Every integration also has provider
unit/contract tests under `tests/test_integrations`; these do not certify a live
account. Follow the provider guide for a real account check, then a bounded E2E
operation and cleanup. Tests must fail if the expected effect did not occur.

## Promotion, troubleshooting and recovery

Promote application code and reviewed templates; provision production credentials
and IDs independently. Start with the production guide's harmless check. Review
any live mutation using the existing capability confirmation/evidence contract.
Never copy token caches or test recipients wholesale into production configuration.

If a key is reported missing, verify the prefix, selected `--integration`, and the
provider's exact variable name. If OAuth opens the wrong account, stop the flow,
select the intended browser profile and inspect the selected token path. If config
is ignored, verify the printed state directory and section name; a `.env` file alone
has no effect. A 401 requires credential repair; a 403 usually requires scope,
resource sharing or account entitlement repair. Neither is fixed by changing mode.

Rotate a key in its provider dashboard, update only that namespace, restart the
application and rerun the harmless check. Revoke the old key after validating the
replacement. Before revocation, cancel/reconcile queued effects using the provider
where applicable; stopping the local process does not undo accepted external work.

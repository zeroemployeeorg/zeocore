# LLM provider account setup

<!-- Teaches CLAUDE.md Rev 17; verified against provider documentation 2026-09-08. -->

Install the provider dependencies from the activated source checkout with
`uv pip install -e ".[llms]"`.

The `llms` integration supports OpenAI, Anthropic, local Ollama, and an explicit
mock fixture. Start with [environment setup](environments.md). Managed live
runs initialize only the chosen provider and fail if its credentials/client
are unavailable. They do not silently use another provider or a mock. Provider
selection and model compatibility still belong to your application.

## OpenAI test account and key

1. Sign in to the [OpenAI API platform](https://platform.openai.com/). Select
   the organization and create/select a project dedicated to ZeoCore testing.
   Arrange API billing/credits and model access; a ChatGPT login alone is not
   a successful API credential check.
2. In that project's [API keys](https://platform.openai.com/api-keys), create
   a secret key for the application or a project service account. Restrict
   it to the endpoints the application uses and copy the secret when shown.
   Keep it in your secret manager, not the source tree.
3. Choose a model your project can access that supports this adapter's Chat
   Completions interface. Set a small output limit for the first check.
   Use the key at the `OPENAI_API_KEY` secure prompt below. In CI its name is
   `ZEO_TEST_OPENAI_API_KEY`. An organization selector, when necessary, is
   `ZEO_TEST_OPENAI_ORG_ID`; it is not a substitute for a project key.
   [Official key quickstart](https://developers.openai.com/api/docs/quickstart).

## Anthropic test account and key

1. Sign in to the [Claude Console](https://platform.claude.com/). Under
   **Settings → Workspaces**, create/select a workspace for ZeoCore tests.
   Have an administrator grant access if these controls are unavailable.
2. Open **Settings → API keys → Create Key**, give it a descriptive test name,
   choose the intended user/service account, and scope the key to that test
   workspace. Save the secret securely and arrange billing/model access.
   This adapter does not provide a separate workspace-header setting: use a
   workspace-scoped key, not a new unscoped organization key that requires
   such a header. [Official authentication guide](https://platform.claude.com/docs/en/manage-claude/authentication).
3. Use it at the `ANTHROPIC_API_KEY` prompt. CI uses
   `ZEO_TEST_ANTHROPIC_API_KEY`. Choose an accessible Messages-compatible
   model ID from your account, rather than copying an obsolete model name.

Both providers' test calls are real, billable requests. Separate projects or
workspaces supply the logical test boundary; this guide does not claim free
sandbox API keys. Use synthetic content and a small dedicated budget.

## Complete live E2E check

Save as `check_llm.py`. Pass the provider and actual model ID as arguments.
The example checks a real response without printing prompt content or keys.
A successful client initialization alone does not prove the API key works.

```python
import sys
from zeo_core.integrations.llms.service import LLMIntegration
from zeo_core.integrations.llms.models import ChatMessage, LLMOptions

provider, model = sys.argv[1:3]
service = LLMIntegration(provider=provider, model=model, enable_fallback=False)
initialized = service.initialize()
assert initialized.success, initialized.message
result = service.chat(
    [ChatMessage(role="user", content="Reply with the word ready.")],
    LLMOptions(max_tokens=32, retry_count=0),
)
assert result.success, result.message
assert result.content is not None
assert not service.is_using_mock, "This is a live qualification"
print("Selected live provider returned a response")
```

```bash
python -m zeo_core.integrations.environments --mode test   --root "$ZEO_ENV_ROOT" --integration llms --secret OPENAI_API_KEY --   python /absolute/path/check_llm.py openai YOUR_CHAT_COMPLETIONS_MODEL
```

For Anthropic replace the prompt with `--secret ANTHROPIC_API_KEY` and the two
script arguments with `anthropic YOUR_MESSAGES_MODEL`. In provider usage logs,
verify the request belongs to the test project/workspace. An unsupported model
or invalid key must produce a failure, not a plausible mock answer.

## Ollama test track

The local Ollama server does not require an API key on localhost. Install the
server from [Ollama](https://ollama.com/download), start it, and pull the chosen
model with `ollama pull MODEL_NAME`. Confirm it appears in `ollama list`.
Use the actual installed model name in the script. This local adapter does
not implement Ollama Cloud credential setup.
[Official authentication behavior](https://docs.ollama.com/api/authentication).

For separate test and production servers, run the test server bound to a
loopback port such as 11435 (`OLLAMA_HOST=127.0.0.1:11435 ollama serve`), and
configure only the test mode's `config/integrations.yaml`:

```yaml
llm:
  default_provider: ollama
  ollama:
    api_base: http://127.0.0.1:11435
    default_model: YOUR_INSTALLED_MODEL
```

Pull/list models against that server using the same `OLLAMA_HOST` setting.
Ollama server environment variables configure the separately started server;
they are not forwarded as provider keys by ZeoCore. Then run the check with
`--mode test --integration llms`, no secret prompt, and arguments
`ollama YOUR_INSTALLED_MODEL`. Bind production to its independently managed
address and set that address only in production YAML. Separate model storage
can be configured with `OLLAMA_MODELS` for each server.
[Ollama server settings](https://docs.ollama.com/faq).

## Offline fixture track

Use `--mode test --fixture --integration llms` with a script that explicitly
constructs `LLMIntegration(provider="mock")`. Call `initialize()` and `chat()`
and assert `is_using_mock` is true. No provider key is needed or forwarded.
This tests your application plumbing, not provider availability or output
quality. The live-check script above deliberately refuses to count mock output
as a live result. Fixture state lives under `fixtures/test`.

## Production account and key

For OpenAI create/select a separate production project, configure its members,
limits and application key, and supply `ZEO_PRODUCTION_OPENAI_API_KEY` (plus
`ZEO_PRODUCTION_OPENAI_ORG_ID` only if needed). OpenAI explicitly recommends
separating staging and production projects.
[Production guidance](https://developers.openai.com/api/docs/guides/production-best-practices).

For Anthropic create/select the production workspace, create its own
workspace-scoped application key, and use `ZEO_PRODUCTION_ANTHROPIC_API_KEY`.
Configure workspace spending/rate controls in the console; confirm which
controls are enforced limits versus alerts.
[Workspace administration](https://platform.claude.com/docs/en/manage-claude/workspaces).

Run the same check with the production key and production model before enabling
the agent's workload:

```bash
python -m zeo_core.integrations.environments --mode production   --root "$ZEO_ENV_ROOT" --integration llms --secret OPENAI_API_KEY --   python /absolute/path/check_llm.py openai YOUR_PRODUCTION_MODEL
```

Use Anthropic substitutions as above, or no key for your managed local Ollama
server. Production requires a real provider; `mock` is refused. Inspect usage
attribution, application outputs, latency and failure handling before increasing
volume. Keep the test fixture and live account checks as separate CI jobs.

## Cleanup and credential repair

Delete disposable provider artifacts if your application created any and retain
only sanitized test receipts. Revoke temporary keys in the provider's API-key
screen. For rotation create the replacement in the same intended project or
workspace, update that mode's secret, run a small check, then revoke the old
key. On 401 stop and correct the key; on 403 check project/workspace permissions
and model entitlement; on 429 inspect quota/rate limits before retrying. Never
fall back to a production key to make a test pass.

## Hosted chat through ZEOconnect (draft)

!!! warning "Draft"
    This follows ZEOconnect's proposed billed LLM chat contract (draft 3),
    which isn't frozen yet. Streaming isn't in the hosted client yet.

`zeo_core.integrations.llms.hosted` sends chat through the ZEOconnect Broker,
which holds the OpenAI, Anthropic or Nebius key. zeocore never sees it.

- **`HostedLLMChat.send(operation, body_bytes, occurrence=...)`** sends the
  provider's own request bytes exactly as given, never re-serialized, so key
  order and number formatting survive. It answers the provider's exact response
  bytes and the Broker's receipt: model requested and reported, usage, cost,
  terminal state, and whether it was a replay.
- **`HostedChatOnce`** implements `chat_once` for OpenAI Chat Completions or
  Anthropic Messages, building the body from `ChatMessage` and `LLMOptions`.
  `max_tokens` is required, because it bounds the call's reservation. Anything
  the contract can't carry (tools, penalties, OpenAI stop sequences, two system
  messages, a different model) is refused, never dropped.
- **The request identity** is the exact bytes plus an `occurrence` label. Pass a
  stable occurrence, such as a record id, and a resume after a crash replays the
  stored outcome instead of billing again.
- **Failures** raise `HostedLLMError`, with an `outcome` (`ambiguous`,
  `in_flight`, `unavailable`, `budget_exhausted`, `stopped`, `refused`,
  `not_paired`, `invalid_response`) and a `retry` hint (`same_request`,
  `new_occurrence`, `none`).

From any language: `zeocore llm <operation> --connection C [--occurrence L]
[--out PATH] < body.json` forwards stdin's exact bytes. See
[the zeocore command](../reference/cli.md).

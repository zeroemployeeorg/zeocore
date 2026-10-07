# Integration account setup

<!-- Teaches CLAUDE.md Rev 17; user documentation reviewed 2026-09-09. -->

Start with [test and production environments](environments.md). It explains the
launcher, secure key prompts, file locations, offline fixtures, and promotion.
Then follow your provider's complete account guide. These instructions target
ZeoCore 0.11.0 or newer. Earlier wheels do not include this complete surface.

| Integration ID | Get credentials and configure accounts | Test track |
|---|---|---|
| `hubspot.marketing` | [HubSpot](hubspot.md) | Developer test account; restricted recipients |
| `kit.marketing` | [Kit](kit.md) | Dedicated creator account and designated subscribers |
| `github` | [GitHub](github.md) | Separate token limited to a disposable repository |
| `google.mail` | [Gmail](google.md#gmail) | Dedicated Google user and seeded mailbox |
| `google.drive` | [Drive](google.md#drive) | Dedicated user and test folder/files |
| `google.calendar` | [Calendar](google.md#calendar) | Dedicated user and secondary calendar |
| `google.docs` | [Docs](google.md#docs) | Dedicated user and disposable document |
| `google.sheets` | [Sheets](google.md#sheets) | Dedicated user and disposable spreadsheet |
| `google.slides` | [Slides](google.md#slides) | Dedicated user and disposable presentation |
| `google.youtube` | [YouTube](youtube.md) | Dedicated test channel; uploads stay private until checked |
| `notion` | [Notion](notion.md) | Test workspace or separately shared test parent |
| `supabase` | [Supabase](supabase.md) | Local stack or separate hosted project |
| `social.bluesky` | [Bluesky](bluesky.md) | Dedicated account; posts remain public |
| `llms` | [OpenAI, Anthropic, Ollama and mock](llms.md) | Separate provider project/workspace or local model; explicit fixture |
| `pandoc` | [Pandoc](local-tools.md#pandoc) | Local sample documents and separate output directory |
| `ffmpeg` | [FFmpeg](local-tools.md#ffmpeg) | Local synthetic media and separate output directory |
| `jupytext` | [Jupytext](local-tools.md#jupytext) | Local sample notebooks and separate output directory |
| `revolut.business` | [Revolut Business](revolut-business.md) | Separate Revolut sandbox account and its own private files |
| `zeoconnect` | [Hosted connection and pairing](zeoconnect.md) | Fake service or separately paired test identity/resources |
| `gemini.images` | [Gemini keys and host custody](gemini-images.md) | Offline request/provider fixtures or separate Google project |
| `notebook` | [Notebook execution](notebook-execution.md) | Local synthetic notebook; no account or key |

The first seventeen rows are package entry points. ZEOconnect, Gemini images and
notebook execution are additional composition APIs. Their launcher selections
forward no provider keys: Gemini uses explicitly provisioned host custody and
notebook execution is local. The `database.sqlite` and `database.bigquery` directories
are placeholders with no implemented service or credential workflow; they are
not supported integrations. HTTP/MCP are adapters for exposing capabilities,
not additional provider accounts. Future integrations must join this inventory
and provide both tracks before their setup is considered complete.

Test mode is a ZeoCore environment selection. It does not turn an ordinary
provider account into a vendor sandbox. Provider credentials, account permissions,
selected resources and recipients still determine what the provider can change.

For staged notebook/document releases, use [authoring conversion receipts](authoring-receipts.md).

For runtime-admitted meeting actions, use the
[meeting adapters](meetings.md).

Trusted notebook execution is a separate [optional local API](notebook-execution.md).

Run the [complete authoring reference](authoring-reference.md) for a staged, independently checked release.

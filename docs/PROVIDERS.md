# AI providers and free-tier limits

Start with **Groq + Cloudflare + OpenRouter**. No models are downloaded to your VPS.
Keys stay in the private configuration file and are never shown in Signal commands.
Public RSS text, your focus, and recent bot-generated headlines are sent to the
selected provider. Ordinary group conversation is not sent to an AI provider.

**Zero cost requires free-tier accounts with paid billing disabled.** The bot restricts
OpenRouter to zero-priced routes, limits Cloudflare and Z.ai to approved model IDs,
and checks Requesty's live zero-price catalog. These controls do not prevent a
provider billing an account you upgraded to paid. Do not enable top-ups or a paid
gateway policy. Free quotas change; none are infinite.

## Recommended signups

### Groq

1. Sign up at https://console.groq.com/keys.
2. Create an API key named `signal-news-bot`; copy it somewhere private.
3. Keep the **Free** plan. In the bot dashboard, edit `groq-20b`, paste the key, save.
4. `groq-120b` can reuse that same provider key. Shared keys do not create extra quota.

Default IDs: `openai/gpt-oss-20b`, `openai/gpt-oss-120b`. Additional IDs can be added
as aliases if listed in your account's current model catalog. Groq's current public
rate-limit table also lists `qwen/qwen3.8-27b`. Test any new model in preview first.

Reference: https://console.groq.com/docs/rate-limits

### Cloudflare Workers AI

1. Create an account at https://dash.cloudflare.com/sign-up/workers-and-pages.
2. Open **Workers AI → Use REST API → Create a Workers AI API Token**.
3. Copy **both the API token and Account ID**.
4. Paste both into `cloudflare-gemma` on a fresh install. Stay on **Workers Free**.
   If you upgraded, your existing aliases and credentials are unchanged.

The official template grants the necessary Workers AI permissions. If making a
custom token, both **Workers AI Read and Edit** are required. Do not use a global
API key. No Worker deployment, domain purchase, or local Cloudflare runtime is needed.

Cloudflare currently provides 10,000 neurons/day across the account. Different models
consume different amounts. The bot does not claim to know remaining neurons; it shows
"provider quota: not reported" plus its separate local request cap. Other allowlisted
options include `@cf/meta/llama-3.1-8b-instruct-fp8-fast` and small Llama variants.

References:
- https://developers.cloudflare.com/workers-ai/get-started/rest-api/
- https://developers.cloudflare.com/workers-ai/platform/pricing/

**2026-11-20 compatibility note:** Cloudflare retired Qwen3 30B A3B FP8.
`@cf/google/gemma-4-26b-a4b-it` is the starter ID for
fresh installs. `@cf/openai/gpt-oss-20b` and `@cf/openai/gpt-oss-120b` are also
listed in Workers AI pricing and on the bot allowlist. All share the same
Workers Free allowance; different token costs mean different capacity.
Clarity holds the retired route instead of retrying it. For an existing
`cloudflare-qwen`, edit its model ID without changing the alias or key, then
run `newsbotctl test-ai cloudflare-qwen` while paused. The actual generating ID
is recorded separately from the alias.

### OpenRouter

1. Sign up at https://openrouter.ai/settings/keys.
2. Create a key named `signal-news-bot`; copy it privately.
3. Enter it in `openrouter-free` in the dashboard. **Do not top up** for a strict $0 setup.
4. Use `/catalog` or **Browse current OpenRouter free IDs** to find additional IDs.
   Add an alias in the dashboard, then use `/model alias` in the group.

Only `openrouter/free` and IDs ending in `:free` are allowed. Catalog listings must
also have zero input/output prices. Availability changes; catalogs are cached for
five minutes to avoid unnecessary API traffic. Specific free models may require
accepting their data-use conditions. Review them; never send sensitive information.

The published basic allowance is currently 50 free-variant requests/day below the
credit-purchase threshold. All free aliases share account-wide quota. Adding 14
aliases does not turn 50 requests into 700. The free router can choose different
underlying models; each article retains its original model provenance.

A live catalog check on 2026-11-19 included NVIDIA Nemotron 3.5 Lightning, Google
Gemma 4 26B/31B, inclusionAI Ling 3.2 Flash and LiquidAI LFM3 1.7B free variants.
These are examples, not promises of serving capacity. NVIDIA model weights served
through OpenRouter are a different offering from NVIDIA's own prototyping API.

References:
- https://openrouter.ai/docs/guides/routing/routers/free-router
- https://openrouter.ai/docs/api/reference/limits
- https://openrouter.ai/api/v1/models

## Optional providers supported by this release

### Requesty

Make a free account and key at [Requesty](https://app.requesty.ai/). Do not add
credits, enable auto recharge, or use a gateway fallback policy. In the bot's
dashboard add provider `requesty` with exact model `google/gemma-4-31b-it`,
`novita/ling-3.1-flash`, `nvidia/nemotron-3.5-lightning-30b-a3b`, or
`nvidia/nemotron-3-ultra-550b-a55b`. Start with JSON mode unchecked and a local
cap below the shared provider cap. New accounts have a published shared limit
of 200 free calls/day and 20/min across free models combined. The provider's
catalog and model data terms can change. The bot skips models it cannot verify
as zero-priced and blocks a route that does not confirm zero cost.

References: [free models](https://docs.requesty.ai/features/free-models) ·
[model catalog](https://docs.requesty.ai/api-reference/endpoint/models-list) ·
[terms](https://www.requesty.ai/terms)

### Z.ai

Use a standard model API key from the [Z.ai dashboard](https://z.ai/manage-apikey),
not a coding subscription key. In the bot's dashboard add `zai` and
`glm-4.7-flash` (first candidate) or `glm-4.5-flash`, with JSON mode unchecked for
the initial test. The official pricing lists these exact text models free,
**not** FlashX, GLM-5.3-Flash, or paid tools. Check current model access, limits
and data terms in your account; no remaining balance is assumed.

References: [pricing](https://docs.z.ai/guides/overview/pricing) ·
[model docs](https://docs.z.ai/guides/llm/glm-4.7)

### Gemini

Use an eligible free-tier AI Studio project without paid billing, and a text model
currently included in that project's free allocation. Add provider `gemini`, its
exact model ID and API key in the dashboard. Availability, regional restrictions,
quotas and data-use terms apply. In particular, check the current terms if users
are in the EEA, Switzerland or UK. No fixed remaining-quota figure is invented.

On 2026-11-20 Google's published pricing lists `gemini-3.8-flash` and
`gemini-3.1-flash-lite` with free standard text input/output. These are candidates for a preview comparison, not
proof of account-level access, enough quota, or better editorial decisions.
Understand the applicable data-use terms before sharing editorial instructions.
No provider or preference is enabled automatically by upgrading.

References: https://aistudio.google.com/api-keys · https://ai.google.dev/gemini-api/docs/pricing ·
https://ai.google.dev/gemini-api/terms

### NVIDIA — preview/evaluation only

Create a developer account/key at https://build.nvidia.com/, then edit and enable
`nvidia-test`. No GPU or NVIDIA software is needed on the VPS. NVIDIA advertises
rate-limited free prototyping, up to 40 RPM for most models, with personal limits
in the dashboard. Its official FAQ restricts this free offering to evaluation/
prototyping, not activity serving real end users. Accordingly this integration is
allowed **only in preview**, and NVIDIA-generated previews cannot be published live.

Reference: https://docs.api.nvidia.com/nim/docs/product

### SambaNova — verify your entitlement before relying on it

Add provider `sambanova`, a key and an exact ID such as `gpt-oss-120b` or
`Meta-Llama-3.3-70B-Instruct`. Its rate-limit docs list a no-payment-method free tier
at 20 requests/day/model and 200,000 tokens/day, but its plans page says to add a
payment method and purchase credits before first requests. These public pages
conflict. Confirm actual free API access in your account **without paying**.

References: https://docs.sambanova.ai/docs/en/models/rate-limits · https://cloud.sambanova.ai/plans

## Other offers checked, but not recommended as recurring live capacity

Checked against official pages on 2026-11-18–20:

| Service | Finding |
|---|---|
| Cerebras | Limited $5/30-day trial; not a renewing always-free tier. |
| Hugging Face | Current pricing lists no included inference credits for free users. |
| Mistral | Current billing docs exclude API access from free mode; older help articles conflict. |
| Ollama Cloud | Current pricing says pay-as-you-go per token; older cloud/blog pages still advertise starter credits. No confirmed recurring allowance to depend on. |
| Cohere | Free evaluation keys, limited to 1,000 calls/month; paid production keys. |
| Hetzner inference | Experimental, free while experimental; official page says not for production. |
| SiliconFlow | Some zero-priced models on the China service, with regional/identity-verification restrictions. Do not assume the global service has identical free prices. |
| Together AI | The serverless catalog is billed per token; no recurring free allowance identified in the official docs. |
| Alibaba Cloud Model Studio | Eligible China-region models may have a temporary 90-day free quota, not a permanent live fallback. |
| GitHub Models | GitHub retired the inference API on July 30, 2026. |

Sources:
- https://inference-docs.cerebras.ai/support/rate-limits
- https://huggingface.co/docs/inference-providers/pricing
- https://docs.mistral.ai/admin/billing-usage/usage-limits
- https://ollama.com/pricing
- https://docs.cohere.com/docs/rate-limits
- https://docs.hetzner.com/general/company-and-policy/experiments/inference/
- https://docs.together.ai/docs/serverless/overview

## Relay research: additional zero-cost candidates

Checked public terms and pricing on 2026-11-23. Relay does **not** enable new
inference providers or change the model order. A synthetic completion doesn't
prove reliable full-batch editorial output, account access, or permission for
production use. Check those and data handling in preview before enabling any adapter.

| Candidate | Finding |
|---|---|
| [OVHcloud AI Endpoints](https://docs.ovhcloud.com/en/guides/public-cloud/ai-machine-learning/ai-endpoints-capabilities) | Best first candidate. Anonymous requests have a published limit of 2/minute per IP and model. Test `gpt-oss-120b` on full synthetic batches. Keyed endpoints are metered; don't fall back to them for a $0 setup. |
| [Kilo Gateway](https://kilo.ai/docs/gateway/usage-and-billing) | Explicit `:free` routes, 200 requests/hour per IP across free calls, anonymous or authenticated. Catalog and upstream capacity change. BYOK at $0 on Kilo can still bill the upstream account. |
| [Synap](https://linkrra.com/ai/guides/switch-from-openrouter.html) | Documents a no-card signup on its gateway. Verify the current ongoing entitlement, pricing and data terms before relying on it; not tested here. |
| [AIHubMix](https://aihubmix.com/call/free_quota_config), [Scaleway](https://www.scaleway.com/en/pricing/model-as-a-service/) | AIHubMix's recurring allowance requires a top-up after a limited trial; Scaleway's first million tokens are a one-time allowance. Neither is dependable recurring capacity at strict $0. |

### Keys, accounts and 429s

Extra keys for the same account aren't extra capacity. Groq scopes limits to
the [organization](https://console.groq.com/docs/rate-limits), Gemini to the
[project](https://ai.google.dev/gemini-api/docs/rate-limits), Cloudflare
shares neurons across the account, and OpenRouter [says extra accounts or keys
won't increase limits](https://openrouter.ai/docs/api-reference/limits).
Don't rotate accounts or identities to defeat a provider limit. Separate keys
are useful for isolation and revocation where the provider permits them.

`429` can mean a minute/day request bucket, a token limit, or busy upstream
capacity, even with local attempts remaining. Respect the provider cooldown,
skip obvious rewrites before analysis, and prefer an independent free provider
when more capacity is needed. Pausing or waiting is safer than paid fallback.

### Optional search for `/expand`

[Tavily's Researcher plan](https://docs.tavily.com/documentation/api-credits)
documents 1,000 credits/month with no card. Keep billing and automatic
overage off, and use a dedicated key if practical. The bot sends one basic
public-news query, does not use Tavily's generated answer, and caps search
attempts separately from inference. Review [Tavily's terms](https://tavily.com/terms)
before enabling this optional feature. A free text model is not necessarily a
free search tool: Gemini 3.5 Flash-Lite's free API tier doesn't include
[Search grounding](https://aistudio.google.com/docs/pricing).
- https://help.aliyun.com/en/model-studio/new-free-quota
- https://docs.github.com/en/github-models

We deliberately do not add dubious proxies, duplicate-account quota bypasses, or
paid fallbacks disguised as "free". Additional legitimate provider adapters can be
added in future releases without replacing your existing keys or subscriptions.

## Compare editorial quality before switching

Keep the same focus, exclusions and threshold; send no live posts during the
comparison. Try a small sample of equivalent headlines, distinct follow-ups and
off-focus items against Groq GPT-OSS 120B and one candidate: Workers AI Gemma 4
26B, Gemini 3.8 Flash, Requesty Gemma 4 31B, or Z.ai GLM-4.7-Flash. A successful
`newsbotctl test-ai` proves one valid response, not
editorial quality. Compare how many rewrites it would post, how many distinct
updates it misses, and whether the summaries preserve attribution. Track retries
and token limits, too; the extra recent-story context raises prompt input.

## What a quota footer means

`187/900 calls` means 187 attempts remain out of the bot's 900-attempt daily cap for
the alias, not your actual provider balance. `/usage` shows that counter as bars
in a single message, with local caps, cooldowns and retired routes marked.
Use `/usage details ALIAS` or the dashboard for provider snapshots, resets and
attempt types. Failed attempts are counted within those categories, not extra requests.

### When capacity resets

| Counter | Schedule shown by `/usage` |
|---|---|
| Bot local daily attempts (all aliases) | 00:00 UTC daily. Does not reset a provider quota or clear the bot’s failure cooldown. |
| Groq | Use response `x-ratelimit-reset-requests` (daily request bucket) and `x-ratelimit-reset-tokens` (tokens per minute). Do not assume the daily reset time is midnight. |
| Cloudflare Workers AI Free | Neuron allocation resets daily at 00:00 UTC. Remaining neurons are not available to this bot. |
| OpenRouter free | Free model daily requests reset at 00:00 UTC. Free routes may still be rate-limited or congested. |
| Gemini | Daily requests reset at midnight US Pacific, with daylight saving. Minute limits are separate. |
| SambaNova | The daily reset header is used if reported; no clock time is assumed. |
| NVIDIA evaluation | Rate-limited per minute; no fixed daily reset is listed. Still preview-only. |

Checked against the official [Groq headers](https://console.groq.com/docs/rate-limits),
[Cloudflare pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/),
[OpenRouter limits](https://openrouter.ai/docs/api_reference/limits),
[Gemini limits](https://ai.google.dev/gemini-api/docs/rate-limits) and optional
provider links above on 2026-11-19. Reported values are snapshots, not guarantees.

OpenRouter receives `reasoning: {effort: low, exclude: true}` and a 4096-token
completion budget to reduce reasoning-only truncation. Exclusion hides reasoning;
it does not stop its token use. Provider/model support varies, so validate with
`newsbotctl test-ai ALIAS`; 429 may be transient/shared capacity, not daily exhaustion.
Reference: https://openrouter.ai/docs/guides/best-practices/reasoning-tokens

Groq/SambaNova response headers can report remaining request buckets; OpenRouter's
key API can report an account-wide daily free bucket. Snapshot timestamps are
shown in the dashboard. Other clients may consume quota after the snapshot. `/usage`
omits unreported balances and marks expired snapshots stale.

The **local daily cap** is an attempt counter per alias, including failed calls,
resetting at UTC midnight. It is not an actual provider balance or billing guarantee.
Provider token limits or shared quotas can be exhausted first. Exhaustion queues
articles; old queued articles expire rather than flooding the channel later.

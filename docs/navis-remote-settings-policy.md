# Navis Remote Settings policy

## Decision

Navis retains Gecko's content-signed Remote Settings implementation, but it does not inherit every collection shipped or known by Firefox. Navis has an exact product allowlist. A collection absent from that list cannot be packaged, discovered by polling, read through an explicit client, or synchronized into a production Navis profile. Each readable collection also declares whether remote synchronization is permitted. This lets an engine consumer use a reviewed packaged snapshot without silently creating a new Mozilla network dependency.

The machine-readable authority is `platform/gecko-chrome/config/navis-remote-settings-policy.json`, mounted in the prepared Gecko tree as `navis/config/navis-remote-settings-policy.json`. Desktop Navis and Android Navis both select that same file with the explicit `--with-navis-remote-settings-policy` configure capability. Configure projects the parsed policy into `AppConstants`; packaging and runtime therefore consume one immutable declaration rather than repeating an editable preference. `scripts/verify-remote-settings-policy.py` rejects any drift between layers.

## Retained collections

| Collection | Bootstrap | Remote sync | Signer | Navis reason |
| --- | --- | --- | --- | --- |
| `blocklists/addons` | No | Yes | default | Legacy block decisions for reviewed application built-ins |
| `blocklists/addons-bloomfilters` | Yes | Yes | default | Current bloom-filter block decisions for reviewed application built-ins |
| `blocklists/gfx` | Yes | Yes | default | Graphics-driver and device compatibility for WebRender/WebGPU |
| `main/devtools-compatibility-browsers` | Yes | No | default | Offline browser-compatibility catalog for retained page tools |
| `main/devtools-devices` | Yes | No | default | Offline Responsive Design Mode device presets |
| `main/moz-essential-domain-fallbacks` | Yes | Yes | default | Availability fallback for the retained settings/signature infrastructure |
| `main/password-recipes` | Yes | Yes | default | Login-form site compatibility |
| `main/password-rules` | Yes | Yes | default | Site-specific generated-password constraints |
| `main/url-parser-default-unknown-schemes-interventions` | Yes | Yes | default | Known external-scheme parser compatibility and safety |
| `security-state/cert-revocations` | No | Yes | OneCRL | CRLite revocation filters and updates |
| `security-state/intermediates` | Yes | Yes | OneCRL | Intermediate CA preloading |
| `security-state/onecrl` | Yes | Yes | OneCRL | Emergency certificate revocation state |

“Default” means `remote-settings.content-signature.mozilla.org`; “OneCRL” means `onecrl.content-signature.mozilla.org`. Both retain Gecko's `p384ecdsa` verification and certificate-root enforcement. A bootstrap dump is trusted as source-controlled application data; every network update is still signature-verified before commit.

The production data endpoint is locked to `https://firefox.settings.services.mozilla.com/v1`. Production preview mode is locked off. Attachment and certificate-chain URLs are record metadata rather than independent trust roots: hashes protect attachments, while the accepted content signer and Gecko's built-in content-signature root constrain the certificate chain.

## Deliberate exclusions

The source inventory currently contains 22 other bootstrap collections. They cover Firefox AI prompts, anti-tracking and cookie-banner lists, other Firefox DevTools product data, DoH rollout/provider UI, examples, search configuration and telemetry, Firefox new-tab content, language-pack discovery, remote permissions, translations, Safe Browsing URL-classifier data and Firefox credential-realm sharing. They are not Navis dependencies:

- Navis supplies an offline search catalogue, with provider edits/defaults persisted by Gecko SearchService, and owns its new-tab surface. Optional consented provider-query suggestions do not restore Mozilla remote search configuration, search icons, region policy or experiments;
- Safe Browsing, translation and remote language-pack installation are outside the capability matrix; the two retained DevTools catalogs are packaged-only data;
- spellcheck uses packaged dictionaries rather than a remote installer;
- Navis does not grant loopback/local-network permissions from a Mozilla server; and
- uBlock Origin remains the explicit built-in content blocker;
- Clean Links consumes its complete provider-neutral packaged policy offline, so `main/query-stripping` is not retained; and
- Firefox URL-decoration referrer rewriting and cookie-banner automation are not silently added.

The exact excluded source list is recorded in the policy JSON. Server-side collections that have no source dump are denied automatically because the runtime is allowlist-based, not denylist-based.

## Enforcement layers

Semantic port 0029 adds an explicit Navis product-policy configure option. Desktop Navis and Android Navis opt into it; an omitted option preserves upstream behavior for generic Desktop Embedder and GeckoView consumers.

1. The dump timestamp generator and all dump/attachment installers filter on the configured list. A desktop archive or Android APK therefore contains ten bootstrap JSON files and only the add-on bloom-filter attachments.
2. The production client factory marks every unlisted collection disabled. Its `get()`, `getLastModified()`, `sync()` and `maybeSync()` paths return no data and do no network or database mutation. Central polling and startup- bundle import also skip it. This prevents an upgraded profile from reviving old excluded data. A readable collection with `remote_sync: false` imports its packaged dump normally but is omitted from startup bundles, central polling and explicit `sync()`/`maybeSync()` network paths.
3. Test builds bypass the product list by default so Gecko's upstream tests can create arbitrary collections. A dedicated xpcshell test forces the policy, seeds an old disallowed database and proves that reads and synchronization remain inert.

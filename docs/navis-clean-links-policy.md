# Navis Clean Links policy

## Decision

Navis Clean Links is offline-first. Enabling the setting must not contact a rule provider, wait for a first synchronization, or change behaviour because a network service is unavailable. The release package contains the complete rule set needed by copy, open, navigation and eligible redirect paths.

The machine-readable product authority is `platform/gecko-chrome/chrome/content/clean-links-policy.json`, mounted in the prepared Gecko tree as `navis/chrome/content/clean-links-policy.json`. Its envelope is deliberately provider-neutral: a schema version, policy revision, provenance, allow domains and rules with explicit scope. Gecko's packaged MPL and LGPL Strip-on-Share lists and this product envelope are normalized into one in-memory policy before any consumer receives it. `privacy.query_stripping.use_unified_rules` makes the ordinary navigation stripper consume that same normalized policy instead of a second global-only list.

The setting remains off by default because removing even a known attribution parameter can affect site behaviour. Enabling or disabling it applies immediately and does not require a page or application restart.

## Bundled provider

The active provider is `bundled`:

- the current Mozilla `main/query-stripping` global records are pinned as a reviewed 22-parameter snapshot;
- `googleadservices.com` remains exempt, matching the reviewed snapshot;
- Gecko's two source-controlled Strip-on-Share lists retain their respective licences and exact-host rules; and
- Navis adds one registrable-domain rule for Alibaba's `spm` attribution parameter.

`main/query-stripping` is absent from Navis's Remote Settings allowlist, and `privacy.query_stripping.remote_settings.enabled` is locked false. Therefore Clean Links neither constructs that collection's client nor reads stale cached records. This does not disable Remote Settings collections retained for graphics, certificate, add-on or password compatibility.

The snapshot records their upstream endpoint and retrieval date as provenance; that URL is documentation/audit input, not a runtime dependency.

## Alibaba `spm` scope

Alibaba documents `spm` as its page/slot/click attribution code. Navis strips it only when the destination's public-suffix-aware registrable domain is in the reviewed Alibaba product list. This covers subdomains such as `help.aliyun.com` without treating the generic name `spm` as a global tracker.

The scope is based on Alibaba Group's official public business destinations and directly observed product links. It deliberately excludes payment/Ant domains and infrastructure/control-plane roots such as `alipay.com`, `aliyuncs.com`, `alicdn.com`, `tbcdn.cn` and `mmstat.com`. It also does not strip `spm` from an external destination merely because an Alibaba page added it. These exclusions reduce the risk of changing payment signatures, API requests or unrelated parameters with the same name.

## Provider evolution

The runtime consumes the normalized schema, not provider-specific record objects. A later release may introduce one of these adapters:

1. a signed Mozilla Remote Settings adapter;
2. a signed Navis-hosted adapter; or
3. another reviewed service with equivalent authenticity and rollback rules.

An adapter must validate its transport, signature, schema, revision monotonicity and bounded values, then produce the same policy envelope. The bundled policy remains the startup and failure fallback. Switching providers must not create separate copy and navigation rule sets, and a failed update must leave the last accepted complete policy active. Adding such an adapter is a future explicit security decision; the policy format preserves the option without adding network traffic now.

## Acceptance

Source and native regressions must prove:

1. `fbclid` and the other pinned global parameters are removed on both copy and eligible open/navigation paths;
2. `spm` is removed from `aliyun.com` and its subdomains but retained on an unrelated registrable domain;
3. the allow domain is honored consistently;
4. disabling the product setting leaves every URL unchanged;
5. a fresh offline profile has the complete policy before its first operation;
6. `main/query-stripping` cannot synchronize or revive stale profile records; and
7. Linux and Windows packages contain the exact policy revision and pass the same behavioural cases.

Mozilla's [Query Parameter Stripping documentation](https://firefox-source-docs.mozilla.org/toolkit/components/antitracking/anti-tracking/query-stripping/index.html) remains the user-facing explanation of the underlying mechanism, not the identity of Navis's active provider.

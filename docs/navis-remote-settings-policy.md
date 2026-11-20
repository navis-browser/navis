# Navis packaged settings policy

Navis uses reviewed data snapshots installed with the application and does not synchronize them with Mozilla Remote Settings during operation. The exact collection authority is `platform/gecko-chrome/config/navis-remote-settings-policy.json`, selected by both desktop and Android configure inputs. Its collection and synchronization lists are compiled into `AppConstants`; editable Profile preferences cannot broaden them. Explicit local page translation may download only engine/model attachments named by its two packaged catalogs, using the existing downloader, mandatory size/hash checks and verified cache reuse. This exception downloads translation files; it sends no page text and permits no settings-service discovery, polling or synchronization.

Every allowed collection has `packaged_bootstrap: true` and `remote_sync: false`. The production endpoint is a non-network `data:` sentinel. Mozilla signer identifiers identify upstream provenance and preserve the upstream verification implementation/tests; they do not create a runtime dependency. Packaged dumps are trusted application source, frozen and hashed with the application, without fetching a signature certificate chain.

| Collection | Offline purpose |
| --- | --- |
| `blocklists/addons-bloomfilters` | Packaged extension block decisions and hash-checked attachments |
| `blocklists/gfx` | Graphics-driver and device compatibility decisions |
| `main/devtools-compatibility-browsers` | Browser compatibility catalog for developer tools |
| `main/devtools-devices` | Responsive Design Mode presets |
| `main/moz-essential-domain-fallbacks` | Retained inert upstream compatibility data; no settings-service fallback is permitted |
| `main/password-recipes` | Login-form compatibility |
| `main/password-rules` | Site-specific generated-password constraints |
| `main/translations-models-v2` | Fixed catalog of compatible local translation models |
| `main/translations-wasm-v2` | Fixed catalog of the compatible Bergamot/WASM engine |
| `main/url-parser-default-unknown-schemes-interventions` | External-scheme parser compatibility and safety |
| `security-state/intermediates` | Retained packaged intermediate metadata; certificate preloading downloads are disabled |
| `security-state/onecrl` | Emergency revocations, also compiled into the native TLS verifier |

Remote-only legacy add-on records and CRLite filter downloads are not selected. Navis does not claim a current CRLite filter: its download consumer and CRLite mode are explicitly disabled. Ordinary certificate-chain validation and OCSP remain available. Existing user certificates/keys and already stored intermediates are not deleted. See [certificate security and maintenance](certificate-security-maintenance.md) for first-handshake protection, CT freshness and the release update procedure.

The source inventory's other dumps and all unlisted server collections are excluded. The JSON explicitly accounts for each source dump. This includes Firefox AI/product/search/DoH/reputation data, obsolete translation catalogs and language-pack discovery. Navis's packaged search catalog, built-in blocker and optional user-selected search suggestions retain their own accepted contracts. Built-in browser spellchecking is excluded and has no dictionary-discovery service.

In production, allowed client reads use the exact packaged dump, including filters/order, instead of trusting old Profile timestamps or signature metadata. An unavailable required dump fails without network fallback. Polling and startup bundles do not run; explicit sync, server-information requests and attachment bundles are closed before creating a transport. Ordinary attachments use hash-checked packaged resources; a newer cached attachment cannot replace them. Translation byte downloads are restricted to exact catalog-owned URLs under the fixed Mozilla attachment CDN and may reuse only matching, hash-verified cached files. Disallowed clients cannot revive stale Profile data.

Translation catalog provenance is recorded in Runtime's `config/translation-assets.json`. Catalog changes require the source hash/schema/URL gate and native Mozilla content-signature verification with a negative tamper control. The application then trusts its packaged snapshot, as it does other release-owned settings; this is not a promise of live model or security-data updates. See Runtime's page translation contract for reproducible checks.

The original collection adapter and the subsequent offline adapter are independently hash-owned semantic ports. Test builds retain upstream valid/invalid-signature coverage by default. Explicit offline-policy tests exercise packaged authority, stale databases and closed low-level network entry points. Source, configure and package checks enforce the same policy on all three targets.

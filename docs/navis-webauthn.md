# Navis WebAuthn contract

## Product scope

Navis retains Gecko's standards-facing WebAuthn implementation for passkey and security-key registration and assertion. It does not reimplement CTAP, credential cryptography, RP validation, authenticator discovery or the native Windows passkey provider. Those remain Gecko responsibilities. Navis owns the browser presentation that upstream Firefox normally supplies.

The capability applies to authenticated HTTPS documents and the standard HTTP loopback development exception. Conditional mediation and related-origin requests remain enabled. Conditional mediation is consumed by Navis's bounded credential-field actor rather than the excluded Firefox Login Manager: a focused `autocomplete="... webauthn"` field is matched to Gecko's pending transaction and then projected through the same `WebAuthnDelegate`. A related-origin request must first pass Gecko's `/.well-known/webauthn` validation and then receive an explicit Navis product decision; it is never silently approved.

## Ownership boundary

| Layer | Responsibility |
| --- | --- |
| Gecko Core | WebAuthn WebIDL and algorithms, RP/origin checks, transaction lifetime, authenticator-rs, virtual authenticators and native platform providers |
| Gecko Binding | Observe backend prompt events, authenticate their live browsing context and origin, discover conditional credentials for the focused field, retain credential IDs privately, bound account/PIN metadata, and translate related-origin consent away from Firefox `PopupNotifications` |
| Session contract | Serialize WebAuthn with permission and generic prompts in one FIFO and expose only frozen language-neutral descriptors through `WebAuthnDelegate` |
| Platform | Render en-US/zh-CN product UI, collect an allowed action, clear PIN input immediately and dismiss stale presentation |

The observer may use Gecko's native transaction identifier only inside the Binding. The public descriptor never contains that identifier, a principal, browsing context, credential ID, device handle, callback or backend object. The account projection is limited to 32 entries and 256 characters per display field. PIN input is nonempty, control-free and limited to 63 UTF-8 bytes.

## Delegate and actions

`WebAuthnDelegate.onRequest()` receives a frozen descriptor containing `id`, `sessionId`, `category: "webauthn"`, `kind`, authenticated `origin`, display `host`, optional `rpId`, optional `retries`, optional `errorKind`, frozen `accounts`, and frozen `actions`.

The action vocabulary is deliberately semantic:

- direct attestation: `allow-identifying-attestation`, `continue-anonymized`, or `cancel`;
- PIN: `submit` with `pin`, or `cancel`;
- account choice: `select` with `selected`, or `cancel`;
- conditional mediation: `select` with a displayed account index, `continue` to use another authenticator, or `cancel`;
- related-origin authorization: `continue` or `cancel`;
- presence and terminal information states: `cancel`.

The privacy-preserving direct-attestation action is Platform's primary action. `continue-anonymized` asks Gecko to strip identifying attestation and continue; it is not a registration denial. Invalid, missing, thrown or rejected Delegate responses cancel the native transaction.

## Lifecycle and security invariants

- The event origin must match a live non-system, non-extension document principal in the owning browser's context tree. Related-origin prompts also match the exact originating browsing-context ID.
- A prompt cannot survive navigation, process crash, Session/View close, browser replacement or Delegate replacement. Backend-originated cancellation removes Platform UI without answering a stale transaction.
- WebAuthn uses the same per-Session FIFO as permissions, before-unload, HTTP authentication and credential-save prompts; two tab-modal surfaces cannot race.
- Content fullscreen exits before a WebAuthn prompt and cannot re-enter while one is active.
- Direct attestation is never globally pre-approved. Gecko's software token is disabled in production, while standards WebDriver may install a disposable virtual authenticator only in an explicitly automated session.
- Certificate overrides do not make an otherwise invalid WebAuthn origin eligible.
- Conditional credential IDs never enter Core or Platform. Binding retains a bounded byte copy, rechecks the live pending transaction before applying a selection, and polls for an abort while UI is visible. A simultaneous modal WebAuthn operation has priority without stealing another tab's pending conditional request.

Semantic port 0030 adds one product-neutral hook at Gecko's related-origin desktop presentation point. Firefox retains `PopupNotifications`, Android retains `GeckoViewPrompter`, and an embedder without the hook retains upstream behavior. The port is hash-owned in `config/gecko-semantic-ports.json` and must be reconstructed logically for a new ESR.

Semantic port 0034 adds an exact-transaction lifetime timer around Gecko's Windows native provider. The Windows API documents its native timeout as guidance that the platform may override, so Gecko now applies its already adjusted WebAuthn timeout independently and reaches the existing cancellation GUID path when it expires. Completion and cancellation disarm the timer, and the captured transaction ID prevents an expired timer from canceling a later ceremony. The port changes neither software-token policy nor WebDriver virtual authenticator routing.

Semantic port 0035 makes the existing privileged WebDriver authenticator IDs the cross-platform routing signal at Gecko's top-level WebAuthn service. While at least one successfully created test authenticator is active, registration, assertion and conditional credential discovery use authenticator-rs even on a platform that normally selects a native provider. Removing the last exact ID immediately restores that native provider. The port does not unlock the software-token preference, expose an authenticator ID to content or add a production fallback.

# Navis deferred Web API exposure policy

Pinned upstream: Gecko ESR 153.1

## Product boundary

`DEFER` does not permit Navis to advertise a browser-mediated API whose permission prompt, chooser, device/provider ownership or lifecycle is missing. The implementation source may remain in Gecko so a later Navis version can make a deliberate capability decision, but ordinary Web content must not see a half-working entry point.

The policy is:

| Surface | Pinned-ESR source state | Navis content state |
| --- | --- | --- |
| WebUSB, WebBluetooth and WebHID | No corresponding Navigator/WebIDL implementation | Absent; a future upstream addition fails the source gate pending review |
| WebMIDI and WebSerial | Implemented and normally desktop-enabled behind Gecko add-on/product permission paths | Source retained; exposure and testing bypasses locked off |
| legacy WebVR and WebXR | Implemented behind disabled preferences | Source retained; exposure locked off |
| Payment Request | Implemented, with ESR release exposure already returning false | Product preference additionally locked off |
| FedCM/Identity Credential and Digital Credential | Implemented behind disabled preferences | Source retained; exposure locked off |

This policy does not alter ordinary HTML forms, OAuth popup/redirect flows, Credential Management's required WebAuthn path, passkeys or security keys.

## Enforced preferences

`platform/gecko-chrome/app/profile/navis.js`, mounted in the prepared Gecko tree as `navis/app/profile/navis.js`, locks the following product policy:

- `dom.webmidi.enabled=false` and `dom.webmidi.gated=true`;
- `dom.webserial.enabled=false` and `dom.webserial.gated=true`;
- `dom.vr.enabled=false` and `dom.vr.webxr.enabled=false`;
- `dom.payments.request.enabled=false`; and
- both `dom.security.credentialmanagement.digital.enabled` and `dom.security.credentialmanagement.identity.enabled` to `false`.

The locked state prevents a profile or ordinary preference change from restoring an API without its Navis product contract. This is an exposure cut, not a source-graph deletion.

## Repeatable gates

Run the source and package policy gate with:

```bash
python3 scripts/verify-deferred-web-apis.py
python3 scripts/verify-deferred-web-apis.py --runtime /path/to/navis-runtime
```

The source gate reconciles every product lock with the exact ESR preference and WebIDL/function exposure mechanism. It also fails if a future ESR introduces WebUSB, WebBluetooth or WebHID WebIDL without an explicit Navis review. The runtime mode checks the packaged `omni.ja` rather than trusting source intent.

## Claim boundary and later activation

Source checks prove the selected ESR and Navis profile express a complete, locked policy. They do not replace extracted-package WebDriver replay on both native platforms. Native replay is required for release acceptance.

A later version may activate one of these surfaces only after the capability matrix records a new state and ownership decision, Platform provides the required permission/chooser/lifecycle UI on every promised operating system, the Binding fails closed without that Delegate, and positive plus rejection automation is added. Removing a lock without those changes is a regression.

# Excluded browser-mediated Web APIs

Navis excludes WebMIDI, WebUSB, WebBluetooth, WebHID, WebSerial, FedCM, Payment Request, Digital Credentials and WebXR on Linux, Windows and Android. Legacy WebVR is excluded with WebXR. These are deliberate product exclusions, not pending activation or permission-only locks.

Every Runtime profile disables `web-device-apis`, `web-identity`, `web-payments` and `webxr`. The semantic-port series gates upstream MIDI/Serial, identity/digital, payment and VR directories, generated bindings, IPC, native dependencies and packaged services. Navis does not include custom USB/HID/Bluetooth engines, their Worker transports, device selectors, background-use UI or Android OS-permission services.

Existing profile preferences and obsolete device grants cannot restore absent code. The product ignores obsolete grants without clearing unrelated permissions, login data or browsing state. No migration may enable an API or introduce a remote provider.

Camera/microphone capture, WebRTC, local Notifications, native geolocation, ordinary input/gamepads/Bluetooth audio, WebAuthn/security keys, local passwords and ordinary website login remain independent. Workers, Service Workers, Cache Storage, extensions and developer tools remain available. Generic consent retains FIFO ordering, cancellation, exact requesting-origin ownership, private isolation and effective media revocation.

Run `python3 scripts/verify-excluded-web-apis.py` after preparing the selected Gecko pin and ports. The source gate checks all build profiles, disabled build declarations and preprocessed WebIDL. Add `--objdir /path/to/obj` to inspect the configured binding inventory and `--runtime /path/to/extracted-product` to audit packaged services. The capability and source-domain gates additionally verify the configured native/Rust dependency graph and distribution contents.

Release validation requires fresh same-source packages on every required target and secure-content feature detection in Window, iframe, DedicatedWorker, SharedWorker and ServiceWorker contexts where applicable, including attempts to enable stale preferences. Negative checks must be paired with retained WebAuthn, media, permissions, Worker and normal-browsing regressions. A source check or build is not device execution or human acceptance.

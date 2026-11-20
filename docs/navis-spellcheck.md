# Built-in spellchecking exclusion

Navis excludes browser-owned spelling detection, inline misspelling underlines, correction suggestions, Hunspell dictionaries and dictionary add-ons. This decision is independent of ordinary text editing and native input-method services.

## Native boundary

The Runtime semantic-port series defines a default-on `--disable-spellchecking` configure gate. All Navis development and release profiles select it off. Other upstream consumers retain their normal enabled behavior unless they explicitly select the gate.

Disabled builds omit the Hunspell and spellchecker implementation graph, editor checking implementations, remote spellcheck protocol and dictionary-update IPC. They package no spelling dictionaries, inline spellchecker modules/actors or dictionary-only built-in registry. Text-layout hyphenation patterns remain available; the `.dic` extension alone does not make them spelling dictionaries. Add-on startup avoids requesting the unavailable service or interface, and dictionary add-ons are unusable. Ordinary extensions, language packs and non-Navis product built-in manifests preserve their independent lifecycle.

The existing `nsIEditorSpellCheck`, `nsIInlineSpellChecker` and editor/XPConnect method declarations remain for native interface compatibility. They provide no spellchecker instance: the editor getter returns null and the privileged factory fails with `NS_ERROR_NOT_AVAILABLE`. HTML `spellcheck` attributes retain their standard DOM semantics, but cannot create a removed checking engine. Navis also locks `layout.spellcheckDefault` to 0 so existing Profile preferences do not request inline checks.

## Product and input boundary

Context-menu descriptors expose no misspelling state, suggestion strings or spelling command slots. Ordinary editing, selection, undo/redo, clipboard operations, local Find and navigation keep their existing Runtime and Platform paths.

Android continues using its real native input connection and input-method composition. Keyboard candidates, language input and keyboard-provided autocorrection follow the selected input method and webpage input hints; removing the browser checker does not disable them. Navis provides no browser spellchecking setting, dictionary manager or spelling menu.

Existing Profile data, including any old personal dictionary file, are preserved. Such files cannot restore the excluded native feature.

## Verification

The capability and source-domain ledgers require the feature off and reject its native implementation graph. `scripts/verify-spellcheck.py` rejects dictionary payloads, helper/actor modules, dictionary registries and obsolete suggestion commands in an extracted package; it does not depend on a generated Gecko tree.

A changed capability graph requires regenerated build metadata and fresh distribution staging, followed by package audits. Native acceptance must reject the spellchecker service/factory and suggestion path while preserving textarea/contenteditable editing, undo, selection, clipboard and Find. Android additionally verifies input-method behavior on its own target. Source checks, incremental builds, native checks and manual acceptance are separate evidence.

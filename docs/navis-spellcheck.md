# Navis spellcheck boundary

Pinned Gecko: Firefox ESR 153.1 (`468445e58d3acc7e4e059be99856daff1f2ae8f1`)

## Product boundary

Navis retains Gecko's ordinary inline spellchecking for multiline Web editors. It packages exactly the pinned ESR `en-US.aff` and `en-US.dic` files. It does not restore Firefox's dictionary manager, remote dictionary catalogue, add-ons UI or user-installable dictionary extensions. Additional packaged dictionaries require an explicit later policy change.

The browser-shell path is:

```text
trusted contextmenu event over a spellchecked editor
  -> content-process Gecko InlineSpellChecker/Hunspell
  -> at most five bounded suggestion strings
  -> frozen Core ContextMenuDelegate descriptor
  -> Navis Gecko menupopup presentation
  -> selected fixed command slot
  -> original content-process Gecko editor replacement
```

Gecko continues to own misspelling detection, underline selection, language selection, suggestion generation and editor replacement semantics. Navis owns only the public projection and browser-menu presentation.

## Contract and security rules

- Suggestions are accepted only for the five fixed `spell-replace-0` through `spell-replace-4` command slots.
- Each suggestion is a nonempty string of at most 128 UTF-16 code units; control characters and duplicates are rejected.
- The public item is immutable and contains only its command ID, semantic `spelling` group, enabled state and bounded dictionary suggestion. No editor, DOM range, node, principal, actor or XPCOM object crosses the Core contract.
- The content actor retains the live editor and misspelled range. Replacement succeeds only while the matching short-lived menu token and connected target remain current. Navigation, competing input, popup replacement or actor destruction invalidates it.
- The dictionary suggestion is user/content-adjacent data, not Navis UI copy; Platform presents it verbatim and does not translate it.

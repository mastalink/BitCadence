# Settings

Open `/console` and choose **Settings**, or run `bitcadence settings`.

```
$ bitcadence settings
Settings
  Pause everything: Off
  Tell me on my phone: On
Pause or resume with `bitcadence pause` / `bitcadence resume`.
For people who like details: bitcadence settings --show-advanced
```

The CLI retains its existing “Pause everything” wording. The browser uses **Stop work…**: review its effect, then **Stop work now** to halt active attempts and pause new work, or **Cancel** to change nothing. **Resume** allows new work; halted jobs still need Retry. External effects already performed are not undone. **Tell me on my phone** reports notification status. Memory lives under Drumline.

## Show advanced

In the current browser, **Connect** opens directly visible connection fields when disconnected. Once connected, **Settings → Server administration** reveals Connection, Gateway controls and Connectors. **Technical details** in the topbar/Experience controls displayed IDs, payloads and retry fields; it does not grant permissions. The old Show advanced browser label is retained here as a historical heading. In a terminal:

```
bitcadence settings --show-advanced     # also: --all
```

adds where BitCadence listens and this computer's sign-in, always masked as
`mco_tok_...1a2b`, followed by every raw setting. Reading or changing one setting
by name still works exactly as before: `mco settings KEY` and `mco settings KEY
VALUE`.

For agent credentials and specialist model/workflow/server screens, use **Administration**. See the [full GUI instructions](gui-reference.md#administration). These instructions describe source/fixture-tested behavior; they do not assert an update to your running app.

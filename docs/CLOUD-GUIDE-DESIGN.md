# Cloud guide design

Subject: the deployed BitCadence AWS test lab. Audience: Joseph learning operations
and investors understanding where control and evidence live. Primary job: explain
the architecture and teach a safe first session without implying production HA.

Palette: paper #edf3f7, ink #16324a, network blue #276baa, approval amber #966000,
evidence teal #16776e, white #ffffff. Typography: Segoe UI for readable Windows
instructions; Georgia for the opening title only. Left-aligned explanatory text.

Layout: a wiring diagram occupies the main canvas with a component inspector
alongside, followed by a numbered operating sequence and adjustable cost estimate.

```
[ title / lab boundary                           ]
[ laptop -- tunnel -- hub -- worker ] [ inspector]
[                      |  -- reviewer           ]
[                      +---- evidence           ]
[ 1 connect / 2 submit / 3 approve / 4 stop       ]
[ session hours -------------------- estimate   ]
```

Review: use borders to show actual AWS/private-network boundaries, not repeated
decorative cards. Number only the real operating sequence. Avoid live-status dots:
this document explains architecture and does not poll AWS. Keep retained storage,
the lack of HA, and the distinction between a budget and a spending cap explicit.

# Architecture artwork

The system diagram is used in the README's architecture section and the
[architecture guide](../../docs/architecture.md).

- [HTML source](uni-robot-agent-architecture.html): **1920 × 1544 CSS pixels**, with embedded fonts.
- [Vector PDF](uni-robot-agent-architecture.pdf): one **1440 × 1158 pt** page.
- [PNG](uni-robot-agent-architecture.png): **7680 × 6176 pixels**, lossless RGB.

To export the PNG with Poppler installed, run from the repository root:

```sh
bash assets/diagrams/export-architecture.sh
```

The script rasterizes the bundled PDF at 384 DPI. When changing the diagram,
update the HTML source and vector PDF together, then re-export the PNG. Keep all
three files in this directory synchronized.
See the [font notices](../brand/README.md#typography).

The diagram uses RoboUse and RoboDojo as simulation examples. `actor_act` requires Hybrid mode and
`write_skill` is opt-in.

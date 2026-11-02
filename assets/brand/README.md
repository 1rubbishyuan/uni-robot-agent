# Brand artwork

The README cover uses a wordmark, positioning line, and supporting sentence on
a dark background. The text has balanced top and bottom margins.

The HTML source, PNG and browser export page are included in this directory.

- [HTML source](uni-robot-agent-cover.html): **1600 × 640 CSS pixels**.
- [PNG](uni-robot-agent-cover.png): **6400 × 2560 pixels**, lossless RGB.
- [Export page](export-cover.html): renders the HTML directly to a 4× PNG.
- [Architecture artwork](../diagrams/README.md): the separate system diagram,
  its HTML source, vector PDF, and PNG export.

## Export the cover

From the repository root, start a local server:

```sh
python3 -m http.server 8766 --bind 127.0.0.1
```

Open `http://127.0.0.1:8766/assets/brand/export-cover.html`, click **Export PNG**,
then **Download PNG**, and replace `uni-robot-agent-cover.png`. All fonts are
embedded in the source, so rendering does not depend on an external font service.
Optional lossless compression may remove an opaque alpha channel but must
preserve every RGB pixel.

## Typography

The cover uses **Montserrat SemiBold** for the wordmark and **Satoshi Medium**
and **Regular** for the supporting text. The architecture diagram uses the same
families plus **Geist Mono** for method names.

- Montserrat: [upstream source](https://github.com/JulietaUla/Montserrat) and
  [SIL Open Font License](fonts/montserrat/OFL.txt).
- Satoshi: [ITF Free Font License](https://www.fontshare.com/licenses/itf-ffl).
- Geist Mono: [upstream source](https://github.com/vercel/geist-font) and
  [SIL Open Font License](fonts/geist/OFL.txt).

Font licenses are separate from the repository's MIT license.

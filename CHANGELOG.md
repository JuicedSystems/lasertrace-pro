# Changelog

What changed in each release of LaserTrace Pro. The release workflow copies
the section for a version into that release's notes on GitHub, so each entry
is written for the person at the laser.

## [0.1.1] - 2026-09-14

### Fixed

- **16-bit height maps no longer come in almost white.** 16-bit and 32-bit
  grayscale images (PNG, TIFF) used to be cut down to 8 bits by clipping, so
  a 16-bit height map arrived almost entirely white and a floating-point one
  almost entirely black. They are now scaled so the full tonal range
  survives: integer images read 0–65535 as black to white, floating-point
  images read 0–1.
- **White logos on a transparent background no longer vanish.** Transparent
  areas used to be filled with white, which made white or light artwork cut
  out for dark stock disappear. When the background is transparent:
  - one-colour art is traced from its shape, whatever the colour;
  - light, flat-colour art with no dark detail is inverted, so the art
    becomes the ink;
  - everything else, such as art with dark outlines or text, and photos, is
    still placed on white as before.
- **Batch mode and SVG files.** When SVG support is missing (the optional
  `cairosvg` package or the Cairo library it needs), batch mode now skips
  `.svg` files instead of reporting an error for each one.

### Changed

- The AUTO notes now also say what happened to the file on the way in, for
  example "16-bit source -> 0..65535 scaled to 8-bit" or "shape taken from
  transparency".
- Saved job settings record the source's bit depth and how its transparency
  was handled (`bit_depth`, `alpha_policy`). The JSON schemas in `schemas/`
  are regenerated and now include the depth-engraving settings too.

## [0.1.0] - 2026-09-11

First public release, with standalone builds for Windows x64 and macOS on
Apple Silicon.

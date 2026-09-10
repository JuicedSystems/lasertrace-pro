# Security

LaserTrace Pro is a desktop and command-line tool. It does not phone home, open
network ports, or transmit your artwork anywhere.

It does process **untrusted input files** — images, PDFs and SVGs that arrive
by email from customers — through third-party parsers (Pillow, OpenCV,
pypdfium2). A malformed file causing a crash, a hang, or worse is a genuine
security concern here.

## Reporting

Please report vulnerabilities privately through
[GitHub Security Advisories](https://github.com/JuicedSystems/lasertrace-pro/security/advisories/new)
rather than a public issue. Include the file that triggers it if you can.

Expect an acknowledgement within a week.

## Scope notes

- The Potrace sidecar runs as a **separate process** and is fed a temporary
  bitmap, not your original file.
- Preset files (`presets/*.json`, `~/.lasertrace/presets/`) are parsed with
  pydantic, not `eval`. They are still configuration you should trust before
  loading.

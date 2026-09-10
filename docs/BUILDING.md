# Building the standalone apps

The shop machine next to the laser should not need a Python install. These are
the PyInstaller bundles that make that true: a Windows folder you unzip and
run, and a macOS `.app`.

Most people never need this page — [the release
page](https://github.com/JuicedSystems/lasertrace-pro/releases) has the builds
already, and `.github/workflows/release.yml` produces them on every `v*` tag.

## What gets built

| Target | Runner | Output |
|---|---|---|
| Windows x64 | `windows-latest` | `LaserTracePro/` → `LaserTracePro-windows-x64.zip` |
| macOS Apple Silicon | `macos-14` | `LaserTrace Pro.app` → `LaserTracePro-macos-arm64.zip` |
| macOS Intel | `macos-13` | `LaserTrace Pro.app` → `LaserTracePro-macos-x64.zip` |

**PyInstaller cannot cross-compile.** A macOS app has to be built on macOS and
a Windows exe on Windows, which is why the two mac architectures are two
separate runners rather than a universal2 build — `macos-14` is arm64 and
`macos-13` is x86_64.

## Locally

```bash
pip install -r requirements-ui.txt
pip install "pyinstaller>=6.10"
pyinstaller packaging/lasertrace.spec --noconfirm

python tools/verify_bundle.py dist/LaserTracePro          # Windows
python tools/verify_bundle.py "dist/LaserTrace Pro.app"   # macOS
```

On macOS, generate the icon first — PyInstaller wants `.icns`, and `iconutil`
ships with the OS:

```bash
mkdir -p packaging/icon.iconset
python - <<'PY'
from pathlib import Path
from PIL import Image
src = Image.open("lasertrace_ui/assets/icon.png").convert("RGBA")
out = Path("packaging/icon.iconset")
for size in (16, 32, 128, 256, 512):
    src.resize((size, size), Image.LANCZOS).save(out / f"icon_{size}x{size}.png")
    src.resize((size * 2, size * 2), Image.LANCZOS).save(out / f"icon_{size}x{size}@2x.png")
PY
iconutil -c icns packaging/icon.iconset -o packaging/icon.icns
```

## Decisions baked into the spec

**onedir, not onefile.** With PySide6 + OpenCV + SciPy the payload is large
enough that onefile's extract-to-temp costs about ten seconds on *every*
launch, and self-extracting executables draw more antivirus attention. On
macOS the folder is the `.app`, so it still looks like a single icon; on
Windows it is a folder to unzip.

**Data files are declared explicitly.** `lasertrace/preset_data/*.json` and
`lasertrace_ui/assets/*` are loaded by path at runtime, and
`Path(__file__).parent` inside a frozen module resolves under `sys._MEIPASS`.
They must land at the same relative paths they occupy in the source tree, or
the app starts and then dies with "preset not found". This is the same trap
that made the first wheel useless — hence `tools/verify_bundle.py`.

**The GPL sidecar is not bundled.** Potrace stays out of the distributed app
for the same reason it stays out of the wheel; the bundle check fails if
anything named `potrace` appears inside. The app falls back to the contour
engine.

**UPX is off.** It saves some size and costs a lot of antivirus false
positives.

**No `argv_emulation`.** It pumps AppleEvents at startup and can hang a
headless launch, which is how CI verifies the build. Open files from inside
the app.

## Verifying a build

`tools/verify_bundle.py` does more than look at the file list — it launches the
real binary with `--selftest`, which traces an image and writes a DXF with a
proper exit code:

```
  ok    built-in presets bundled  (12 json)
  ok    window icon bundled
  ok    no GPL sidecar in the bundle
  ok    app launches and traces an image
```

CI runs it before uploading anything, so a broken bundle never reaches a
release.

## Signing

**The published builds are unsigned.** Code-signing needs a paid Apple
Developer ID (for notarisation) and an Authenticode certificate for Windows;
neither is set up. Consequences for users:

- **Windows** — SmartScreen shows "Windows protected your PC" until the binary
  builds reputation. *More info* → *Run anyway*.
- **macOS** — Gatekeeper refuses a double-click. Right-click → *Open* → *Open*,
  or *System Settings → Privacy & Security → Open Anyway*.

The macOS build is **ad-hoc signed** (`codesign --sign -`) in CI. That is not
notarisation and does not remove the warning, but an arm64 binary without any
signature at all is killed outright rather than merely warned about, so it is
the difference between "annoying" and "broken".

To sign properly, add `codesign_identity` and `entitlements_file` to the spec's
`EXE()` and run `xcrun notarytool submit` on the zip with your Developer ID.

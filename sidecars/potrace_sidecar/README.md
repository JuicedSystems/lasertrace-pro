# Potrace sidecar (GPL isolated)

Potrace and its Python ports (`potracer`, `pypotrace`) are GPL-2+/GPL-3. LaserTrace
core is MIT. To keep the GPL from reaching the application we never import
Potrace in-process. This directory is a standalone program:

```
python sidecar.py --turdsize 2 --alphamax 0.4 --opttolerance 0.2 < input.pbm > paths.json
python sidecar.py --check      # exit 0 if potracer is importable
```

The core talks to it only through stdin/stdout (`lasertrace/engines/potrace_client.py`).
It can be replaced by the native `potrace.exe` binary with a 20-line shim, or
dropped entirely: the app is fully functional with the in-house `contour` engine.

Everything in this directory is licensed under GPL-3.0 (see LICENSE).

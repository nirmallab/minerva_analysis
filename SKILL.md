# Minerva Analysis Project Guide

Use this guide when working on the `minerva_analysis` repository. It captures the project shape, high-risk boundaries, and validation workflows so a new coding agent can become useful quickly.

## What This Project Is

Minerva Analysis, also called Gater in older docs, is an OpenSeadragon-based cellular image viewing and analysis tool. It has a Python Flask/Waitress backend and a JavaScript/Webpack frontend.

The core application serves multiresolution microscopy image tiles, segmentation/label tiles, feature CSV-backed cell data, marker-threshold/GMM gating analysis, and viewer pages. It now also supports Jupyter notebooks through an iframe-backed sidecar Flask server, exposed directly on localhost or through `jupyter-server-proxy`.

Important user-facing modes:

- Desktop/local web app: `python run.py`, then open `http://localhost:8000/`.
- Notebook app: `from minerva_analysis.jupyter import MinervaViewer`.
- Remote Jupyter/JupyterHub: use `MinervaViewer(..., proxy=True)` with `jupyter-server-proxy`.
- PyPI/package install: `pip install "minerva-analysis[jupyter]"` should work without conda, while conda/uv remain useful for development.
- Frontend development: edit `minerva_analysis/client/src`, then run `npm run start` to regenerate bundled assets in `minerva_analysis/client/dist`.

## Repository Map

Top-level files:

- `pyproject.toml`: Python package metadata, dependencies, extras, console script, Jupyter server proxy entry point, package data.
- `requirements.yml`: Conda bootstrap environment for local development. Conda owns the interpreter; pip/uv installs the package.
- `requirements-dev.lock.txt`: uv-generated Python dependency lock for dev/Jupyter extras.
- `run.py`: legacy/local desktop server entry point. Keep this working.
- `Dockerfile`: Docker runtime, currently Python 3.13.
- `MANIFEST.in` and `[tool.setuptools.package-data]`: packaging inclusion for frontend assets/templates/shaders.
- `README.md`: user-facing usage notes.
- `tests/baseline_orion2.py`: main local smoke test using the `orion2` datasource when available.

Python package:

- `minerva_analysis/__init__.py`: creates the Flask app, configures `data_path`, SQLite path, package paths, base URL, notebook iframe headers, and imports routes/models.
- `minerva_analysis/server_cli.py`: notebook-friendly sidecar CLI, exposed as `minerva-analysis-server`.
- `minerva_analysis/jupyter.py`: notebook display API and subprocess lifecycle for sidecar servers.
- `minerva_analysis/proxy.py`: `jupyter-server-proxy` launcher entry point.
- `minerva_analysis/datasource.py`: programmatic datasource registration for notebooks and scripts.
- `minerva_analysis/server/models/data_model.py`: core tile, metadata, CSV, zarr/OME-TIFF, segmentation, GMM, and spatial-query behavior. Treat this as high-risk.
- `minerva_analysis/server/models/database_model.py`: SQLite models.
- `minerva_analysis/server/routes/page_routes.py`: viewer/upload/page routes.
- `minerva_analysis/server/routes/data_routes.py`: JSON/data/tile/query/download routes.
- `minerva_analysis/server/routes/import_routes.py`: upload/import flow routes.
- `minerva_analysis/server/utils/*`: conversion, pyramid, normalization, and image utility code.

Frontend:

- `minerva_analysis/client/package.json`: Webpack 5 frontend dependencies and scripts.
- `minerva_analysis/client/webpack.config.js`: JS/CSS/shader bundling config.
- `minerva_analysis/client/src/js/main.js`: app initialization.
- `minerva_analysis/client/src/js/services/dataLayer.js`: client API layer for server data/metadata/tile configuration.
- `minerva_analysis/client/src/js/views/imageViewer.js`: OpenSeadragon viewer, tile loading, cache behavior, overlays, channel rendering.
- `minerva_analysis/client/src/js/services/glRenderer.js`: owned WebGL2 tile-colorize/threshold engine (`GLRenderer` class — shader compile/link, texture upload). Ported from the `viawebgl` project's OpenSeadragon-independent core; this repo no longer depends on that package. See "OpenSeadragon Integration" below.
- `minerva_analysis/client/src/js/views/csvGatingList.js`: CSV/gating UI behavior.
- `minerva_analysis/client/templates/*.html`: Flask templates. `base.html` is especially important for base URL and frontend asset loading.
- `minerva_analysis/client/external/openseadragon-bin-2.4.0/`: only `canvas-overlay-hd.js` (lasso/centroid canvas overlay), `openseadragon-scalebar.js` (scale bar + "download current view" export), and the toolbar icon image set remain here — both are third-party, unmaintained, single-file plugins loaded as plain `<script>` tags in `base.html`, not npm packages. The folder name is stale (real OpenSeadragon is now `client/package.json`'s `openseadragon` npm dependency, not a vendored 2.4.0 build) but was kept as-is rather than renamed. Do not put a new copy of the OpenSeadragon core JS file back in this folder.
- `minerva_analysis/client/dist/vendor_bundle.js`: built frontend bundle that must be included in packages.

Generated/local-only directories:

- `build/`, `dist/`, `minerva_analysis.egg-info/`, `minerva_analysis-<version>/`, `__pycache__/`, `.pytest_cache/`, `node_modules/`, and `minerva_analysis/data/` are generated or local data. Do not commit them unless explicitly asked and justified.

## Core Architecture

The Flask app is created at import time in `minerva_analysis/__init__.py`.

Data root selection:

- `MINERVA_DATA_PATH` wins when set.
- Frozen/PyInstaller apps use a `data` directory next to the executable.
- Default development mode uses `minerva_analysis/data`.

The selected data root contains:

- `config.json`: datasource definitions.
- `db.sqlite3`: local SQLite database.
- datasource directories and generated pyramids/tiles as needed.

Tile and metadata flow:

- The browser loads a datasource page such as `/orion2`.
- The frontend requests `/config`, metadata, channel names, OME metadata, and `/generated/data/<datasource>/<channel>/<level>/<x>_<y>.png` tiles.
- Python routes delegate most tile/metadata behavior to `server/models/data_model.py`.
- Segmentation is represented as an image channel in `config.json` plus `segmentation` metadata. The first `imageData` entry often points to the label/area channel.

Notebook flow:

- `MinervaViewer` starts `python -m minerva_analysis.server_cli` in a subprocess bound to `127.0.0.1`.
- Direct local notebooks use iframe URLs like `http://127.0.0.1:<port>/<datasource>`.
- Remote/JupyterHub notebooks use proxy URLs like `<jupyter_base>/proxy/<port>/<datasource>`.
- `MINERVA_BASE_URL` makes Flask templates and frontend requests base-url aware.
- `MINERVA_NOTEBOOK_MODE=1` enables same-origin iframe-friendly headers.

Datasource registration:

- Use `register_datasource(...)` in `minerva_analysis/datasource.py`.
- It writes/updates `config.json` under the selected `data_dir`.
- It uses `data_model.convertOmeTiff(...)` for image and segmentation metadata.
- `copy=False` stores absolute paths and is preferred for large files on remote servers.

## OpenSeadragon Integration

OpenSeadragon is a real, current npm dependency (`client/package.json`'s `openseadragon`, currently `^6.1.0`) with matching `@types/openseadragon`. It used to come in through a chain of personal GitHub forks (`viawebgl` → a pinned-commit fork of OpenSeadragon reporting as 2.3.1) that existed for one reason: exposing raw AJAX tile bytes so the app could decode true 16-bit pixel data itself instead of losing precision through the browser's built-in 8-bit PNG decode. That fork chain was removed; if you see any reference to `viawebgl`, `thejohnhoffer/openseadragon`, or `window.viaWebGL` in old branches/history, treat it as gone, not current.

- **Raw tile bytes**: `imageViewer.js`'s `handleTileLoaded` reads `e.tileRequest.response` (an `ArrayBuffer`) directly off the `tile-loaded` event — OpenSeadragon 6.x exposes the underlying XHR there as a stable, non-deprecated property, and uses `responseType: "arraybuffer"` for AJAX-loaded tiles, so no fork or custom `OpenSeadragon.converter` registration is needed. It's registered as an `async` function; OpenSeadragon awaits a handler's returned promise (`raiseEventAwaiting`) instead of needing an explicit `getCompletionCallback()`.
- **WebGL colorize/threshold pass**: `client/src/js/services/glRenderer.js` (`GLRenderer` class) is a self-contained, OpenSeadragon-independent WebGL2 engine (shader compile/link, texture upload). `imageViewer.js` drives it directly via `viewer.addHandler('tile-drawing', ...)`, compositing the WebGL output onto the tile's 2D canvas.
- **`drawer: 'canvas'` is required** in the `viewer_config` passed to `OpenSeadragon(...)` in `imageViewer.js`. The per-tile WebGL compositing depends on the `tile-drawing` event's 2D `rendered` canvas context, which is only guaranteed under the canvas drawer — OpenSeadragon 6's newer WebGL Drawer has no documented custom-shader hook as of 6.1. Don't change this to `'webgl'` or `'auto'` without re-verifying that assumption against whatever OpenSeadragon version is current at the time.
- **Vendored plugins**: only `canvas-overlay-hd.js` (lasso/centroid overlay, `OpenSeadragon.CanvasOverlayHd`) and `openseadragon-scalebar.js` (`viewer.scalebar(...)`, `scalebarInstance.getImageWithScalebarAsCanvas()` for the download-view export) remain in `client/external/openseadragon-bin-2.4.0/`. Both are unmaintained third-party single-file plugins with no npm equivalent (confirmed via `npm view` — 404), vendored in-repo rather than pulled from a live fork; both currently work against OpenSeadragon 6.x with zero patches. `openseadragon-svg-overlay.js`, `openseadragonrgb.js`, and `openseadragon-filtering.js` were deleted — confirmed zero references anywhere in the app, and the RGB one was actually crashing page load under 6.x (it patched a `Drawer` internal that no longer exists).
- If a future OpenSeadragon upgrade breaks tile rendering, the debugging order is: (1) confirm `drawer: 'canvas'` is still in effect, (2) confirm `tile-loaded` still exposes `e.tileRequest.response` the same way, (3) check the two vendored plugins against whatever internals changed.

## Data Layer: Polars, Not Pandas

`data_model.py`'s `datasource` global (the feature-CSV-backed cell table) and every other DataFrame in this codebase are Polars, not pandas — pandas was fully migrated away and removed as a dependency. Non-obvious things worth knowing before touching this:

- **`id` column**: manufactured via `datasource.with_row_index("id")` immediately after `pl.read_csv(...)`, before any other transform — mirrors the old pandas code's `df['id'] = df.index` trick (a stable positional identity), since nothing in the app sorts/reindexes/samples `datasource` after load. Unlike the old pandas version (where `id` was appended as the *last* column), Polars' `with_row_index` prepends it as the *first* column — this changes CSV export column order (visible in `/download_gating_csv` output) but not correctness, since every consumer accesses columns by name, not position.
- **NaN vs. null**: Polars distinguishes `null` from float `NaN`; pandas' `pd.to_numeric(col, errors="coerce")` produced `NaN` for unparseable values, and downstream code (`_apply_gate_mask` in `data_model.py`, `_apply_gates` in `centroid_tiles.py`) relies on `np.isnan`/`np.isfinite` semantics. Every place a numeric column is extracted to numpy uses an explicit `.cast(pl.Float32, strict=False).fill_null(float("nan")).to_numpy()` pattern rather than relying on Polars' default null-to-NaN export behavior — keep this pattern for any new extraction rather than a bare `.to_numpy()`.
- **`download_gating_csv` dtype parity**: when writing gate-encoded values into an existing float column, the value literal is explicitly cast to that column's original dtype (`csv.schema[channel]`) before the `pl.when/then/otherwise`. Without this, Polars renders an int literal as `"1"` in the CSV where pandas' implicit upcast used to render `"1.0"` — a real text diff downstream tools might depend on.
- **`download_gating_csv` empty-gates behavior changed on purpose**: the old pandas `.query('')` raised `ValueError` when called with zero gates set. The Polars rewrite treats empty gates as "no filter" (empty `ids`, no crash) — a deliberate behavior fix made during the migration, not an oversight.
- **No `.loc`-style in-place mutation**: `download_gates`/`save_gating_list`/`download_channels`/`save_channel_list` build small per-channel export DataFrames using `pl.when(...).then(...).otherwise(...)` inside `with_columns(...)` instead of pandas' `.loc[mask, col] = value`, since Polars frames are immutable. The lasso-row-append path in `download_gates`/`save_gating_list` is confirmed dead in live usage (lasso was removed; `imageViewer.js` permanently sets `list_lassos = {}`) but was still migrated correctly (build rows, `pl.concat(..., how="diagonal_relaxed")`) rather than left as pandas leftovers.
- Do not reintroduce pandas. If a new feature needs CSV/DataFrame work, use Polars and follow the patterns above.


## Common Tasks And Where To Work

For notebook support:

- Start in `minerva_analysis/jupyter.py`, `minerva_analysis/server_cli.py`, `minerva_analysis/proxy.py`, and `minerva_analysis/__init__.py`.
- Then check `client/templates/base.html` and URL construction in frontend services.
- Preserve `python run.py` behavior while changing notebook/server-proxy behavior.

For PyPI packaging:

- Start in `pyproject.toml`, `MANIFEST.in`, and package data under `minerva_analysis/client`.
- Use `uv build` as the canonical package build.
- Verify the built wheel from outside the repo so imports come from `site-packages`, not the checkout.
- Ensure templates, `client/dist/vendor_bundle.js`, shaders, CSS, images, and external OpenSeadragon assets are included.

For tile or segmentation bugs:

- Start with browser console URLs and `server/routes/data_routes.py`.
- Then inspect `server/models/data_model.py`, especially OME/zarr level selection, channel names, label image handling, and generated tile paths.
- On the frontend, inspect `client/src/js/services/dataLayer.js` and `client/src/js/views/imageViewer.js`.
- If the bug is specifically in tile decoding/colorizing (wrong colors, blank/black tiles, WebGL errors), see "OpenSeadragon Integration" above — start with `imageViewer.js`'s `handleTileLoaded`/`tileDrawingCustom` handlers and `client/src/js/services/glRenderer.js`.
- Be careful with cache behavior: a symptom that only resolves after hard refresh can be frontend cache ordering, stale bundle, or request timing.

For gating/nearest-cell/query behavior:

- Server side: `server/routes/data_routes.py`, `server/models/data_model.py`, `server/models/database_model.py`.
- Frontend side: `client/src/js/views/csvGatingList.js` and `client/src/js/views/viewerSidebar.js`. Gating is marker-threshold/GMM-based only (slider ranges per channel, auto-gate via `getGatingGMM`/`getChannelGMM`) — there is no spatial/lasso selection tool.
- "Nearest cell" in the live app is only the click-to-inspect lookup (`dataLayer.getNearestCell()` -> `GET /get_nearest_cell`), triggered from `imageViewer.js`. There is no separate neighborhood/channel-relationship analysis view — an earlier `lensingFilters/*` module implementing that was found to be dead code (never imported by `main.js`) and was removed; do not recreate features assuming it still exists.
- Lasso/spatial-region selection (freeform polygon drawing on the image, `draw_lasso`/`toggle_lasso`/`delete_lasso`/`get_cells_in_polygon`/`get_cells_in_lassos`) was a real, wired-in feature that has since been **removed by user request** (unused). Saved/exported gate lists may still contain legacy `channel == 'Lasso'` rows from before the removal — `csvGatingList.js` `applyGates()` intentionally skips them rather than erroring. `imageViewer.js`'s `list_lassos` property is kept (always empty) purely so `saveGatingList`/`downloadGatingCSV` keep a stable call signature; don't read that as lasso still being supported.
- Confirm CSV download payloads and query endpoints after changes.

For frontend dependency or UI work:

- Work in `minerva_analysis/client`.
- Run `npm install` after dependency changes.
- Run `npm run start` to regenerate `client/dist/vendor_bundle.js`.
- Browser tests are legacy and may fail old behavioral assertions; do not assume `npm test` is fully green without checking current notes. As of this writing the known-stable baseline is 2 passing / 3 failing (`Ensure visual rendering must load a mask`, `Ensure download ranges must download channel ranges`, `Ensure download encodings must download cell encodings`) — verified as pre-existing/unrelated to recent dependency work by running the same suite against both the old and new dependency versions and getting identical results. Treat only *new* failures beyond these three as real regressions.
- `karma-jquery` (the test harness's jQuery-serving plugin) only bundles jQuery up to 3.4.0 and has no 4.x build, so `karma.conf.js`'s `frameworks: [..., 'jquery-3.4.0']` stays pinned to 3.4.0 even though the real app runs jQuery 4.x. This is a test-infra-only gap, not a product bug — don't try to "fix" it by downgrading the app's jQuery.
- Files loaded as plain `<script>` tags in `base.html` (e.g. `imageViewer.js`, `viewerManager.js` is the exception — it's `import`ed into `vendor.js`) are NOT processed by webpack/Babel and cannot use `import`/`export` syntax. Anything they need from an npm package must be exposed as a `window.X` global from `vendor.js` first (see how `GLRenderer`, `ViewerManager`, `OpenSeadragon`, `$`, `d3`, etc. are attached there).

For Python dependency modernization:

- Prefer Python 3.13. Python 3.12 is the fallback target.
- Use the `minerva` conda env for local work. A stale/broken `minerva_analysis` env may exist on some machines; do not use it unless the user explicitly says it has been repaired.
- Use uv for pip resolution/builds, not hand-edited lock files.
- Keep `requires-python = ">=3.12,<3.14"` unless a real dependency forces a narrower range.

## Validation Commands

Run from the repository root unless noted.

Core Python baseline:

```powershell
conda run -n minerva python -m tests.baseline_orion2
```

The baseline datasource is configurable via `MINERVA_BASELINE_DATASOURCE` (defaults to `orion2`). **On macOS, `orion2`'s referenced files live only on the Windows machine this repo is Dropbox-synced with, so those tests just skip.** The locally-populated datasource on Mac is `orion_mac` — use it instead:

```bash
MINERVA_BASELINE_DATASOURCE=orion_mac python -m tests.baseline_orion2
```

All 4 tests pass with `orion_mac` (verified). An earlier version of `tests/baseline_orion2.py` hardcoded the tile-check URL to the `orion2` datasource regardless of `MINERVA_BASELINE_DATASOURCE`; that's been fixed — the test now builds the URL from the `DATASOURCE` variable like the rest of the suite. If you see all 4 pass, that's the expected/healthy state, not a fluke.

Python import/compile sanity:

```powershell
conda run -n minerva python -m compileall -q minerva_analysis tests
```

Local server:

```powershell
conda run -n minerva python run.py
```

Open:

```text
http://localhost:8000/orion2
```

On macOS, use `http://localhost:8000/orion_mac` instead — `orion2`'s image/segmentation files are only present on the Windows side of this Dropbox-synced repo.

Frontend build:

```powershell
cd minerva_analysis/client
npm run start
```

Frontend tests:

```powershell
cd minerva_analysis/client
npm test
```

Known caveat: after the modernization work, TypeScript and Webpack/Karma bundling worked, but several legacy browser assertions were still failing. Treat those as a separate test-maintenance task unless the current branch has fixed them.

Package build:

```powershell
conda run -n minerva uv build
```

Package install probe:

```powershell
conda create -n minerva_piptest -c conda-forge python=3.13 pip
conda activate minerva_piptest
python -m pip install --upgrade pip
python -m pip install dist/minerva_analysis-<version>-py3-none-any.whl
python -c "from minerva_analysis.jupyter import MinervaViewer; print(MinervaViewer.__name__)"
minerva-analysis-server --help
```

When testing wheel imports, run Python from outside the repo. If cwd is the checkout, Python may import the local package instead of the installed wheel.

Notebook smoke:

```python
from minerva_analysis.jupyter import MinervaViewer

MinervaViewer(datasource="orion2", data_dir="path/to/minerva_data")
```

Remote/JupyterHub smoke:

```python
MinervaViewer(datasource="orion2", data_dir="path/to/minerva_data", proxy=True)
```

## Important Invariants

- `python run.py` must keep working for existing desktop/Docker users.
- Notebook support should remain iframe-backed and server-proxied, not a pure ipywidget rewrite.
- The sidecar server should bind to `127.0.0.1`; Jupyter proxy provides authenticated browser access.
- Datasource registration should not copy large OME-TIFF/zarr files by default.
- Do not break absolute-path datasets in `config.json`; many remote datasets will live outside the package directory.
- Keep package data complete. A pip-installed wheel must serve templates, built JS, shaders, CSS, images, and OpenSeadragon external files.
- Avoid committing generated local data/build artifacts.
- Treat `server/models/data_model.py`, `client/src/js/views/imageViewer.js`, and `client/src/js/services/glRenderer.js` as high-risk: small changes can affect tile rendering, segmentation visibility, zoom behavior, and analysis queries. Any OpenSeadragon version bump needs the full re-verification described in "OpenSeadragon Integration" above, not just a `package.json` bump.
- If changing URL construction, test both root mode `/` and proxied notebook mode `/proxy/<port>/`.
- If changing segmentation, test both zoomed-out and zoomed-in display, first normal page load, and browser hard-refresh behavior.

## Known Performance Hot Spots

A dedicated pass fixed most of what was originally found here (frontend O(n²)/O(n) main-thread loops, backend caching/warmup, tile-cache eviction), plus a full pandas→Polars migration of the data layer (see "Data Layer: Polars, Not Pandas" above). What's fixed vs. what remains an accepted gap:

Fixed:
- `client/src/js/views/imageViewer.js` `loadBuffers()`: the modulo-`.filter()` deinterleave when activating a new marker/gating channel was O(nNew² × cellCount); now a single linear pass.
- `client/src/js/services/numericData.js` `fetchCells()`: the double full-array `.filter()` deinterleave (ids/centers) is now a single linear pass.
- `server/models/data_model.py` `get_channel_gmm`/`get_gating_gmm`: both are memoized (`_gmm_cache`, keyed by datasource/channel/selection) and pre-warmed in a background thread after each `load_datasource` call, so most real requests hit a warm cache instead of refitting. `get_gating_gmm` additionally caps its fit input at a random 100,000-row subsample (fixed seed, deterministic) when the cell-level column exceeds that — EM cost scales ~linearly with N, and a 2-component 1D mixture's fitted parameters barely move between 100k and millions of samples. `get_channel_gmm` is intentionally NOT capped — it already fits on the pre-block-reduced zarray (~40k points), already under the cap.
- `server/models/data_model.py` `get_datasource_description`: memoized (`_description_cache`), pre-warmed in the same background thread, and `describe()`'s per-column loop is one vectorized numpy pass (`_describe_numeric`) instead of a per-column loop.
- `client/src/js/views/imageViewer.js` `clearTileCache()`: the 30s watchdog no longer force-clears every active channel's entire tile pyramid when the shared 1000-tile budget is hit. `evictLeastRecentlyUsedTiles(perItemBudget)` evicts per-`TiledImage`, oldest-touched-first (via OpenSeadragon's own `tile.lastTouchTime`), so activating many channels doesn't cause periodic full-pyramid flicker across all of them at once. The segmentation-only clear on `ensureSegmentationReady()` (`clearTileCache(true)`) is unrelated and untouched.
- `server/models/data_model.py` `load_ball_tree`: no longer does its own redundant second CSV read — builds the BallTree from the already-loaded `datasource` DataFrame. The BallTree pickle cache is validated against the source CSV's size/mtime, not just file existence.
- `server/models/data_model.py` `get_channel_cells`/`get_gated_cells`/`get_gated_cells_custom`: no longer build/eval a string query over the full cell table per request — vectorized numpy masking via `_gate_filter_columns`/`_apply_gate_mask`, modeled on `centroid_tiles.py`'s `_load_filter_table`/`_apply_gates` pattern.
- `server/models/data_model.py`/`server/routes/data_routes.py` `download_gating_csv`: no more redundant full-frame copies; the CSV response streams in chunks instead of materializing the whole serialized string in memory at once.
- `server/models/data_model.py` `generate_zarr_png` tile serving: encoded PNG bytes are cached (bounded in-process LRU in `data_routes.py`, keyed on `data_model.load_generation` so a datasource reload invalidates it), so panning back over previously-viewed tiles doesn't re-decode/re-encode from zarr every time.

Still an accepted gap (deliberately not fixed):
- `load_datasource`/`load_ball_tree` still hold exactly one datasource's state in bare module globals behind a single `load_lock`. Concurrent requests touching two *different* datasources (or a background cache-warmup thread racing the very first request after a load) can still transiently read inconsistent state — reproduced once during testing (a `get_gated_cell_ids` call briefly returned unfiltered results immediately after server startup, then was consistent on every retry after). Fixing this for real requires caching multiple datasources' state simultaneously (a dict keyed by name, touching ~30 read sites in the highest-risk file) — deliberately scoped out as too large a change to bundle with other work; revisit only as its own dedicated task if concurrent multi-datasource access becomes a real requirement.
- Any handler bound to a high-frequency event (`mousemove`, zoom, brush) that fires a network request needs a real debounce/throttle, not just a `loading` boolean guard. `updateVisibleCentroidTiles`/`scheduleCentroidTileUpdate` (`imageViewer.js`) and `scheduleSegmentationForGate` (`main.js`) already do this correctly — copy that pattern rather than re-deriving it.

All of the above were CPU-bound/caching/algorithmic problems, not I/O-concurrency problems — switching web frameworks would not have fixed any of them without the same caching/memoization/vectorization work, since Python's GIL means synchronous numpy/sklearn/Polars work blocks a worker either way.

## Current Dependency Policy

Python:

- Primary target: Python 3.13.
- Fallback: Python 3.12.
- Package metadata allows `>=3.12,<3.14`.
- Flask stack is modernized to Flask 3.x.
- Scientific stack is modernized around NumPy 2.x and current Polars/scikit/skimage/tifffile/zarr.
- zarr is currently allowed as `>=3`; zarr/OME paths remain high-risk and need baseline tile tests after changes.
- Before adding a new Python dependency, check whether it is already pulled in transitively (`pip show <pkg>` in the `minerva` env shows `Required-by`). Several declared dependencies have no direct `import` anywhere in the codebase but are still required: `imagecodecs` (used internally by `tifffile` for less-common TIFF codecs), `pydantic` (hard dependency of `ome-types`), `jupyter-server-proxy` (discovered via the `[project.entry-points.jupyter_serverproxy_servers]` entry point, not an import), `xmlschema` (imported in `__init__.py` only to force PyInstaller to bundle it). Don't remove these just because grep finds no import.
- `requests` was removed as a genuinely unused dependency (no import anywhere, `pip show requests` had no `Required-by`) from `pyproject.toml`, `requirements.yml`, and `requirements-dev.lock.txt`.
- `pandas` was fully migrated to Polars and removed as a dependency (`pyproject.toml`, `requirements.yml`, `requirements-dev.lock.txt`) — confirmed `pip show pandas`'s `Required-by:` was `minerva-analysis` only (not a transitive dependency of anything else) before removal, and the full test suite (`baseline_orion2`, `tests/test_centroid_tiles.py`) plus live endpoint checks were re-run with pandas fully uninstalled from the `minerva` env to confirm. See "Data Layer: Polars, Not Pandas" above for what changed.
- `requirements-dev.lock.txt` must be regenerated with `uv`, never hand-edited: `uv pip compile pyproject.toml --extra jupyter --extra dev --prerelease disallow --universal --python-version 3.12 -o requirements-dev.lock.txt`. The `--universal` flag is required — without it, `uv` resolves only for the machine it's run on and silently drops the other platform's markers (e.g. regenerating on macOS previously dropped the Windows-only `pywin32-ctypes`/`pywinpty`/`pefile` entries that `pyinstaller` needs on Windows, since this repo is used from both Windows and macOS via Dropbox sync). The `--python-version 3.12` flag became necessary at some point after this doc was first written: in `pip compile` mode (as opposed to `uv lock` project mode), `uv` 0.8.14 does not reliably read `requires-python` from `pyproject.toml` and instead falls back to trying to resolve for Python `>=3.10` by default, which now fails outright because `tifffile>=2026.7.31` itself requires `>=3.12` — reproduced against an unmodified checkout, so this is environment/tooling drift, not a project regression. Without the flag you'll see `No solution found when resolving dependencies for split (markers: python_full_version == '3.11.*' ...)`.

Frontend:

- Webpack 5 is used.
- Bootstrap is 5.3.8 (upgraded from 4.6.2), paired with `@popperjs/core` ^2.x (not the old `popper.js` v1). Bootstrap 5 removed `.form-group`, renamed `.ml-*`/`.mr-*` → `.ms-*`/`.me-*`, and `data-toggle` → `data-bs-toggle`; if you find code still using the old names it's a leftover, not intentional.
- jQuery is 4.0.0 (upgraded from 3.7.x). Bootstrap 4 has a hard runtime guard that throws if it detects jQuery ≥4, which is why the Bootstrap and jQuery upgrades had to land in one commit together — they are not independently revertible.
- Babel toolchain is 8.x (`@babel/core`, `@babel/preset-env`, `@babel/plugin-transform-runtime`, `@babel/plugin-transform-class-properties`, `@babel/preset-typescript`, `@babel/runtime` — keep these in lockstep, they're released together). Babel 8 defaults to browserslist-resolved compile targets and ESM output instead of ES5/CJS; `client/.browserslistrc` pins an explicit modern target (Chrome/Firefox/Edge ≥100, Safari ≥15) instead of riding Babel's shifting default. Babel 8 packages declare `engines: node ^22.18.0 || >=24.11.0` — an older local Node (e.g. 22.14.x) produces `EBADENGINE` warnings on `npm install` but has not caused build/test/runtime failures; don't treat that warning alone as a blocker.
- OpenSeadragon is a real npm dependency at 6.1.0 (see "OpenSeadragon Integration" above) — it used to come from a personal-fork chain (`viawebgl`), which has been fully removed.
- D3 is 7.x, FontAwesome is 7.x.
- Browser-side `node-fetch` was removed in favor of native `fetch`.
- The source is not React. Do not describe or treat it as a React app.

## Known Sharp Edges

- `uv build` can create `minerva_analysis.egg-info/` and versioned unpack directories. These are generated.
- Building or installing from the live Dropbox checkout on Windows may hit file-lock issues. A clean temp archive/clone is a better PyPI simulation.
- Running import probes from the repo root can accidentally import the checkout instead of the wheel.
- This checkout is synced via Dropbox between at least one Windows machine and one macOS machine. `minerva_analysis/data/config.json` has separate local datasources per machine — `orion2` (Windows-only files) and `orion_mac` (macOS-local files); `orion` also exists. Use `orion_mac` for local testing/viewing when working on macOS. Two more symptoms of the cross-machine sync to expect and not misdiagnose as real changes:
  - Files can get silently flipped from LF to CRLF line endings (or back) with zero content change, making `git status`/`git diff` show huge diffs on files nobody intentionally edited. Before editing or reviewing a file that shows as heavily modified, check with `git diff --ignore-space-at-eol -- <file>`; if that is empty, it is pure line-ending noise. When you do need to edit a CRLF-flipped file, the `Edit` tool's exact-string match can fail against `\r\n` content — fall back to a small Python script that edits the raw bytes and writes them back with `\n`.join(...) to avoid re-flipping the whole file back to LF as a side effect.
  - Executable bits on synced files (notably `minerva_analysis/client/node_modules/**/bin/*` after `npm install`) can get stripped, causing `npm run start` to fail with `Permission denied` on `webpack`. `chmod +x` the specific binary; `node_modules` is gitignored so this never touches tracked files.
- `conda run -n minerva ...` can fail with `permission denied` from the `__conda_exe` shell function if the invoking shell's `$CONDA_EXE` env var is stale/unset (seen in non-interactive tool shells). If that happens, call the env's Python directly instead, e.g. `~/miniconda3/envs/minerva/bin/python -m compileall ...`, rather than assuming the environment itself is broken.
- `requirements.yml`'s internal `name:` field says `minerva_analysis`, but the conda env actually used for local work (per this doc and in practice) is named `minerva`. Running plain `conda env create -f requirements.yml` will create/target an env called `minerva_analysis`, not `minerva`. To recreate the env under the name actually used, pass `-n minerva` explicitly: `conda env create -n minerva -f requirements.yml` (this also runs `pip install -e .[jupyter,dev]`, since that's baked into the yml's `pip:` section, so no separate install step is needed).
- Missing segmentation tiles may show as browser console messages like `/generated/data/<dataset>/<label-channel>/<level>/<x>_<y>.png`. Confirm whether the tile is truly absent, computed lazily, or blocked by stale frontend cache.
- A segmentation overlay that appears only after hard refresh suggests frontend cache/timing/base-url behavior, not necessarily bad source data.
- `minerva_analysis/data/` is local runtime data. It may contain large datasets and should not be swept into commits.
- Existing uncommitted changes may be user work. Do not revert them unless explicitly asked.

## Git And Release Notes

- Main active remote for current work may be `nirmallab` at `https://github.com/nirmallab/minerva_analysis.git`.
- Upstream/original remote may also exist as `origin` at `https://github.com/labsyspharm/minerva_analysis.git`.
- Check branch and remote before pushing.
- Check `pyproject.toml` (`version = ...`) and `minerva_analysis/client/package.json` (`"version"`) for the current package version before referencing it. They have drifted before (e.g. `pyproject.toml` at `1.0.8` while `package.json` stayed at `1.0.2`) — always re-check both rather than assuming they match or trusting a previously-noted pair of numbers.
- For PyPI readiness, prefer this order:
  1. Run `python -m tests.baseline_orion2`.
  2. Run `npm run start` if frontend changed.
  3. Run `uv build`.
  4. Install the generated wheel into a fresh env and import from outside the repo.
  5. Verify `minerva-analysis-server --help`.

## Agent Operating Notes

- Read the relevant server and frontend files before changing behavior; this project has coupled Python/JS paths.
- Keep edits narrow and preserve old usage paths unless explicitly migrating them.
- Use `rg`/`rg --files` for code discovery.
- Use `apply_patch` for hand edits.
- Before committing, inspect `git status --short` and avoid generated artifacts.
- When reporting results, mention which validation commands were actually run and which were not.

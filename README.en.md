# ModelForge

[简体中文](README.md) ｜ **English**

A model toolchain for Warcraft III. The core, `mdxcore`, implements MDX parsing and **in-place editing**
in pure Python: chunks it does not recognise are passed through untouched, only the fields you changed
are written back, and length plus chunk integrity are verified before anything touches the disk.

Two front ends share that core — `ModelForge` (a local service with a browser UI, packageable into a
double-clickable standalone program) covers configuration work, while the Blender add-on handles
geometry and skinning.

Supports both Classic and Reforged (HD) assets, direct CASC reading, tiered texture loading and live
team-colour switching.

> **Compatibility**: tested against Warcraft III 3.0.0. Earlier versions are untested and not
> guaranteed to work.

## Download

[Latest release](https://github.com/alfredshn000/ModelForge/releases/latest)

- `ModelForge-win64.zip` — unzip and run the `ModelForge.exe` inside. Keep the whole folder together;
  the exe will not start on its own
- `ModelForge.exe` — single file. The first launch unpacks the runtime (~2s); later launches reuse the cache

Requires Windows 10/11 x64 + WebView2 Runtime (bundled with Windows 11, usually present on Windows 10
via Edge). Without it the program still runs, falling back to your default browser. See the
[changelog](CHANGELOG.md) for what changed.

## What it does

| Area | Detail |
|---|---|
| Texture paths | Edit one path or bulk-replace external ones; detects `<root>\` ownership and mixed `\` / `/` separators |
| Materials | Layer filter mode, alpha, normal / ORM / emissive slots |
| Sequence ranges | Start and end in milliseconds, loop flag |
| Attachments | Insert or delete at any position, with full-table renumbering and PIVT sync |
| Mounted weapons | Rewrite the ATCH mount path, verify in-game that it follows the hand |
| Animation transfer | Bone matching (L1–L4 fallbacks), move a whole animation from model A to model B, or unbind it |
| 3D preview | three.js with real geometry, skinning, keyframes and particles; textures load 512→1024 progressively |
| Built-in game models | Read straight from CASC: `war3.w3mod` (3,458 Classic) and `_hd.w3mod` (5,034 Reforged) |

## Three ground rules

1. **Unknown chunks pass through** — new chunks added by newer MDX versions are not dropped just because the parser does not know them
2. **Only edited regions change** — untouched floats keep their original bytes, never recomputed
3. **Verify before writing** — `check_delta` (length unchanged) and `check_chunk_rewrite` (only the target chunk may differ)

## Quick start

```bash
# Run from source (Python 3.10+, no third-party dependencies)
cd mdx-studio
python run.py                          # pick a free port and open the window
python run.py "D:/models/hero.mdx"     # open a specific model

# Package as a standalone program
python -m pip install pyinstaller pywebview
python build.py --zip                  # produces ModelForge-win64.zip
python build.py --single               # single exe (self-extracting launcher)
```

The window backend is the system's WebView2. It also runs without it, falling back to your default
browser with identical functionality.

## Layout

```
mdx-toolkit/mdxcore/     pure Python core, zero dependencies
  container.py           container and chunk table
  chunks/                per-chunk parsing and in-place writing
  casc/                  CASC storage reader (TVFS + local index)
  dds.py blp.py          DXT1/3/5, ATI2 and BLP1 decoding, block-skipping for speed
  paths.py               texture path classification and rewriting
  transfer.py            animation transfer
  nodesedit.py           node table insert/delete and renumbering
  verify.py              pre-write verification

mdx-studio/              standalone editor (local HTTP service + browser front end)
  server.py session.py   API and session
  extract.py             mdx → UI data / render data
  textures.py            three-tier texture lookup with disk cache
  web/                   front end (vanilla JS + three.js r147)
  build.py run.py        launch and packaging

blender-addon/           Blender add-on (geometry and skinning)
docs/                    design notes and measured results (Chinese)
```

## Tests

```bash
cd mdx-studio
python test_server.py                  # backend, 46 checks
python test_webdir.py                  # front-end directory resolution, 14 checks
python test_ui.py                      # real window, real clicks, 82 checks
```

`test_ui.py` needs an actual model as test data. The repository **contains no game assets**
(for copyright reasons); point it at your own `.mdx`:

```bash
MDX_TEST_MODEL="D:/models/hero.mdx" python test_ui.py
```

## Known limitations

- Single-file mode uses a **self-extracting launcher** (an outer exe unpacks the runtime, then starts
  the inner program). Packaging pywebview directly with PyInstaller `--onefile` hangs inside
  `webview.start()` — measured, not theoretical.
- Handles a **single** MDX file; the mdx + mdx2 dual-model setup is not supported.
- BLP2 decoding is not implemented (Classic textures are BLP1, Reforged are DDS).
- Team colour is applied as a mask tint via the ORM texture's alpha channel (the Reforged approach);
  Classic models go through the replaceable-texture path instead.

## References and acknowledgements

The **format research, UI trade-offs and several implementation approaches** in this project reference
[Retera's Model Studio](https://github.com/Retera/ReterasModelStudio) (MIT):

- Field layout and version differences of the MDX chunks, especially MTLS layers and HD texture slots
- Mapping layer FilterMode onto OpenGL blend states
- The replaceable-texture swap path for team colour and team glow (`ReplaceableTextures\TeamColor\TeamColorNN.blp`)
- Viewport interaction feel and panel organisation

Retera's source is not included in this repository; please visit the original repository to compare.

## License

MIT, see [LICENSE](LICENSE).

This repository contains no Warcraft III game assets. Using this project requires that you own the game.

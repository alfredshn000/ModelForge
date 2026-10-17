# Changelog

[简体中文](CHANGELOG.zh-CN.md) ｜ **English**

All notable changes to this project. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] - 2026-10-17

First public release. Windows x64.

### Added

**Core (`mdx-toolkit/mdxcore`, zero dependencies)**

- MDX container and chunk table parsing, with in-place writing: unknown chunks are passed through
  untouched, only edited fields are rewritten, untouched floats keep their original bytes
- Pre-write verification: `check_delta` (length unchanged) and `check_chunk_rewrite` (only the target
  chunk may differ)
- Material layers for all supported versions, including the Reforged HD texture slots (1100/1200/1800)
  and the six-layer Reforged SD layout (1000)
- Sequence ranges, attachments (insert/delete at any position with full-table renumbering and PIVT
  sync), and animation transfer between models with L1–L4 bone matching
- CASC reader for `war3.w3mod` and `_hd.w3mod` (TVFS prefix tree plus a tolerant fallback index)
- Texture decoding: DDS DXT1/3/5, ATI2/BC5, BLP1; block-skipping decode for large sources
- Texture path classification (replaceable / built-in / external) and rewriting that preserves the
  original separator style

**Editor (`mdx-studio`)**

- Local HTTP service with a browser front end; packages into a double-clickable Windows program
- 3D preview: real geometry, skinning, keyframes and particles on three.js r147
- Texture pipeline: three-tier lookup (model directory → same-name file → game CASC), disk cache keyed
  by source hash, tiered loading that shows 512 first and sharpens to 1024 in the background
- Normal and emissive maps, including the Reforged SD per-layer layout
- Live team colour: all 24 player colours read from the game, applied through the ORM alpha mask
- Browsing and opening models straight from the game's CASC (3,458 Classic, 5,034 Reforged)
- Attachment mounting, bulk texture-path rewriting, export dialog with a diff preview
- Chinese and English UI, switchable at runtime
- Packaging: `onedir` build for development and a self-extracting single exe for distribution

**Blender add-on (`blender-addon`)**

- Nine panels for geometry and skinning editing on top of the same core

### Changed

- The single-file build uses a self-extracting launcher instead of PyInstaller `--onefile`, which hangs
  inside `webview.start()` when pywebview is involved. The outer exe unpacks the runtime, then starts the
  inner program.
- Memory caches are evicted by byte budget rather than entry count (a 1024² RGBA texture is 4 MB).

### Fixed

- **Textures reported as missing under concurrent load.** CASC reads are now serialised; a read error is
  no longer cached as "file not found", which previously poisoned the rest of the session.
- **Models without a `MTLS` chunk failed to open.** Read paths now tolerate missing optional chunks;
  write paths still refuse.
- **Untextured meshes rendered pure white** while textures were still streaming in; they now start from
  a neutral grey and switch to the texture when it arrives.
- Textures and thumbnails were capped at 512 (and 256 on the short edge), which looked blurry for HD
  models; textures now load in two tiers and reach 1024.
- A blank row in the texture list for replaceable entries; they are now labelled and show the actual
  team colour.

[Unreleased]: https://github.com/alfredshn000/ModelForge/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/alfredshn000/ModelForge/releases/tag/v0.1.0

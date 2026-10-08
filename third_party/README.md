# Third-party and public asset notices

Application source uses the root [MIT license](../LICENSE). Dependencies and fonts retain their licenses. MIT does not replace those licenses or grant additional rights to third-party services, models, user imports or generated artwork.

## Locked dependencies

Python versions and artifact hashes are frozen in [uv.lock](../uv.lock), with a hash-checked pip fallback in [requirements-lock.txt](../requirements-lock.txt). Frontend versions are frozen in [package-lock.json](../src/web/package-lock.json). Current installed metadata is recorded in [dependencies.json](dependencies.json): 30 runtime Python packages and 284 frontend packages including build dependencies. License files accompany installed distributions.

The role engine uses [deepseek-harness-sdk 0.1.5rc1](https://pypi.org/project/deepseek-harness-sdk/0.1.5rc1/) and its same-version runtime (MIT). Backend dependencies include FastAPI (MIT), Uvicorn (BSD-3-Clause), Pydantic (MIT), HTTPX (BSD-3-Clause), Pillow (MIT-CMU), Cryptography (Apache-2.0 OR BSD-3-Clause), sqlite-vec (MIT / Apache-2.0), and python-multipart (Apache-2.0). Python transitive licenses include certifi (MPL-2.0), regex (Apache-2.0 AND CNRI-Python), typing-extensions (PSF-2.0), and cffi (MIT-0). Dependencies are installed without modifying or relicensing their files.

React, React DOM, React Router, TanStack Query, Radix UI, XYFlow, Zustand and Phosphor Icons are MIT; Lucide and qrcode.react are ISC. QR generator attribution remains in qrcode.react. Build tools retain MIT, ISC, 0BSD, BSD-3-Clause or Apache-2.0 licenses. Development-only caniuse-lite compatibility data is CC-BY-4.0; its authors and upstream attribution remain in the installed package, sourced through [caniuse-lite](https://github.com/browserslist/caniuse-lite).

No source from SillyTavern (AGPL), RisuAI (GPL), or BotSearcher (AGPL) is bundled. Imported characters, worlds and chats are user data outside this repository.

## Fonts

Bundled, unmodified fonts use SIL Open Font License 1.1: LXGW WenKai Lite, ZCOOL XiaoWei, ZCOOL QingKe HuangYou and Ma Shan Zheng. [fonts.json](fonts.json) records upstream commits, URLs, hashes and license URLs. Original copyright/license notices remain in `src/web/public/fonts/*-OFL.txt`. Fonts are optional interface resources, not separately sold font products.

## Public interface artwork

[public-assets.json](public-assets.json) pins binary names and SHA-256 hashes. These are product decorations, empty-state illustrations, frame effects and optional cover-selection images. They contain no user roleplay material. Application SVG/CSS follows the source license. AI-generated raster decorations remain subject to generation service terms; no exclusive copyright or additional third-party authorization is asserted.

Picturebook and Moonweave v1 have adjacent `manifest.json` files with generation constraints, no-reference-image/private-input declarations and delivery hashes. Production Moonweave ui-v3 has an `asset-files.json` inventory. Earlier emblem, wordmark and astral-theme images are public product decorations; detailed generation records for those and ui-v3 are not distributed with this code-only release. An inventory alone is not an independent chain-of-title assertion. Original generation outputs, development mockups, user media and acceptance screenshots are excluded.

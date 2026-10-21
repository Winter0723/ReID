# Copyright and third-party notices

Original MyReID contributions: Copyright (C) 2026 MyReID contributors.
The project is licensed under **AGPL-3.0-only**, see [LICENSE](LICENSE).
Contributors retain copyright in their own contributions. Existing third-party
notices and licenses remain applicable to their respective files.

| Component | License / source |
| --- | --- |
| Ultralytics code and YOLO models | [AGPL-3.0 / enterprise terms](https://www.ultralytics.com/license); this project uses the open-source option |
| Adapted CLIP-ReID code | [model/NOTICE.md](model/NOTICE.md), [upstream MIT license](model/LICENSE.CLIP-REID) |
| OpenAI CLIP | [retained MIT license](model/LICENSE.OPENAI-CLIP); [upstream](https://github.com/openai/CLIP) |
| Bundled music | File-specific attribution, source and license in [LICENSE_SOURCES.md](FastAPIBackend/assets/scenic_music/LICENSE_SOURCES.md) |
| Application dependencies | Each Python, npm and Rust dependency retains its own license; lockfiles identify installed versions |

Model checkpoints and training datasets are **not licensed by this project**.
They are excluded from the public source snapshot. Obtain them from their
respective upstreams and check model and dataset terms for the intended use.
See [model/README.md](model/README.md). An AGPL license on application code does
not grant rights to third-party images, personal recordings or training data.

When operating a modified network service, provide its corresponding source to
users as required by AGPL section 13. Include these notices when redistributing.
There is no warranty; see the full license text.

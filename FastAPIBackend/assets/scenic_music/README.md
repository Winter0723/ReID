# Scenic 本地配乐

`app/services/scenic_templates.py` 提供三首从作者作品页下载的 CC0 器乐，以及原有三首程序合成演示配乐。下载音乐随应用保存在 `cc0-v1/`，演示配乐在本地生成；列出、试听和生成成片均不需要访问在线音乐网站。

## 内置 CC0 音乐

以下三首均于 **2026-09-10** 下载并核对作品页许可，采用 **CC0 1.0 Universal**。可剪辑、商用、修改和再分发，无需署名，也可随应用分发音频文件。完整条款见 [CC0 官方说明](https://creativecommons.org/publicdomain/zero/1.0/)及[法律全文](https://creativecommons.org/publicdomain/zero/1.0/legalcode.en)。

| 曲目 | 原作者 | 应用内分类 | 作者发布的作品页 | 原始下载 |
| --- | --- | --- | --- | --- |
| Revelation · 静谧旅途 | Indieteur | 旅行纪实 | [Revelation](https://opengameart.org/content/revelation) | [MP3](https://opengameart.org/sites/default/files/Revelation_0.mp3) |
| Happy Beat · 轻快脚步 | burabotti | 轻快打卡 | [Happy Beat](https://opengameart.org/content/happy-beat) | [OGG](https://opengameart.org/sites/default/files/happybeat.ogg) |
| Happy Moments · 幸福时光（原作 Happy moments，1.4 版） | Centurion_of_war | 温情回忆 | [Happy moments](https://opengameart.org/content/happy-moments) | [OGG](https://opengameart.org/sites/default/files/piano1.4_0.ogg) |

分类和中文副标题由本项目根据作者描述及情绪、乐器标签拟定，供选择配乐参考，不是作者提供的用途分类。下载的音乐不套用演示配乐的固定节拍网格。

Revelation 保留原始 MP3 字节；Happy Beat 和 Happy moments 使用 FFmpeg 从 OGG 转为 **48 kHz、双声道、192 kbps MP3**，未裁剪。分发文件分别为 `cc0-v1/oga_revelation.mp3`、`cc0-v1/oga_happy_beat.mp3` 和 `cc0-v1/oga_happy_moments.mp3`。

逐曲来源、下载日期、转换记录和版本记录在 [catalog.json](catalog.json)。其中 `source_sha256` 是原始下载文件的 SHA-256，`sha256` 是随应用分发文件的 SHA-256；转码后两个值不同属于预期。服务使用配乐前会校验分发文件。作品页的简短许可摘录及校验值另见 [LICENSE_SOURCES.md](LICENSE_SOURCES.md)。保留这些信息便于核对来源，不要求在每个成片中加入作者署名。

## 原创程序合成演示配乐

原有三首仍然可用：山间晨光（82 BPM）、轻快脚步（116 BPM）、留念时光（66 BPM）。

来源：本项目的确定性正弦谐波、包络、和弦及节拍程序。未使用录音、下载采样、商业曲谱、生成模型或云服务。版本 1 的三首音乐在首次调用时生成 48 kHz、16-bit 双声道 WAV，并缓存于对象存储目录的 `.scenic_music/1`。不将几十 MB 的可再生音频加入 Git。

使用范围：这三首原创音乐输出以 CC0 1.0 Universal 方式提供，可剪辑、商用、修改和再分发，无需署名。完整条款：[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/)。这仅针对本项目原创音乐输出，不改变应用程序其他文件的许可。外部音乐沿用各自作者在作品页给出的 CC0 许可。

音乐以明确节拍网格合成，因此直接缓存作曲时的节拍时间，避免对已知节奏做有误差的重复估算。

## 版本与成片复现

每首音乐的音频 SHA-256、版本和来源通过音乐目录接口提供并写入剪辑快照；演示配乐还包含作曲节拍数据。旧成片必须保留其对应音频及缓存。更换演示合成程序、旋律或音色应提升 `MUSIC_VERSION` 并保留旧版解析能力；更换下载音频或转码方式时，应新增曲目 ID、文件路径及校验值，保留原目录条目及音频，不得覆写已被提交版本引用的资源。

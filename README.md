# text-portal-reveal · 泼墨文字穿越

用视频填充文字笔画，制作黑底文字开场或反向收拢结尾。默认使用三极泼墨体，输出 MP4 和可修改的 HyperFrames 工程。底层视频与原声持续正向播放。

## 安装

```bash
npx -y skills add jacob6re/text-portal-reveal -g --all
```

## 使用

给 Agent 一段本地视频，再说：

> 使用 $text-portal-reveal，文字是“出发”，先停留 0.6 秒，再用 1.5 秒放大展开至全屏。

或制作结尾：

> 使用 $text-portal-reveal，先正常播放视频，最后收拢成“Jake边做边学”，并保持文字窗口 0.7 秒。

“反向”指遮罩转场方向；素材与音频不会倒放。

## 环境与脚本

需要 Python 3.9+、FFmpeg/ffprobe、Node.js 22+。在专用虚拟环境安装依赖：

```bash
python -m pip install -r skills/text-portal-reveal/scripts/requirements.txt
python skills/text-portal-reveal/scripts/build.py --video source.mp4 --text '出发' --out opening
python skills/text-portal-reveal/scripts/build.py --video source.mp4 --text 'Jake边做边学' --direction conceal --hold 0.7 --text-width 0.78 --out ending
```

在输出工程目录执行：

```bash
npm run check
npm run render -- --output result.mp4
```

渲染固定 HyperFrames 0.8.161；生成时使用 `--fps 60`，工程的渲染命令也会保留 60 fps。输出目录必须不存在。

## 默认字体

首次使用自动从公开来源下载约 20 MB 的三极泼墨体到工程旁的缓存目录，并核对固定 SHA-256；后续复用缓存。也可用 `--font /path/to/font.ttf` 选择已有字体。字体版权归三极字库，字体文件不随仓库重新分发。来源与说明见 [font-source.md](skills/text-portal-reveal/references/font-source.md)。

## 边界

支持中文、拉丁文与数字的单行文字。复杂连写、双向文字及组合字符尚不支持。HDR/HLG 视频需先正确色调映射为 SDR。改变画幅会居中裁切，请检查主体。

视频处理在本地进行；字体下载和工具安装需要网络，不上传输入视频。仓库不包含个人视频、测试答案或运行缓存。项目内 GSAP 保留原授权，字体使用范围以版权方说明为准。

#!/usr/bin/env python3
"""Build a seek-safe video-filled text portal; never overwrite an output folder."""
import argparse
import html
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unicodedata

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont, TTLibError
from fetch_default_font import fetch_font


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(result.stderr.strip()[-2500:] or f'{command[0]} failed')
    return result.stdout


def probe(video):
    data = json.loads(run(['ffprobe', '-v', 'error', '-show_streams',
                           '-show_format', '-of', 'json', str(video)]))
    stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
    if stream is None:
        raise ValueError('Input has no video stream.')
    if stream.get('color_transfer') in ('smpte2084', 'arib-std-b67'):
        raise ValueError('HDR input needs an SDR tone-mapped copy before using this builder.')
    width, height = stream['width'], stream['height']
    rotation = float(stream.get('tags', {}).get('rotate', 0))
    for side in stream.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    if round(rotation) % 180:
        width, height = height, width
    # FFmpeg autorotates before scale/crop. Normalize non-square sample pixels too.
    sar = stream.get('sample_aspect_ratio', '1:1')
    if sar not in ('N/A', '0:1'):
        num, den = (float(x) for x in sar.split(':'))
        if den:
            if round(rotation) % 180:
                height = round(height * num / den)
            else:
                width = round(width * num / den)
    duration = float(stream.get('duration') or data['format']['duration'])
    return width, height, duration, any(s['codec_type'] == 'audio' for s in data['streams'])


def largest_ink_square(mask):
    """Find an entirely opaque square, with a central tie-break; holes are excluded."""
    ink = np.asarray(mask) >= 254
    integral = np.pad(ink.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    low, high, best = 1, min(ink.shape), None
    while low <= high:
        side = (low + high) // 2
        sums = (integral[side:, side:] - integral[:-side, side:]
                - integral[side:, :-side] + integral[:-side, :-side])
        ys, xs = np.where(sums == side * side)
        if len(xs):
            centers_x, centers_y = xs + (side - 1) / 2, ys + (side - 1) / 2
            distance = ((centers_x - ink.shape[1] / 2) ** 2
                        + (centers_y - ink.shape[0] / 2) ** 2)
            pick = int(np.argmin(distance))
            best = float(centers_x[pick]), float(centers_y[pick]), side
            low = side + 1
        else:
            high = side - 1
    if best is None or best[2] < 6:
        raise ValueError('Text strokes are too thin: use a heavier font, fewer letters, or larger text.')
    return best


def letter_geometry(text, font_path, font_index, width, height, text_width):
    if any(unicodedata.combining(c) or unicodedata.bidirectional(c) in ('R', 'AL', 'AN')
           or unicodedata.category(c) == 'Cf' for c in text):
        raise ValueError('Complex shaping/bidirectional/combining text is not supported by this builder.')
    font = TTFont(str(font_path), fontNumber=font_index)
    try:
        cmap, glyphs = font.getBestCmap(), font.getGlyphSet()
        missing = [c for c in text if ord(c) not in cmap]
        if missing:
            raise ValueError('Font has no glyph for: ' + ''.join(dict.fromkeys(missing)))
        units = font['head'].unitsPerEm
        cursor, boxes, positioned = 0, [], []
        for char in text:
            name = cmap[ord(char)]
            bounds_pen = BoundsPen(glyphs)
            glyphs[name].draw(bounds_pen)
            if bounds_pen.bounds:
                x0, y0, x1, y1 = bounds_pen.bounds
                boxes.append((cursor + x0, y0, cursor + x1, y1))
            positioned.append((char, name, cursor))
            cursor += font['hmtx'][name][0]
        if not boxes:
            raise ValueError('Text contains no visible glyphs.')
        left, bottom = min(b[0] for b in boxes), min(b[1] for b in boxes)
        right, top = max(b[2] for b in boxes), max(b[3] for b in boxes)
        size = math.floor(min(width * text_width / (right - left),
                              height * .28 / (top - bottom)) * units)
        if size < 12:
            raise ValueError('Text is too long for this canvas. Shorten it.')
        k = size / units
        x = width / 2 - (left + right) * k / 2
        baseline = height / 2 + (top + bottom) * k / 2
        raster_font = ImageFont.truetype(str(font_path), size, index=font_index)
        mask = Image.new('L', (width, height), 0)
        draw = ImageDraw.Draw(mask)
        paths = []
        for char, name, advance in positioned:
            px = x + advance * k
            draw.text((px, baseline), char, font=raster_font, fill=255, anchor='ls')
            pen = SVGPathPen(glyphs)
            glyphs[name].draw(TransformPen(pen, (k, 0, 0, -k, px, baseline)))
            paths.append(pen.getCommands())
        ax, ay, side = largest_ink_square(mask)
        # Two-pixel inward margin plus 25% scale headroom absorbs raster hinting.
        radius = (side - 4) / 2
        scale = max(width, height) / (2 * radius) * 1.25
        return ''.join(paths), mask, (ax, ay), side, scale
    finally:
        font.close()


def document(path, config):
    w, h, duration = config['width'], config['height'], config['duration']
    ax, ay = config['anchor']
    s = config['end_scale']
    end_matrix = f'matrix({s:.8f},0,0,{s:.8f},{w / 2 - s * ax:.8f},{h / 2 - s * ay:.8f})'
    identity = 'matrix(1,0,0,1,0,0)'
    if config['direction'] == 'conceal':
        initial, final = end_matrix, identity
        transition_start = duration - config['hold'] - config['zoom']
        ease = 'power3.out'
        cleanup = ''
    else:
        initial, final = identity, end_matrix
        transition_start = config['hold']
        ease = 'power3.in'
        cleanup = f"tl.set('#veil', {{opacity: 0}}, {config['hold'] + config['zoom']});"
    audio = (f'<audio id="source-audio" src="assets/source.mp4" data-start="0" '
             f'data-duration="{duration}" data-track-index="2" data-volume="1"></audio>'
             if config['audio'] else '')
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width={w}, height={h}">
<title>{html.escape(config['text'])} · 文字穿越</title>
<script src="assets/gsap.min.js"></script>
<style>
html,body{{margin:0;width:100%;height:100%;background:#000;overflow:hidden}}
#root{{position:relative;width:100%;height:100%;overflow:hidden;background:#000}}
#footage,#veil-clip{{position:absolute;inset:0;width:100%;height:100%}}
#footage{{object-fit:cover}}
#veil-clip{{pointer-events:none;z-index:1}}
</style></head><body>
<div id="root" data-composition-id="text-portal-reveal" data-width="{w}" data-height="{h}" data-duration="{duration}">
<video id="footage" class="clip" src="assets/source.mp4" data-start="0" data-duration="{duration}" data-track-index="0" muted playsinline></video>
{audio}
<svg id="veil-clip" class="clip" data-start="0" data-duration="{duration}" data-track-index="1" viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" data-layout-ignore>
<defs><mask id="portal-mask" x="0" y="0" width="{w}" height="{h}" maskUnits="userSpaceOnUse" maskContentUnits="userSpaceOnUse" style="mask-type:luminance">
<rect width="{w}" height="{h}" fill="white"/>
<g id="portal-path" transform="{initial}"><path d="{path}" fill="black" fill-rule="nonzero"/></g>
</mask></defs>
<g id="veil"><rect width="{w}" height="{h}" fill="black" mask="url(#portal-mask)"/></g>
</svg></div>
<script>
const tl = gsap.timeline({{ paused: true }});
tl.fromTo('#portal-path', {{attr: {{transform: '{initial}'}}}},
  {{attr: {{transform: '{final}'}}, duration: {config['zoom']}, ease: '{ease}'}}, {transition_start});
{cleanup}
window.__timelines['text-portal-reveal'] = tl;
</script></body></html>
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--video', required=True, type=Path)
    p.add_argument('--text', required=True)
    p.add_argument('--direction', choices=['reveal', 'conceal'], default='reveal',
                   help='reveal: text expands to full video; conceal: full video contracts to text at the end')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--font', type=Path)
    p.add_argument('--font-index', type=int, default=0)
    p.add_argument('--hold', type=float, default=.6)
    p.add_argument('--zoom', type=float, default=1.5)
    p.add_argument('--start', type=float, default=0)
    p.add_argument('--duration', type=float)
    p.add_argument('--width', type=int)
    p.add_argument('--height', type=int)
    p.add_argument('--fps', type=int, default=30)
    p.add_argument('--text-width', type=float, default=.45)
    p.add_argument('--mute', action='store_true')
    a = p.parse_args()
    temporary = None
    try:
        if a.out.exists() or a.out.is_symlink():
            raise ValueError('Output already exists; choose a new directory.')
        if not a.video.is_file():
            raise ValueError('Source video does not exist.')
        if not a.text.strip() or '\n' in a.text or '\r' in a.text:
            raise ValueError('Provide non-empty, single-line text.')
        numeric = [a.hold, a.zoom, a.start, a.text_width]
        if a.duration is not None:
            numeric.append(a.duration)
        if not all(math.isfinite(n) for n in numeric):
            raise ValueError('Numeric parameters must be finite.')
        if a.hold < 0 or a.zoom <= 0 or a.start < 0:
            raise ValueError('hold/start must be nonnegative and zoom positive.')
        if not .1 <= a.text_width <= .9 or not 1 <= a.fps <= 60:
            raise ValueError('text-width must be 0.1–0.9; fps must be 1–60.')
        if (a.width is None) != (a.height is None):
            raise ValueError('Supply both width and height.')
        if a.font_index < 0:
            raise ValueError('font-index must be nonnegative.')
        for exe in ('ffmpeg', 'ffprobe'):
            if not shutil.which(exe):
                raise ValueError(f'Missing executable: {exe}')
        sw, sh, available, has_audio = probe(a.video)
        w = a.width if a.width is not None else sw
        h = a.height if a.height is not None else sh
        w, h = w // 2 * 2, h // 2 * 2
        if not 64 <= min(w, h) or max(w, h) > 4096:
            raise ValueError('Canvas dimensions must be 64–4096 pixels.')
        requested = a.duration if a.duration is not None else available - a.start
        if requested > available - a.start + .001:
            raise ValueError('Requested range exceeds available source video.')
        # Whole output frames, never extend a short source by silently freezing it.
        duration = math.floor((requested + 1e-7) * a.fps) / a.fps
        if duration < a.hold + a.zoom + .2:
            raise ValueError('Source range too short: need hold + zoom + at least 0.2 seconds.')
        font = a.font or fetch_font(a.out.parent / '.text-portal-fonts')
        if font is None or not font.is_file():
            raise ValueError('Font file not found. Supply --font with a TTF/OTF/TTC file.')
        paths, mask, anchor, side, scale = letter_geometry(
            a.text, font, a.font_index, w, h, a.text_width)
        config = dict(text=a.text, direction=a.direction, font=font.name, font_index=a.font_index,
                      width=w, height=h, fps=a.fps, duration=duration, hold=a.hold,
                      zoom=a.zoom, source_start=a.start, audio=has_audio and not a.mute,
                      anchor=list(anchor), safe_square_side=side, end_scale=scale,
                      text_width=a.text_width, hyperframes_version='0.8.161')
        a.out.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.portal-', dir=a.out.parent))
        assets = temporary / 'assets'
        assets.mkdir()
        shutil.copy2(Path(__file__).resolve().parents[1] / 'assets/gsap.min.js', assets)
        mask.save(assets / 'initial-mask.png')
        command = ['ffmpeg', '-v', 'error', '-ss', str(a.start), '-i', str(a.video),
                   '-t', str(duration), '-map', '0:v:0']
        command += ['-map', '0:a:0?'] if config['audio'] else ['-an']
        command += ['-vf', f'scale={w}:{h}:force_original_aspect_ratio=increase:out_color_matrix=bt709:out_range=tv,crop={w}:{h},setsar=1',
                    '-r', str(a.fps), '-c:v', 'libx264', '-crf', '18', '-preset', 'fast',
                    '-pix_fmt', 'yuv420p', '-colorspace', 'bt709', '-color_primaries', 'bt709',
                    '-color_trc', 'bt709', '-color_range', 'tv',
                    '-c:a', 'aac', '-movflags', '+faststart',
                    str(assets / 'source.mp4')]
        run(command)
        # Confirm the normalized media really spans the requested composition.
        _, _, actual_duration, _ = probe(assets / 'source.mp4')
        if actual_duration + .5 / a.fps < duration:
            raise ValueError('Decoded source ended early; shorten the requested range.')
        (temporary / 'index.html').write_text(document(paths, config), encoding='utf-8')
        (temporary / 'portal.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        (temporary / 'hyperframes.json').write_text(json.dumps({
            '$schema': 'https://hyperframes.heygen.com/schema/hyperframes.json',
            'paths': {'assets': 'assets'}, 'media': {'autoProxy': True},
        }, indent=2) + '\n', encoding='utf-8')
        (temporary / 'package.json').write_text(json.dumps({
            'name': 'text-portal-reveal-project', 'private': True,
            'scripts': {key: f'npx --yes hyperframes@0.8.161 {value}'
                        for key, value in [('check', 'check'), ('preview', 'preview'),
                                           ('render', f'render --quality looks --fps {a.fps}')]},
        }, indent=2) + '\n', encoding='utf-8')
        if a.out.exists() or a.out.is_symlink():
            raise ValueError('Output appeared while building; refusing to overwrite it.')
        temporary.rename(a.out)
        temporary = None
        print(json.dumps({'ok': True, 'project': str(a.out.resolve()), **config}, ensure_ascii=False))
    except (ValueError, OSError, KeyError, TTLibError, IndexError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

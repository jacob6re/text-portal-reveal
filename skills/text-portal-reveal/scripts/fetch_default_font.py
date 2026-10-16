#!/usr/bin/env python3
"""Fetch the pinned SanJi PoMo font into a task-local cache, without redistribution."""
import argparse
import hashlib
from pathlib import Path
import tempfile
import urllib.request

FONT_NAME = 'SanJiPoMoTi.ttf'
FONT_URL = 'https://resources.17font.com/all_font/44a93850-52ab-11ee-8f96-27ccb52e885c.ttf'
FONT_SHA256 = 'd81fdc804502e05874c7d6089c356939fc349e32c2f280ed8b6ce47e46f1cde4'


def matches(path):
    if not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest() == FONT_SHA256


def fetch_font(cache_dir):
    cache_dir = Path(cache_dir)
    destination = cache_dir / FONT_NAME
    if matches(destination):
        return destination
    cache_dir.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        request = urllib.request.Request(FONT_URL, headers={'User-Agent': 'text-portal-reveal/1.0'})
        with urllib.request.urlopen(request, timeout=45) as response:
            with tempfile.NamedTemporaryFile(dir=cache_dir, suffix='.download', delete=False) as stream:
                temporary = Path(stream.name)
                count = 0
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    count += len(block)
                    if count > 30 * 1024 * 1024:
                        raise ValueError('Default font download exceeds the expected size.')
                    stream.write(block)
        if not matches(temporary):
            raise ValueError('Default font checksum mismatch; refusing to use changed content.')
        temporary.replace(destination)
        temporary = None
        return destination
    except OSError as error:
        raise ValueError('Cannot fetch default SanJi PoMo font. Retry with network access or supply --font.') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir', required=True, type=Path)
    args = parser.parse_args()
    print(fetch_font(args.cache_dir))

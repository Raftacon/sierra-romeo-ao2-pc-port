"""Compose the skip prompt from the local game's font and controller glyphs.

First export 00_fonts.xxx with UE Viewer -export -groups to artifacts/font-groups.
No substitute font or downloaded game art is used.
"""
from pathlib import Path
import struct
from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
data = (root / 'assets/AO2Game/CookedXenon/00_fonts.xxx').read_bytes()
def integer(offset):
    return struct.unpack_from('>I', data, offset)[0]

assert data[:8].hex() == '9e2a83c1004f01bd', 'Unexpected font package revision'
names, cursor = [], 0x65
for _ in range(51):
    length = integer(cursor)
    names.append(data[cursor + 4:cursor + 3 + length].decode('ascii'))
    cursor += 4 + length + 8

def properties(cursor):
    result = {}
    while True:
        name = names[integer(cursor)]
        cursor += 8
        if name == 'None':
            return result, cursor
        kind, length = names[integer(cursor)], integer(cursor + 8)
        cursor += 16
        result[name] = (kind, data[cursor:cursor + length], cursor)
        cursor += length

def characters(offset):
    props, _ = properties(offset + 4)  # serialized NetIndex precedes properties
    _, payload, cursor = props['Characters']
    count = integer(cursor)
    cursor += 4
    chars = []
    for _ in range(count):
        item, cursor = properties(cursor)
        chars.append({key: int.from_bytes(value[1], 'big') for key, value in item.items()})
    return chars

if __name__ == '__main__':
    chars = characters(0x70DA3)  # United_Sans_Rg_Md, revision-pinned export
    xbox = characters(0x8F4E3)
    output = root / 'artifacts/branding'
    output.mkdir(exist_ok=True, parents=True)
    (output / 'font-characters.txt').write_text('\n'.join(f'{i}: {x}' for i, x in enumerate(xbox)))
    atlas = Image.open(root / 'artifacts/font-groups/00_fonts/United_Sans_Rg_Md/United_Sans_Rg_Md_PageA.tga').convert('RGBA')
    # A contact sheet makes the original controller character mapping reviewable.
    sheet = Image.new('RGBA', (640, 640), '#303030')
    draw = ImageDraw.Draw(sheet)
    pages = [Image.open(root / f'artifacts/font-groups/00_fonts/Xbox360_18pt/Texture2D_{i}.tga').convert('RGBA') for i in (2, 3)]
    for i, char in enumerate(xbox[:256]):
        u, v, w, h, page = [char.get(k, 0) for k in ('StartU', 'StartV', 'USize', 'VSize', 'TextureIndex')]
        if w and h and page < len(pages):
            glyph = pages[page].crop((u, v, u + w, v + h))
            sheet.alpha_composite(glyph, ((i % 16) * 40, (i // 16) * 40))
        draw.text(((i % 16) * 40, (i // 16) * 40 + 26), str(i), fill='white')
    sheet.save(output / 'controller-glyphs.png')
    font_pages = [Image.open(root / f'artifacts/font-groups/00_fonts/United_Sans_Rg_Md/United_Sans_Rg_Md_Page{p}.tga').convert('RGBA') for p in ('A', 'B')]
    def glyph(char, pages):
        u, v, w, h, page = [char.get(k, 0) for k in ('StartU', 'StartV', 'USize', 'VSize', 'TextureIndex')]
        return pages[page].crop((u, v, u + w, v + h))
    prompt = Image.new('RGBA', (256, 40))
    x = 4
    button_x = 0
    # Leave space outside the progress ring and the wider keyboard key.
    for item in ['H', 'o', 'l', 'd', 13, None, 13, 't', 'o', ' ', 'S', 'k', 'i', 'p']:
        if isinstance(item, int):
            x += item
            continue
        if item == ' ':
            x += 7
            continue
        bitmap = glyph(xbox[66], pages) if item is None else glyph(chars[ord(item)], font_pages)
        if item is None:
            button_x = x + bitmap.width // 2
        prompt.alpha_composite(bitmap, (x, (40 - bitmap.height) // 2))
        x += bitmap.width + 1
    prompt = prompt.crop((0, 0, x + 4, 40))
    prompt.save(output / 'skip-prompt.png')
    (output / 'skip-prompt.rgba').write_bytes(struct.pack('<4I', prompt.width, prompt.height, button_x, 20) + prompt.tobytes())
    key = Image.open(root / 'resources/input/keyboard_f_outline.png').convert('RGBA')
    key = key.crop(key.getbbox()).resize((24, 26), Image.Resampling.LANCZOS)
    pad = glyph(xbox[66], pages)
    left = button_x - pad.width // 2
    prompt.paste((0, 0, 0, 0), (left, 0, left + pad.width, 40))
    prompt.alpha_composite(key, (button_x - 12, 7))
    prompt.save(output / 'skip-prompt-keyboard.png')
    (output / 'skip-prompt-keyboard.rgba').write_bytes(struct.pack('<4I', prompt.width, prompt.height, button_x, 20) + prompt.tobytes())
    print('Parsed', len(chars), 'font characters and', len(xbox), 'controller characters')

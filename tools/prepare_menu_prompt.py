"""Build PC menu labels from the locally exported original game font atlas."""
import struct
from PIL import Image
from prepare_skip_prompt import root, characters

font = characters(0x70DA3)
xbox = characters(0x8F4E3)
font_pages = [Image.open(root / f'artifacts/font-groups/00_fonts/United_Sans_Rg_Md/United_Sans_Rg_Md_Page{p}.tga').convert('RGBA') for p in ('A', 'B')]
pad_pages = [Image.open(root / f'artifacts/font-groups/00_fonts/Xbox360_18pt/Texture2D_{i}.tga').convert('RGBA') for i in (2, 3)]

def render(name, items, text_font=font, text_pages=font_pages):
    bitmap = Image.new('RGBA', (1024, 40))
    x = 4
    for item in items:
        if item == ' ':
            x += 7
            continue
        if isinstance(item, Image.Image):
            tile = item
            w, h = tile.size
        else:
            glyph = xbox[item] if isinstance(item, int) else text_font[ord(item)]
            pages = pad_pages if isinstance(item, int) else text_pages
            u, v, w, h, page = [glyph.get(k, 0) for k in ('StartU', 'StartV', 'USize', 'VSize', 'TextureIndex')]
            tile = pages[page].crop((u, v, u + w, v + h))
        bitmap.alpha_composite(tile, (x, (40 - h) // 2))
        x += w + 1
    bitmap = bitmap.crop((0, 0, x + 4, 40))
    output = root / 'artifacts/branding' / name
    bitmap.save(output.with_suffix('.png'))
    output.with_suffix('.rgba').write_bytes(struct.pack('<4I', bitmap.width, bitmap.height, 0, 0) + bitmap.tobytes())

if __name__ == '__main__':
    def key(name):
        image = Image.open(root / 'resources/input' / (name + '.png')).convert('RGBA')
        image = image.crop(image.getbbox())
        return image.resize((round(image.width * 26 / image.height), 26), Image.Resampling.LANCZOS)
    # The original Select/Back draw binds this semi-extended atlas, verified
    # from the captured UI draw and the local texture export.
    menu_font = characters(0x8527B)  # United_Sans_Semi_Ext_Md_Sz_14
    menu_pages = [Image.open(root / f'artifacts/font-groups/00_fonts/United_Sans_Semi_Ext_Md_Sz_14/Texture2D_{p}.tga').convert('RGBA') for p in (15, 16)]
    render('exit-prompt', [62, ' ', *'Exit'], menu_font, menu_pages)
    render('exit-prompt-keyboard', [key('keyboard_escape_outline'), ' ', *'Exit'], menu_font, menu_pages)
    render('pc-settings-controls', [65, ' ', *'Apply', ' ', ' ', 66, ' ', *'Cancel', ' ', ' ', 88, ' ', *'Defaults'])
    render('pc-settings-controls-keyboard', [key('keyboard_e_outline'), ' ', *'Apply', ' ', ' ', key('keyboard_f_outline'), ' ', *'Cancel', ' ', ' ', key('keyboard_r_outline'), ' ', *'Defaults'])
    # A bitmap font allows changing PC option values without replacing the
    # original game's font with a host/system typeface.
    width = max(p.width for p in font_pages)
    height = sum(p.height for p in font_pages)
    atlas = Image.new('RGBA', (width, height))
    offsets, y = [], 0
    for page in font_pages:
        offsets.append(y)
        atlas.alpha_composite(page, (0, y))
        y += page.height
    metrics = bytearray()
    for char in font:
        u, v, w, h, page = [char.get(k, 0) for k in ('StartU', 'StartV', 'USize', 'VSize', 'TextureIndex')]
        metrics.extend(struct.pack('<4I', u, v + offsets[page], w, h))
    (root / 'artifacts/branding/pc-font.rgba').write_bytes(struct.pack('<4I', width, height, len(font), 0) + metrics + atlas.tobytes())

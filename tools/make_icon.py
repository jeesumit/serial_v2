"""Regenerate icons from the company logo (needs Pillow):  python tools/make_icon.py
assets/logo_source.jpg -> assets/icon.png + assets/icon.ico (red emblem on a white tile) + assets/header_logo.png"""
from PIL import Image, ImageDraw
src = Image.open("assets/logo_source.jpg").convert("RGB"); W, H = src.size; px = src.load()

def bbox(pred, x0, y0, x1, y1):
    xs, ys = [], []
    for y in range(y0, y1):
        for x in range(x0, x1):
            if pred(px[x, y]): xs.append(x); ys.append(y)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1

ex0, ey0, ex1, ey1 = bbox(lambda p: p[0] > 170 and p[1] < 90 and p[2] < 90, 0, 0, W // 3, H)    # red emblem
emb = src.crop((ex0, ey0, ex1, ey1))
S = 1024
tile = Image.new("RGBA", (S, S), (0, 0, 0, 0))
ImageDraw.Draw(tile).rounded_rectangle((0, 0, S - 1, S - 1), radius=190, fill=(255, 255, 255, 255))
k = 0.74 * S / max(emb.size)
emb = emb.resize((int(emb.width * k), int(emb.height * k)), Image.LANCZOS)
tile.paste(emb, ((S - emb.width) // 2, (S - emb.height) // 2))
tile.save("assets/icon.png")
tile.save("assets/icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

hx0, hy0, hx1, hy1 = bbox(lambda p: min(p) < 200, 0, 0, W, int(H * 0.68))                         # emblem + Kubota wordmark
hl = src.crop((max(0, hx0 - 6), max(0, hy0 - 6), min(W, hx1 + 6), min(H, hy1 + 6)))
hl = hl.resize((int(hl.width * 72 / hl.height), 72), Image.LANCZOS).convert("P", palette=Image.ADAPTIVE, colors=64)
hl.save("assets/header_logo.png", optimize=True)
print("icons written", emb.size, hl.size)

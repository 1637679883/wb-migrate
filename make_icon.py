# -*- coding: utf-8 -*-
"""生成程序图标 app.ico（纯标准库，无第三方依赖）。

设计：铬银球体 + 全息边缘光，放在浅色圆角方块上。
Y2K 的感觉来自三样东西：高光偏硬的球面、粉蓝对冲的轮廓光、极窄的描边。
"""

import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.ico")
SIZES = (256, 128, 64, 48, 32, 16)
SS = 4  # 超采样倍数


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(lerp(c1[i], c2[i], t) for i in range(3))


def rounded_alpha(x, y, s, radius):
    """圆角方块的覆盖率，坐标已归一化到边长 s。"""
    cx = min(max(x, radius), s - radius)
    cy = min(max(y, radius), s - radius)
    d = math.hypot(x - cx, y - cy)
    if d <= radius - 0.5:
        return 1.0
    if d >= radius + 0.5:
        return 0.0
    return radius + 0.5 - d


def shade(x, y):
    """返回 (r,g,b,a)，x,y 归一化 0..1。"""
    # 背景：上浅下深的银蓝渐变
    bg = mix((252, 254, 255), (196, 208, 228), y ** 0.85)
    # 极轻的网格感：对角细线
    if (x * 8 + y * 8) % 1.0 < 0.02:
        bg = mix(bg, (255, 255, 255), 0.35)

    cx = cy = 0.5
    rad = 0.315
    dx = (x - cx) / rad
    dy = (y - cy) / rad
    d = math.hypot(dx, dy)

    if d <= 1.02:
        # 球体：光来自左上，右下压暗拉出体积
        nx, ny = dx, dy
        nz = math.sqrt(max(0.0, 1.0 - min(1.0, d * d)))
        lx, ly, lz = -0.45, -0.62, 0.65
        lam = max(0.0, nx * lx + ny * ly + nz * lz)
        base = mix((104, 118, 146), (255, 255, 255), lam ** 0.7)
        # 右下二次压暗，避免整颗球发灰
        if dy > 0:
            base = mix(base, (86, 100, 128), (dy * 0.55) ** 1.4 * 0.55)
        # 边缘：左上偏青、右下偏粉的全息对冲
        rim = max(0.0, d - 0.70) / 0.32
        if rim > 0:
            holo = mix((150, 218, 255), (255, 150, 205), 0.5 + 0.5 * (dx * 0.7 + dy * 0.7))
            base = mix(base, holo, min(1.0, rim) * 0.92)
        # 硬高光
        hx, hy = dx + 0.36, dy + 0.42
        hd = math.hypot(hx, hy)
        if hd < 0.27:
            base = mix(base, (255, 255, 255), (1 - hd / 0.27) ** 1.3 * 1.0)
        # 底部反光
        if dy > 0.5:
            base = mix(base, (255, 255, 255), (dy - 0.5) * 0.7)

        a = 1.0 if d <= 0.965 else max(0.0, (1.02 - d) / 0.055)
        # 球体外描边，小尺寸下靠它撑住轮廓
        if d > 0.955:
            base = mix(base, (78, 92, 118), min(1.0, (d - 0.955) / 0.05) * 0.62)
        out = (int(base[0]), int(base[1]), int(base[2]), a)
    else:
        out = (int(bg[0]), int(bg[1]), int(bg[2]), 1.0)

    # 背景圆角裁切
    m = rounded_alpha(x, y, 1.0, 0.22)
    if out[3] > 0:
        out = (out[0], out[1], out[2], out[3] * m)
    return out


def render(size):
    px = []
    inv = 1.0 / (size * SS)
    for oy in range(size):
        for ox in range(size):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SS):
                for sx in range(SS):
                    x = (ox * SS + sx + 0.5) * inv
                    y = (oy * SS + sy + 0.5) * inv
                    r, g, b, a = shade(x, y)
                    acc[0] += r * a
                    acc[1] += g * a
                    acc[2] += b * a
                    acc[3] += a
            n = SS * SS
            a = acc[3] / n
            if acc[3] > 0:
                px.append((int(acc[0] / acc[3]), int(acc[1] / acc[3]), int(acc[2] / acc[3]),
                           int(round(a * 255))))
            else:
                px.append((0, 0, 0, 0))
    return px


def png_bytes(px, size):
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            r, g, b, a = px[y * size + x]
            raw += bytes((r, g, b, a))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def main():
    blobs = []
    for s in SIZES:
        blobs.append((s, png_bytes(render(s), s)))
        print("  渲染 %dx%d" % (s, s))

    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for s, png in blobs:
        w = 0 if s >= 256 else s
        h = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", w, h, 0, 0, 1, 32, len(png), offset)
        offset += len(png)
        data += png
    with open(OUT, "wb") as f:
        f.write(header + entries + data)
    print("已生成 %s（%d 字节）" % (OUT, os.path.getsize(OUT)))


if __name__ == "__main__":
    main()

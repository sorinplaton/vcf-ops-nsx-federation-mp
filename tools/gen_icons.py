#!/usr/bin/env python3
"""Generate the icons of the pack: one per object type and one for the adapter kind (needs Pillow).

Each icon is a white pictogram on a rounded square in the colour of its family (managers and sites, connectivity,
stretched objects). The glyphs are drawn at 512 px and scaled down, so they stay readable at the 16 px of the
inventory tree. Writes PNG files into pack/conf/images."""
import math
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "pack", "conf", "images")


MANAGERS, LINKS, STRETCHED = (0, 99, 140), (87, 71, 140), (30, 122, 80)
S = 512
W = (255, 255, 255, 255)
LW = 34  # stroke width at 512 px


def base(colour):
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((16, 16, S - 16, S - 16), radius=88, fill=colour + (255,))
    return im, d


def circle(d, cx, cy, r, fill=None, width=LW):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill, outline=None if fill else W, width=width)


def arrow(d, x1, y1, x2, y2, head=60, width=LW):
    d.line((x1, y1, x2, y2), fill=W, width=width)
    a = math.atan2(y2 - y1, x2 - x1)
    p = [(x2 + head * 0.35 * math.cos(a), y2 + head * 0.35 * math.sin(a)),
         (x2 - head * math.cos(a - 0.6), y2 - head * math.sin(a - 0.6)),
         (x2 - head * math.cos(a + 0.6), y2 - head * math.sin(a + 0.6))]
    d.polygon(p, fill=W)


def font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    raise SystemExit("DejaVu Sans Bold is needed for the T0 and T1 icons")


def g_federation(d):  # three linked sites
    pts = [(256, 140), (150, 350), (362, 350)]
    for a in range(3):
        d.line(pts[a] + pts[(a + 1) % 3], fill=W, width=LW)
    for x, y in pts:
        circle(d, x, y, 58, fill=W)


def g_global_manager(d):  # globe
    circle(d, 256, 256, 150)
    d.ellipse((256 - 62, 106, 256 + 62, 406), outline=W, width=LW - 6)
    d.line((106, 256, 406, 256), fill=W, width=LW - 6)
    d.line((256, 106, 256, 406), fill=W, width=LW - 6)


def g_manager_node(d):  # one appliance
    d.rounded_rectangle((120, 150, 392, 362), radius=28, outline=W, width=LW)
    d.line((170, 225, 300, 225), fill=W, width=LW - 6)
    d.line((170, 290, 300, 290), fill=W, width=LW - 6)
    circle(d, 345, 225, 18, fill=W)
    circle(d, 345, 290, 18, fill=W)


def g_location(d):  # map pin
    cx, cy, r = 256, 215, 112
    d.pieslice((cx - r, cy - r, cx + r, cy + r), 150, 390, fill=W)
    d.polygon([(cx - r * math.cos(math.radians(30)), cy + r * math.sin(math.radians(30))),
               (cx + r * math.cos(math.radians(30)), cy + r * math.sin(math.radians(30))), (cx, 420)], fill=W)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=W)
    circle(d, cx, cy, 44, fill=None)
    d.ellipse((cx - 46, cy - 46, cx + 46, cy + 46), fill=(0, 0, 0, 0))


def g_sync_channel(d):  # two arcs with arrows
    d.arc((126, 126, 386, 386), 200, 340, fill=W, width=LW)
    d.arc((126, 126, 386, 386), 20, 160, fill=W, width=LW)
    arrow(d, 330, 145, 372, 205, head=70, width=2)
    arrow(d, 182, 367, 140, 307, head=70, width=2)


def g_edge_node(d):  # NSX style edge: circle with two opposite arrows
    circle(d, 256, 256, 160)
    arrow(d, 175, 215, 345, 215, head=62, width=LW - 6)
    arrow(d, 337, 297, 167, 297, head=62, width=LW - 6)


def g_edge_cluster(d):  # a cluster box holding three edges
    d.rounded_rectangle((95, 165, 417, 347), radius=40, outline=W, width=LW)
    for cx in (176, 256, 336):
        circle(d, cx, 256, 40, fill=W)


def g_rtep_tunnel(d):  # tunnel between two endpoints
    d.rounded_rectangle((110, 190, 402, 322), radius=66, outline=W, width=LW)
    arrow(d, 175, 256, 345, 256, head=58, width=LW - 8)


def g_bgp_neighbor(d):  # two peers
    d.line((165, 256, 347, 256), fill=W, width=LW)
    circle(d, 150, 256, 74, fill=W)
    circle(d, 362, 256, 74, fill=W)


def text_glyph(code):
    def g(d):
        f = font(200 if len(code) <= 2 else 150)
        b = d.textbbox((0, 0), code, font=f)
        d.text(((S - b[2] + b[0]) / 2 - b[0], (S - b[3] + b[1]) / 2 - b[1]), code, font=f, fill=W)
    return g


def g_gateway(digit):  # gateway: circle with four outward arrows and the tier digit
    def g(d):
        for a in range(4):
            ang = math.radians(45 + 90 * a)
            arrow(d, 256 + 95 * math.cos(ang), 256 + 95 * math.sin(ang),
                  256 + 175 * math.cos(ang), 256 + 175 * math.sin(ang), head=60, width=LW - 8)
        circle(d, 256, 256, 92, fill=W)
        f = font(130)
        b = d.textbbox((0, 0), digit, font=f)
        d.text((256 - (b[2] - b[0]) / 2 - b[0], 256 - (b[3] - b[1]) / 2 - b[1]), digit, font=f, fill=COL)
    return g


def g_segment(d):  # a wire with three ports
    d.line((100, 300, 412, 300), fill=W, width=LW)
    for x in (160, 256, 352):
        d.line((x, 300, x, 190), fill=W, width=LW - 6)
        circle(d, x, 175, 34, fill=W)


def g_global_policy(d):  # shield
    d.polygon([(256, 105), (395, 160), (380, 290), (256, 412), (132, 290), (117, 160)], fill=W)
    d.line((195, 260, 245, 310), fill=COL, width=LW)
    d.line((240, 312, 330, 205), fill=COL, width=LW)


def g_rule(d):  # one line of a rule list with a tick
    for i, y in enumerate((170, 256, 342)):
        d.line((215, y, 400, y), fill=W, width=LW)
        circle(d, 150, y, 30, fill=W)


def g_realization(d):  # check in a circle
    circle(d, 256, 256, 155)
    d.line((185, 262, 240, 318), fill=W, width=LW + 6)
    d.line((232, 322, 335, 195), fill=W, width=LW + 6)


ICONS = {
    "federation": (g_federation, MANAGERS), "global_manager": (g_global_manager, MANAGERS),
    "manager_node": (g_manager_node, MANAGERS), "location": (g_location, MANAGERS),
    "sync_channel": (g_sync_channel, LINKS), "edge_cluster": (g_edge_cluster, LINKS), "edge_node": (g_edge_node, LINKS),
    "rtep_tunnel": (g_rtep_tunnel, LINKS), "bgp_neighbor": (g_bgp_neighbor, LINKS), "tier0_sr": (text_glyph("SR"), LINKS),
    "tier0": (text_glyph("T0"), STRETCHED), "tier1": (text_glyph("T1"), STRETCHED), "segment": (g_segment, STRETCHED),
    "global_policy": (g_global_policy, STRETCHED), "realization": (g_realization, STRETCHED), "rule": (g_rule, STRETCHED),
}
COL = None


def render(kind):
    global COL
    g, colour = ICONS[kind]
    COL = colour + (255,)
    im, d = base(colour)
    g(d)
    if kind == "location":  # punch the hole of the pin
        hole = Image.new("L", (S, S), 0)
        ImageDraw.Draw(hole).ellipse((256 - 46, 215 - 46, 256 + 46, 215 + 46), fill=255)
        px = im.load()
        hp = hole.load()
        for y in range(215 - 50, 215 + 50):
            for x in range(256 - 50, 256 + 50):
                if hp[x, y]:
                    px[x, y] = colour + (255,)
    return im


def save(kind, path, size=128):
    render(kind).resize((size, size), Image.LANCZOS).save(path)


if __name__ == "__main__":
    for kind in ICONS:
        save(kind, os.path.join(OUT, "ResourceKind", kind + ".png"))
    save("federation", os.path.join(OUT, "AdapterKind", "NSXFederation.png"))
    save("federation", os.path.join(OUT, "ResourceKind", "NSXFederation_adapter_instance.png"))
    print("wrote", len(ICONS) + 2, "icons to", OUT)

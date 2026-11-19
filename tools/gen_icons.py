#!/usr/bin/env python3
"""Generate the icons of the pack: one per object type and one for the adapter kind (needs Pillow).

Each icon is a rounded square in the colour of its family (managers, connectivity, stretched objects) with a
short code. Writes PNG files into pack/conf/images."""
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "pack", "conf", "images")
MANAGERS, LINKS, STRETCHED = (0, 99, 140), (87, 71, 140), (30, 122, 80)
ICONS = {  # object type -> (code, colour)
    "federation": ("FED", MANAGERS), "global_manager": ("GM", MANAGERS), "manager_node": ("N", MANAGERS),
    "location": ("LOC", MANAGERS), "sync_channel": ("SYN", LINKS), "edge_cluster": ("EC", LINKS),
    "edge_node": ("EN", LINKS), "rtep_tunnel": ("RT", LINKS), "bgp_neighbor": ("BGP", LINKS),
    "tier0": ("T0", STRETCHED), "tier1": ("T1", STRETCHED), "segment": ("SEG", STRETCHED),
    "global_policy": ("POL", STRETCHED), "realization": ("@", STRETCHED), "rule": ("R", STRETCHED),
}


def font(size):
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def icon(code, colour, path, size=128):
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((6, 6, size - 6, size - 6), radius=22, fill=colour + (255,))
    f = font(54 if len(code) <= 2 else 38)
    box = d.textbbox((0, 0), code, font=f)
    d.text(((size - box[2] + box[0]) / 2 - box[0], (size - box[3] + box[1]) / 2 - box[1]), code, font=f, fill="white")
    im.save(path)


if __name__ == "__main__":
    for kind, (code, colour) in ICONS.items():
        icon(code, colour, os.path.join(OUT, "ResourceKind", kind + ".png"))
    icon("FED", MANAGERS, os.path.join(OUT, "AdapterKind", "NSXFederation.png"))
    icon("FED", MANAGERS, os.path.join(OUT, "ResourceKind", "NSXFederation_adapter_instance.png"))
    print("wrote", len(ICONS) + 2, "icons to", OUT)

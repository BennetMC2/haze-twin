"""Draw the link-preview image (1200x630) for WhatsApp, Telegram, X etc. from the latest forecast.
Usage: python tools/og_image.py site/data/forecast.json site/data/inputs.json site/data/og.png [fontdir]"""
import json, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

fc = json.loads(Path(sys.argv[1]).read_text())
inp = json.loads(Path(sys.argv[2]).read_text())
out = sys.argv[3]
fontdir = Path(sys.argv[4]) if len(sys.argv) > 4 else None
BANDS = [(50, "Good", "#4FB286"), (100, "Moderate", "#6E9FD8"), (200, "Unhealthy", "#E8C547"), (300, "Very unhealthy", "#E9813A"), (10**9, "Hazardous", "#D24B45")]
band = lambda v: next(b for b in BANDS if round(v) <= b[0])


def font(name, size):
    for p in ([fontdir / name] if fontdir else []) + [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if "Bold" in name or "Semi" in name else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]:
        try:
            return ImageFont.truetype(str(p), size)
        except OSError:
            continue
    return ImageFont.load_default()


r = fc["regions"]["national"]
now = round(r["now"])
b = band(now)
W, H = 1200, 630
im = Image.new("RGB", (W, H), "#0E1820")
glow = Image.new("RGB", (W, H), b[2])
mask = Image.new("L", (W, H), 0)
md = ImageDraw.Draw(mask)
for i in range(60, 0, -1):
    md.ellipse([180 - i * 9, 330 - i * 6, 180 + i * 9, 330 + i * 6], fill=int(70 * (1 - i / 60)))
im = Image.composite(glow, im, mask)
d = ImageDraw.Draw(im)
d.text((60, 52), "Haze Twin", font=font("Barlow-Bold.ttf", 46), fill="#E8EEF1")
d.text((60, 108), "Singapore haze forecast", font=font("Barlow-Medium.ttf", 30), fill="#A9B9C3")
d.text((60, 190), str(now), font=font("BarlowSemiCondensed-Bold.ttf", 190), fill="#E8EEF1")
f = font("Barlow-SemiBold.ttf", 34)
tw = d.textlength(b[1], font=f)
x0 = 60 + d.textlength(str(now), font=font("BarlowSemiCondensed-Bold.ttf", 190)) + 30
d.rounded_rectangle([x0, 250, x0 + tw + 32, 304], radius=8, fill=b[2])
d.text((x0 + 16, 256), b[1], font=f, fill="#0B1218")
d.text((x0, 316), "24-hour PSI now", font=font("Barlow-Medium.ttf", 28), fill="#A9B9C3")
# when better
cb = r.get("clearBy") or []
days = fc["days"]
i50 = next((i for i, v in enumerate(cb) if v >= 50), None)
SGT = timezone(timedelta(hours=8))
if r.get("mode") != "haze":
    line = "Air is " + b[1].lower() + " today"
elif i50 is not None:
    dt = datetime.fromisoformat(days[i50]["iso"].replace("Z", "+00:00")).astimezone(SGT)
    line = "Likely better around " + dt.strftime("%a %-d %b")
else:
    line = "Not likely better in the next 2 weeks" + (f" (season ends ~{fc['season']['median']})" if fc.get("season") else "")
size = 48
while size > 26 and d.textlength(line, font=font("Barlow-SemiBold.ttf", size)) > W - 120:
    size -= 2
d.text((60, 430), line, font=font("Barlow-SemiBold.ttf", size), fill="#E8EEF1")
# 7-day strip
x = 60
for k in range(min(7, len(r["days"]))):
    v = r["days"][k]["p50"]
    bb = band(v)
    d.rounded_rectangle([x, 520, x + 140, 590], radius=8, outline="#33495A", width=2)
    d.rectangle([x + 2, 582, x + 138, 588], fill=bb[2])
    d.text((x + 12, 526), days[k]["dow"], font=font("Barlow-Medium.ttf", 22), fill="#A9B9C3")
    d.text((x + 12, 548), str(v), font=font("BarlowSemiCondensed-Bold.ttf", 30), fill="#E8EEF1")
    x += 156
im.save(out, optimize=True)
print("wrote", out)

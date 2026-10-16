"""Instagram-plaatjes per rotatie volgens de Wissellijst-stijlgids (retro pop-art poster).

Per rotatie twee assets van 1080x1080: "5 IN" en "5 OUT", met de vijf nummers altijd in de
volgorde 80s, 90s, 00s, 10s, 20s en één uitgelicht nummer. Alle tekst (logo, titels,
artiesten, badges, rotatienummer, datum) rendert deze module zelf; alleen de achtergrond-
illustratie met de muziekdrager mag van een beeldmodel komen. Lukt dat niet of staat het
uit, dan tekenen we een eigen achtergrond met de albumhoes als hoofdbeeld.
"""
import base64
import datetime
import io
import math
import os
import random
import re
import urllib.request
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from config import (OPENAI_API_KEY, OPENAI_IMAGE_MODEL, OPENAI_IMAGE_QUALITY,
                    INSTAGRAM_AI_ILLUSTRATIE)
from logging_config import get_logger

logger = get_logger(__name__)

FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
FORMAAT = 1080

DECADE_ORDER = ["80s", "90s", "00s", "10s", "20s"]

BRAND = {
    "yellow": "#F6C945", "turquoise": "#48B9C7", "orange": "#F28A32", "cream": "#F4E8C8",
    "ink": "#17202A", "redShadow": "#D94835", "wood": "#B97A35",
}
DECADE_KLEUR = {"80s": "#E0457B", "90s": "#3FA34D", "00s": "#3B6FD8",
                "10s": "#F28A32", "20s": "#8A4FD1"}
DECADE_KLEUR_NAAM = {"80s": "pink/magenta", "90s": "green", "00s": "blue",
                     "10s": "orange", "20s": "purple"}

# Muziekdrager per decennium: (variant A, variant B), gekozen met rotatienummer % 2.
DEVICES = {
    "80s": ("walkman_cassette", "foam_headphones"),
    "90s": ("discman_cd", "boombox"),
    "00s": ("ipod_earbuds", "featurephone_headset"),
    "10s": ("smartphone_streaming", "overear_headphones"),
    "20s": ("wireless_earbuds", "bluetooth_speaker"),
}
DEVICE_PROMPT = {
    "walkman_cassette": "a portable cassette walkman with a cassette tape",
    "foam_headphones": "light on-ear headphones with orange foam cushions",
    "discman_cd": "a portable discman CD player with a shiny CD",
    "boombox": "a portable boombox radio with cassette and CD",
    "ipod_earbuds": "a classic white MP3 player with white wired earbuds",
    "featurephone_headset": "a 2000s mobile phone with a wired headset",
    "smartphone_streaming": "a smartphone showing a music streaming player",
    "overear_headphones": "modern over-ear headphones",
    "wireless_earbuds": "a smartphone with wireless earbuds and charging case",
    "bluetooth_speaker": "a compact wireless bluetooth speaker",
}
DEVICE_NL = {
    "walkman_cassette": "walkman met cassette", "foam_headphones": "koptelefoon met schuimkussens",
    "discman_cd": "discman met cd", "boombox": "boombox", "ipod_earbuds": "mp3-speler met oortjes",
    "featurephone_headset": "mobiele telefoon met headset",
    "smartphone_streaming": "smartphone met streaming-app", "overear_headphones": "over-ear koptelefoon",
    "wireless_earbuds": "smartphone met draadloze oordopjes", "bluetooth_speaker": "bluetooth-speaker",
}
CAPTION_EMOJI = {"80s": "📼", "90s": "💿", "00s": "🎵", "10s": "🎧", "20s": "🎶"}
MAANDEN = ["JAN", "FEB", "MRT", "APR", "MEI", "JUN", "JUL", "AUG", "SEP", "OKT", "NOV", "DEC"]


_ACHTERVOEGSEL = re.compile(
    r"\s[-–]\s.*\b(remaster(ed)?|version|versie|edit|mix|live|mono|stereo|single|radio)\b.*$"
    r"|\s[\(\[][^\)\]]*\b(remaster(ed)?|remastered version)\b[^\)\]]*[\)\]]\s*$", re.I)


def schone_titel(titel):
    """'Take On Me - 2015 Remaster' -> 'Take On Me'. Andere toevoegingen blijven staan."""
    return _ACHTERVOEGSEL.sub("", titel or "").strip() or (titel or "")


def label(decade):
    return f"{decade[:2]}’s"


def decade_van(categorie):
    """'80s heeft in de Top 2000 gestaan' -> '80s'; geen decennium -> None."""
    match = re.match(r"\s*(\d{2})s\b", categorie or "")
    return f"{match.group(1)}s" if match else None


def datum_label(datum):
    return f"{datum.day} {MAANDEN[datum.month - 1]} {datum.year}"


# --- Data: groeperen, valideren, regels ---

def groepeer(tracks):
    """Verdeel de tracks van een rotatie in groepen van één per decennium (meerdere blokken
    per rotatie geven meerdere groepen). Tracks zonder bruikbaar decennium vallen af."""
    groepen = []
    for t in tracks:
        if t.get("decade") not in DECADE_ORDER:
            continue
        for groep in groepen:
            if t["decade"] not in {g["decade"] for g in groep}:
                groep.append(t)
                break
        else:
            groepen.append([t])
    return groepen


def valideer(asset):
    """Regels uit de stijlgids (hoofdstuk 13). Returns een lijst met fouten (leeg = goed)."""
    fouten = []
    tracks = asset.get("tracks", [])
    if len(tracks) != 5:
        fouten.append(f"{len(tracks)} tracks in plaats van 5")
    for d in DECADE_ORDER:
        n = sum(1 for t in tracks if t.get("decade") == d)
        if n != 1:
            fouten.append(f"{n} tracks uit de {d}")
    if sum(1 for t in tracks if t.get("highlighted")) != 1:
        fouten.append("niet precies één uitgelicht nummer")
    if asset.get("direction") not in ("IN", "OUT"):
        fouten.append("richting moet IN of OUT zijn")
    if not asset.get("rotation_number"):
        fouten.append("rotatienummer ontbreekt")
    if not isinstance(asset.get("changed_at"), datetime.date):
        fouten.append("ongeldige datum")
    if any(not (t.get("title") or "").strip() or not (t.get("artist") or "").strip() for t in tracks):
        fouten.append("lege titel of artiest")
    return fouten


def maak_asset(tracks, rotatienummer, datum, richting, playlist_naam="Wissellijst"):
    """Asset-data met afgeleide rendering-data (highlight, device, palet)."""
    tracks = sorted((dict(t) for t in tracks), key=lambda t: DECADE_ORDER.index(t["decade"]))
    # Deterministisch en afwisselend: IN en OUT lichten een ander decennium uit.
    verschuiving = 0 if richting == "IN" else 2
    highlight = DECADE_ORDER[(rotatienummer + verschuiving) % len(DECADE_ORDER)]
    for t in tracks:
        t["highlighted"] = t["decade"] == highlight
    device = DEVICES[highlight][rotatienummer % 2]
    return {
        "rotation_number": rotatienummer, "changed_at": datum, "direction": richting,
        "playlist_name": playlist_naam, "subtitle": "Door de tijd heen", "tracks": tracks,
        "highlight_decade": highlight, "device": device,
        "palette_variant": f"{highlight}_{DECADE_KLEUR_NAAM[highlight].split('/')[0]}",
        "render_variant": rotatienummer % 2, "output_type": "instagram_square",
    }


def uitgelicht(asset):
    return next(t for t in asset["tracks"] if t["highlighted"])


def bestandsnaam(asset, deel=1):
    extra = f"-b{deel}" if deel > 1 else ""
    return (f"wissellijst-r{asset['rotation_number']:03d}-{asset['changed_at'].isoformat()}"
            f"-{asset['direction'].lower()}{extra}-square.png")


def caption(asset):
    h = uitgelicht(asset)
    richting = "IN" if asset["direction"] == "IN" else "OUT"
    regels = [f"🔄 Wissellijst — rotatie #{asset['rotation_number']}", "", f"5 {richting}.", ""]
    regels += [f"{CAPTION_EMOJI[t['decade']]} {label(t['decade'])} — {t['title']} · {t['artist']}"
               for t in asset["tracks"]]
    regels += ["", "▶ Nu op de Wissellijst:", f"{h['title']} — {h['artist']}", "",
               "#Wissellijst #DoorDeTijdHeen"]
    return "\n".join(regels)


def alt_tekst(asset):
    h = uitgelicht(asset)
    return (f"Wissellijst rotatie {asset['rotation_number']}, {asset['direction']}. "
            "Vijf nummers, één uit ieder decennium van de jaren 80 tot en met de jaren 20. "
            f"Uitgelicht is {h['title']} van {h['artist']}. "
            f"De illustratie toont een {DEVICE_NL[asset['device']]} in retro pop-artstijl.")


# --- Achtergrond ---

def ai_prompt(asset):
    """Prompt uit hoofdstuk 16 van de stijlgids; de lijst rond het beeld tekenen we zelf."""
    h = uitgelicht(asset)
    sfeer = "fresh, upbeat, moving inward" if asset["direction"] == "IN" else "warm, nostalgic, moving outward"
    return (
        'Create a square retro comic pop-art music poster background for "Wissellijst — Door de tijd heen".\n\n'
        f"Highlighted decade: {asset['highlight_decade']}\n"
        f"Highlighted track mood: {sfeer}, inspired by the song \"{h['title']}\" by {h['artist']}\n"
        f"Main audio device: {DEVICE_PROMPT[asset['device']]}\n\n"
        "Visual language:\n"
        "vintage illustrated music poster, bold dark outlines, warm screen-print texture, subtle "
        "halftone dots, paper grain, radial sunburst, small music notes, sparks and arrows, "
        "yellow/turquoise/orange base palette, decade accent color "
        f"{DECADE_KLEUR_NAAM[asset['highlight_decade']]}.\n\n"
        "The main audio device is the hero object, placed on the right half, slightly above center.\n"
        "Leave clean, calm negative space for a five-track list on the left half, "
        "branding at the top, rotation metadata at top right, "
        "and highlighted-track information below the hero object.\n\n"
        "Do not render any text, letters, numbers, song titles, artist names, dates, decade labels "
        "or logos. No picture frame or border. No photorealism. No glossy 3D. No random typography."
    )


def ai_achtergrond(asset):
    """Achtergrond van het beeldmodel, of None (uitgezet, geen sleutel of mislukt)."""
    if not INSTAGRAM_AI_ILLUSTRATIE or not OPENAI_API_KEY:
        return None
    try:
        from openai import OpenAI
        resultaat = OpenAI(api_key=OPENAI_API_KEY).images.generate(
            model=OPENAI_IMAGE_MODEL, prompt=ai_prompt(asset), size="1024x1024",
            quality=OPENAI_IMAGE_QUALITY, n=1)
        data = base64.b64decode(resultaat.data[0].b64_json)
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as e:
        logger.warning("AI-illustratie mislukt, getekende achtergrond gebruikt",
                       extra={"error": str(e), "model": OPENAI_IMAGE_MODEL})
        return None


def _ster(draw, cx, cy, r, kleur, punten=4):
    hoeken = []
    for i in range(punten * 2):
        straal = r if i % 2 == 0 else r * 0.38
        hoek = math.pi * i / punten - math.pi / 2
        hoeken.append((cx + straal * math.cos(hoek), cy + straal * math.sin(hoek)))
    draw.polygon(hoeken, fill=kleur, outline=BRAND["ink"], width=3)


def getekende_achtergrond(asset):
    """Eigen achtergrond: turquoise sunburst met decade-accent, halftone en papierkorrel."""
    rnd = random.Random(asset["rotation_number"] * 7 + (asset["direction"] == "OUT"))
    accent = DECADE_KLEUR[asset["highlight_decade"]]
    warm = asset["direction"] == "OUT"
    img = Image.new("RGB", (FORMAAT, FORMAAT), BRAND["turquoise"])
    draw = ImageDraw.Draw(img)

    cx, cy = 800, 470
    stralen = 28
    for i in range(stralen):
        a1, a2 = 2 * math.pi * i / stralen, 2 * math.pi * (i + 0.5) / stralen
        kleur = ("#5CC6D2" if i % 2 else "#3FAAB8") if i % 4 else (BRAND["orange"] if warm else BRAND["yellow"])
        draw.polygon([(cx, cy), (cx + 1600 * math.cos(a1), cy + 1600 * math.sin(a1)),
                      (cx + 1600 * math.cos(a2), cy + 1600 * math.sin(a2))], fill=kleur)
    # Zachte decade-gloed achter het hoofdbeeld
    gloed = Image.new("L", img.size, 0)
    ImageDraw.Draw(gloed).ellipse((cx - 330, cy - 330, cx + 330, cy + 330), fill=170)
    img = Image.composite(Image.new("RGB", img.size, accent), img, gloed.filter(ImageFilter.GaussianBlur(60)))

    # Halftone-raster
    raster = Image.new("L", img.size, 0)
    rd = ImageDraw.Draw(raster)
    for y in range(0, FORMAAT, 14):
        for x in range(0, FORMAAT, 14):
            r = 1.5 + 2.5 * (x / FORMAAT)
            rd.ellipse((x - r, y - r, x + r, y + r), fill=60)
    img = Image.composite(Image.new("RGB", img.size, BRAND["ink"]), img, raster)

    draw = ImageDraw.Draw(img)
    for _ in range(9):
        _ster(draw, rnd.randint(600, 1020), rnd.randint(220, 880), rnd.randint(10, 22),
              rnd.choice([BRAND["yellow"], BRAND["cream"], BRAND["orange"]]))
    # Papierkorrel
    korrel = Image.effect_noise(img.size, 28).convert("RGB")
    return Image.blend(img, korrel, 0.06)


# --- Tekst en vormen ---

def _font(naam, grootte):
    return ImageFont.truetype(os.path.join(FONT_DIR, naam), grootte)


def _logo_font(g):
    return _font("LuckiestGuy-Regular.ttf", g)


def _cond_font(g):
    return _font("BarlowCondensed-ExtraBold.ttf", g)


def _bold_font(g):
    return _font("Barlow-Bold.ttf", g)


def _medium_font(g):
    return _font("Barlow-Medium.ttf", g)


def _marker_font(g):
    return _font("PermanentMarker-Regular.ttf", g)


def _past(draw, tekst, font, breedte):
    """Kort de tekst in met een ellips tot hij in de breedte past."""
    if draw.textlength(tekst, font=font) <= breedte:
        return tekst
    while tekst and draw.textlength(tekst + "…", font=font) > breedte:
        tekst = tekst[:-1]
    return tekst.rstrip() + "…"


def _regels(draw, tekst, font, breedte, max_regels=2):
    """Woordafbreking tot max_regels; de laatste regel wordt zo nodig ingekort."""
    woorden, regels, huidig = tekst.split(), [], ""
    for woord in woorden:
        probeer = f"{huidig} {woord}".strip()
        if draw.textlength(probeer, font=font) <= breedte or not huidig:
            huidig = probeer
        else:
            regels.append(huidig)
            huidig = woord
    if huidig:
        regels.append(huidig)
    if len(regels) > max_regels:
        regels = regels[:max_regels - 1] + [" ".join(regels[max_regels - 1:])]
    return [_past(draw, r, font, breedte) for r in regels]


def _kaart(draw, box, fill, rand=4, radius=18):
    x0, y0, x1, y1 = box
    draw.rounded_rectangle((x0 + 6, y0 + 7, x1 + 6, y1 + 7), radius, fill=BRAND["ink"])
    draw.rounded_rectangle(box, radius, fill=fill, outline=BRAND["ink"], width=rand)


def _schaduwtekst(draw, xy, tekst, font, fill, schaduw, stroke=5, offset=6, anchor="la"):
    x, y = xy
    draw.text((x + offset, y + offset), tekst, font=font, fill=schaduw, anchor=anchor,
              stroke_width=stroke, stroke_fill=BRAND["ink"])
    draw.text((x, y), tekst, font=font, fill=fill, anchor=anchor,
              stroke_width=stroke, stroke_fill=BRAND["ink"])


def _hoes(url, grootte):
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return ImageOps.fit(Image.open(io.BytesIO(r.read())).convert("RGB"), (grootte, grootte))
    except Exception as e:
        logger.warning("Albumhoes downloaden mislukt", extra={"error": str(e)})
        return None


def _hoes_placeholder(grootte, kleur):
    """Geen albumhoes: een muzieknoot op de decade-kleur."""
    img = Image.new("RGB", (grootte, grootte), kleur)
    d = ImageDraw.Draw(img)
    g = grootte
    w = max(2, int(g * 0.04))
    d.ellipse((g * 0.22, g * 0.60, g * 0.46, g * 0.80), fill=BRAND["ink"])
    d.ellipse((g * 0.54, g * 0.52, g * 0.78, g * 0.72), fill=BRAND["ink"])
    d.line((g * 0.44, g * 0.70, g * 0.44, g * 0.24), fill=BRAND["ink"], width=w)
    d.line((g * 0.76, g * 0.62, g * 0.76, g * 0.16), fill=BRAND["ink"], width=w)
    d.polygon([(g * 0.44 - w / 2, g * 0.24), (g * 0.76 + w / 2, g * 0.16),
               (g * 0.76 + w / 2, g * 0.28), (g * 0.44 - w / 2, g * 0.36)], fill=BRAND["ink"])
    return img


def _plak_rond(img, hoes, box, radius=10):
    masker = Image.new("L", hoes.size, 0)
    ImageDraw.Draw(masker).rounded_rectangle((0, 0, hoes.size[0] - 1, hoes.size[1] - 1), radius, fill=255)
    img.paste(hoes, box[:2], masker)


# --- Render ---

def render(asset, achtergrond=None, hoezen=None):
    """Render één asset naar PNG-bytes (1080x1080)."""
    hoezen = hoezen or {}
    ai = achtergrond is not None
    img = (ImageOps.fit(achtergrond, (FORMAAT, FORMAAT)) if ai else getekende_achtergrond(asset)).convert("RGB")
    draw = ImageDraw.Draw(img)
    ink, cream, yellow = BRAND["ink"], BRAND["cream"], BRAND["yellow"]
    h = uitgelicht(asset)
    is_in = asset["direction"] == "IN"

    # Hoofdbeeld zonder AI: de albumhoes van het uitgelichte nummer in een medaillon
    if not ai:
        cx, cy, r = 800, 470, 190
        draw.ellipse((cx - r - 16, cy - r - 16, cx + r + 16, cy + r + 16), fill=DECADE_KLEUR[h["decade"]],
                     outline=ink, width=8)
        hoes = _hoes(h.get("artwork_url"), 2 * r) or _hoes_placeholder(2 * r, DECADE_KLEUR[h["decade"]])
        masker = Image.new("L", hoes.size, 0)
        ImageDraw.Draw(masker).ellipse((0, 0, 2 * r - 1, 2 * r - 1), fill=255)
        img.paste(hoes, (cx - r, cy - r), masker)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ink, width=6)

    # Logo, subtitel en metadata
    _schaduwtekst(draw, (66, 58), "WISSELLIJST", _logo_font(92), yellow, BRAND["redShadow"])
    sub = "DOOR DE TIJD HEEN"
    sf = _cond_font(34)
    sb = draw.textlength(sub, font=sf)
    _kaart(draw, (70, 160, 70 + sb + 40, 206), cream, rand=3, radius=10)
    draw.text((90, 183), sub, font=sf, fill=ink, anchor="lm")

    meta_f, datum_f = _cond_font(40), _cond_font(30)
    rot = f"ROTATIE #{asset['rotation_number']}"
    dat = datum_label(asset["changed_at"])
    mb = max(draw.textlength(rot, font=meta_f), draw.textlength(dat, font=datum_f)) + 44
    _kaart(draw, (1014 - mb, 60, 1014, 160), ink, rand=3, radius=12)
    draw.text((1014 - 22, 86), rot, font=meta_f, fill=yellow, anchor="rm")
    draw.text((1014 - 22, 133), dat, font=datum_f, fill=cream, anchor="rm")

    # Vijf trackregels, altijd 80s -> 20s
    x0, x1, y, hoogte, tussen = 66, 560, 238, 116, 16
    badge_f, titel_f, artiest_f = _logo_font(34), _bold_font(31), _medium_font(25)
    for t in asset["tracks"]:
        is_h = t["highlighted"]
        _kaart(draw, (x0, y, x1, y + hoogte), yellow if is_h else cream, rand=6 if is_h else 4)
        kleur = DECADE_KLEUR[t["decade"]]
        draw.rounded_rectangle((x0 + 16, y + 30, x0 + 112, y + 86), 14, fill=kleur, outline=ink, width=4)
        draw.text((x0 + 64, y + 59), label(t["decade"]), font=badge_f, fill="#FFFFFF", anchor="mm",
                  stroke_width=2, stroke_fill=ink)
        hoes = _hoes(t.get("artwork_url"), 76) or _hoes_placeholder(76, kleur)
        _plak_rond(img, hoes, (x0 + 126, y + 20))
        draw.rounded_rectangle((x0 + 126, y + 20, x0 + 202, y + 96), 10, outline=ink, width=3)
        tx = x0 + 218
        rechts = x1 - (54 if is_h else 18)
        draw.text((tx, y + 40), _past(draw, t["title"], titel_f, rechts - tx), font=titel_f, fill=ink, anchor="lm")
        draw.text((tx, y + 78), _past(draw, t["artist"], artiest_f, rechts - tx), font=artiest_f,
                  fill="#3A3F45", anchor="lm")
        if is_h:  # play-teken
            draw.polygon([(x1 - 42, y + 40), (x1 - 42, y + 76), (x1 - 14, y + 58)], fill=BRAND["redShadow"],
                         outline=ink, width=3)
        y += hoogte + tussen

    # NU OP DE WISSELLIJST
    sticker = Image.new("RGBA", (420, 72), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sticker)
    sd.rounded_rectangle((4, 4, 414, 66), 14, fill=yellow, outline=ink, width=5)
    sd.text((209, 36), "NU OP DE WISSELLIJST", font=_marker_font(30), fill=ink, anchor="mm")
    sticker = sticker.rotate(4, expand=True, resample=Image.BICUBIC)
    img.paste(sticker, (588, 680), sticker)
    draw = ImageDraw.Draw(img)
    _kaart(draw, (600, 762, 1014, 900), cream)
    hf = _bold_font(32)
    regels = _regels(draw, h["title"], hf, 374, max_regels=2)
    ty = 794 if len(regels) == 1 else 784
    for regel in regels:
        draw.text((620, ty), regel, font=hf, fill=ink, anchor="lm")
        ty += 36
    draw.text((620, ty + 4), _past(draw, h["artist"], _medium_font(26), 374), font=_medium_font(26),
              fill="#3A3F45", anchor="lm")

    # 5 IN / 5 OUT
    banner_kleur = "#2E9E6A" if is_in else BRAND["redShadow"]
    _kaart(draw, (66, 918, 1014, 1012), banner_kleur, rand=5, radius=20)
    tekst = "5 IN" if is_in else "5 OUT"
    _schaduwtekst(draw, (540, 968), tekst, _logo_font(80), yellow, ink, stroke=4, offset=5, anchor="mm")
    for pijl_x in (330, 750):  # pijlen naar binnen (IN) of naar buiten (OUT)
        richting = 1 if (pijl_x < 540) == is_in else -1
        punt = pijl_x + 36 * richting
        draw.polygon([(pijl_x - 36 * richting, 950), (punt, 965), (pijl_x - 36 * richting, 980),
                      (pijl_x - 22 * richting, 965)], fill=cream, outline=ink, width=3)

    # Houten/gouden posterlijst
    draw.rectangle((0, 0, FORMAAT - 1, FORMAAT - 1), outline=BRAND["wood"], width=34)
    draw.rectangle((26, 26, FORMAAT - 27, FORMAAT - 27), outline="#E3B061", width=5)
    draw.rectangle((33, 33, FORMAAT - 34, FORMAAT - 34), outline=ink, width=4)
    draw.rectangle((0, 0, FORMAAT - 1, FORMAAT - 1), outline="#6E4518", width=6)

    uit = io.BytesIO()
    img.save(uit, "PNG", optimize=True)
    return uit.getvalue()


# --- Per rotatie: data ophalen, renderen, opslaan ---

def _rotatienummer(session, run):
    from db.models import RotatieRun
    return (session.query(RotatieRun)
            .filter(RotatieRun.wissellijst_id == run.wissellijst_id, RotatieRun.status == "voltooid",
                    RotatieRun.id <= run.id).count())


def _hoes_urls(uris):
    from post import _hoezen
    return _hoezen(uris)


def maak_voor_run(run_id):
    """Maak de IN- en OUT-plaatjes van een rotatie (vervangt eerdere). Returns aantal plaatjes."""
    from db.session import get_session
    from db.models import RotatieRun, RotatieWijziging, RotatieAfbeelding, Wissellijst
    from scheduler import SCHEDULER_TZ

    with get_session() as s:
        run = s.get(RotatieRun, run_id)
        if not run or run.status != "voltooid":
            raise ValueError("Alleen voltooide rotaties krijgen een plaatje")
        wl = s.get(Wissellijst, run.wissellijst_id)
        rotatienummer = _rotatienummer(s, run)
        moment = (run.completed_at or run.started_at).replace(tzinfo=datetime.timezone.utc)
        datum = moment.astimezone(ZoneInfo(SCHEDULER_TZ)).date()
        wijzigingen = [{"type": w.type, "title": schone_titel(w.titel), "artist": w.artiest,
                        "uri": w.uri or "",
                        "decade": decade_van(w.categorie)}
                       for w in s.query(RotatieWijziging).filter_by(run_id=run_id)
                       .order_by(RotatieWijziging.id).all()]
        naam = wl.naam if wl else "Wissellijst"

    hoezen = _hoes_urls([w["uri"] for w in wijzigingen])
    for w in wijzigingen:
        w["artwork_url"] = hoezen.get(w["uri"], "")

    plaatjes, redenen = [], []
    for richting, soort in (("IN", "toegevoegd"), ("OUT", "verwijderd")):
        tracks = [w for w in wijzigingen if w["type"] == soort]
        groepen = groepeer(tracks)
        if not groepen:
            redenen.append(f"{richting}: geen nummers met een decennium 80s-20s "
                           f"({len(tracks)} nummers; rotaties van vóór de plaatjes-update "
                           "hebben geen decennium per nummer)")
            logger.warning("Geen plaatje: geen tracks met een decennium 80s-20s",
                           extra={"run_id": run_id, "richting": richting})
        for deel, groep in enumerate(groepen, start=1):
            asset = maak_asset(groep, rotatienummer, datum, richting, naam)
            fouten = valideer(asset)
            if fouten:
                redenen.append(f"{richting}" + (f" blok {deel}" if len(groepen) > 1 else "")
                               + ": " + ", ".join(fouten))
                logger.warning("Plaatje overgeslagen: rotatie past niet in de stijlgids",
                               extra={"run_id": run_id, "richting": richting, "deel": deel,
                                      "fouten": fouten})
                continue
            png = render(asset, ai_achtergrond(asset))
            plaatjes.append(RotatieAfbeelding(
                run_id=run_id, richting=richting.lower(), deel=deel,
                bestandsnaam=bestandsnaam(asset, deel), png=png,
                caption=caption(asset), alt_tekst=alt_tekst(asset)))

    if not plaatjes:
        raise ValueError("Geen plaatje gemaakt. " + "; ".join(redenen))
    with get_session() as s:
        s.query(RotatieAfbeelding).filter_by(run_id=run_id).delete()
        for p in plaatjes:
            s.add(p)
    logger.info("Instagram-plaatjes gemaakt", extra={"run_id": run_id, "aantal": len(plaatjes)})
    return len(plaatjes)

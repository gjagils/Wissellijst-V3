"""Populariteitsverdeling binnen een blok: elk nummer in een eigen populariteitsvak.

Spotify geeft per track een popularity van 0-100 (wereldwijd). Ontbreekt dat veld
(Development Mode-apps van na februari 2026), dan schakelt de verdeling zichzelf uit
en wordt elk nummer geaccepteerd.
"""
import math

from logging_config import get_logger

logger = get_logger(__name__)


def parse_vakken(tekst):
    """'30-50,50-70' -> [(30, 50), (50, 70)]. Ongeldige delen worden overgeslagen."""
    vakken = []
    for deel in (tekst or "").split(","):
        try:
            lo, hi = (int(x) for x in deel.strip().split("-"))
        except ValueError:
            continue
        if 0 <= lo < hi <= 100:
            vakken.append((lo, hi))
    return sorted(vakken)


def omschrijving(vak):
    lo, hi = vak
    if lo >= 90:
        woord = "wereldhit"
    elif lo >= 80:
        woord = "grote hit"
    elif lo >= 70:
        woord = "bekende hit"
    elif lo >= 50:
        woord = "bekend bij liefhebbers"
    else:
        woord = "vrij onbekend"
    return f"{lo}-{hi} ({woord})"


class Verdeling:
    """Houdt per blok bij welke populariteitsvakken al gevuld zijn."""

    def __init__(self, vakken, aantal_tracks):
        self.vakken = list(vakken)
        self.capaciteit = max(1, math.ceil(aantal_tracks / len(self.vakken))) if self.vakken else 0
        self.gebruik = {v: 0 for v in self.vakken}
        self.uit = not self.vakken

    def vak(self, popularity):
        for lo, hi in self.vakken:
            if lo <= popularity < hi or (hi == 100 and popularity == 100):
                return (lo, hi)
        return None

    def check(self, popularity):
        """Returns (ok, reden). reden is leeg als ok."""
        if self.uit:
            return True, ""
        if popularity is None:
            logger.warning("Spotify geeft geen popularity terug; populariteitsverdeling staat uit")
            self.uit = True
            return True, ""
        vak = self.vak(popularity)
        if vak is None:
            return False, f"populariteit {popularity} valt buiten de vakken"
        if self.gebruik[vak] >= self.capaciteit:
            return False, f"populariteit {popularity}, vak {vak[0]}-{vak[1]} is al gevuld"
        return True, ""

    def neem(self, popularity):
        if self.uit or popularity is None:
            return
        vak = self.vak(popularity)
        if vak:
            self.gebruik[vak] += 1

    def open_vakken(self):
        if self.uit:
            return []
        return [v for v in self.vakken if self.gebruik[v] < self.capaciteit]

    def prompt_hint(self):
        """Tekst voor GPT; leeg als de verdeling uit staat of alles gevuld is."""
        open_ = self.open_vakken()
        if not open_:
            return ""
        return ("Spreid de bekendheid (Spotify-populariteit 0-100). Nog nodig, één nummer per vak: "
                + "; ".join(omschrijving(v) for v in open_) + ".\n"
                "Geef per categorie dus zowel grote hits als minder bekende nummers.\n")

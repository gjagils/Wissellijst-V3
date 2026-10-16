"""NL-radiolijsten (Q-top 500, Top 2000, ...) als bron voor populariteit en kandidaten.

Een lijst komt binnen via een Spotify-playlist of als geplakte tekst. De plek in de
lijst geeft een NL-score: plek 1 = 100, de laatste plek = RADIO_SCORE_MIN. Staat een
nummer in meer lijsten, dan telt de hoogste score.
"""
import random
import re
import unicodedata

from config import RADIO_SCORE_MIN, RADIO_KANDIDATEN_PER_CATEGORIE
from logging_config import get_logger

logger = get_logger(__name__)


def _session():
    from db.session import get_session
    return get_session()


# --- Normaliseren en parsen ---

def _norm(tekst):
    tekst = unicodedata.normalize("NFKD", tekst or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", tekst).strip()


def titel_sleutel(titel):
    """'Radar Love - 2001 Remaster (Live)' -> 'radar love'."""
    titel = re.sub(r"\s[-–]\s.*$", "", titel or "")
    titel = re.sub(r"[\(\[].*?[\)\]]", "", titel)
    return _norm(titel)


def artiest_sleutel(artiest):
    """Eerste artiest, zonder 'feat.'. 'Nick & Simon' blijft heel; 'A feat. B' -> 'a'."""
    artiest = re.split(r"\s(?:feat\.?|ft\.?|featuring|met)\s", artiest or "", flags=re.I)[0]
    return _norm(artiest)


def zelfde_artiest(a, b):
    return bool(a and b) and (a == b or a.startswith(b) or b.startswith(a))


def playlist_id_uit(tekst):
    """Spotify-URL, URI of kaal ID -> ID; None als het geen playlist lijkt."""
    tekst = (tekst or "").strip()
    match = re.search(r"playlist[/:]([A-Za-z0-9]{10,})", tekst)
    if match:
        return match.group(1)
    return tekst if re.fullmatch(r"[A-Za-z0-9]{10,}", tekst) else None


def parse_tekst(tekst):
    """Geplakte lijst -> [{positie, artiest, titel, jaar}].

    Per regel 'positie. artiest - titel', 'artiest - titel' of met tabs
    ('positie<tab>artiest<tab>titel'). Een jaartal tussen haakjes aan het eind wordt
    als releasejaar gelezen. Zonder positie telt de regelvolgorde.
    """
    entries = []
    for regel in (tekst or "").splitlines():
        regel = regel.strip()
        if not regel:
            continue
        positie = None
        match = re.match(r"^(\d{1,5})[\.\):\s\t]+(.*)$", regel)
        if match:
            positie, regel = int(match.group(1)), match.group(2).strip()
        jaar = None
        match = re.search(r"\s*[\(\[]((?:19|20)\d{2})[\)\]]\s*$", regel)
        if match:
            jaar, regel = int(match.group(1)), regel[:match.start()].strip()
        delen = [d.strip() for d in regel.split("\t") if d.strip()]
        if len(delen) < 2:
            delen = [d.strip() for d in re.split(r"\s[-–—]\s", regel, maxsplit=1)]
        if len(delen) < 2 or not delen[0] or not delen[1]:
            continue
        entries.append({"positie": positie or len(entries) + 1,
                        "artiest": delen[0], "titel": delen[1], "uri": "", "jaar": jaar})
    entries.sort(key=lambda e: e["positie"])
    return entries


def lees_playlist(sp, playlist_id):
    """Spotify-playlist -> (naam, [{positie, artiest, titel, uri, jaar}]) in playlist-volgorde."""
    naam = sp.playlist(playlist_id, fields="name")["name"]
    results = sp.playlist_items(
        playlist_id, limit=100,
        fields="items(track(uri,name,artists(name),album(release_date))),next")
    items = list(results["items"])
    while results.get("next"):
        results = sp.next(results)
        items.extend(results["items"])
    entries = []
    for item in items:
        track = item.get("track")
        if not track or not track.get("uri"):
            continue
        datum = (track.get("album") or {}).get("release_date") or ""
        entries.append({
            "positie": len(entries) + 1,
            "artiest": track["artists"][0]["name"] if track.get("artists") else "",
            "titel": track.get("name", ""),
            "uri": track["uri"],
            "jaar": int(datum[:4]) if datum[:4].isdigit() else None,
        })
    return naam, entries


# --- Opslag ---

def lijsten():
    from sqlalchemy import func
    from db.models import Radiolijst, RadiolijstEntry
    with _session() as s:
        aantallen = dict(s.query(RadiolijstEntry.radiolijst_id, func.count(RadiolijstEntry.id))
                         .group_by(RadiolijstEntry.radiolijst_id).all())
        return [rl.to_dict(aantal=aantallen.get(rl.id, 0))
                for rl in s.query(Radiolijst).order_by(Radiolijst.zender, Radiolijst.naam).all()]


def sla_op(naam, zender, decennium, entries, spotify_playlist_id="", lijst_id=None):
    """Maak een radiolijst aan of vervang de inhoud van een bestaande. Returns de dict."""
    from db.models import Radiolijst, RadiolijstEntry, utcnow
    with _session() as s:
        rl = s.get(Radiolijst, lijst_id) if lijst_id else None
        if rl:
            s.query(RadiolijstEntry).filter_by(radiolijst_id=rl.id).delete()
            rl.geimporteerd_op = utcnow()
        else:
            rl = Radiolijst(naam=naam, zender=zender, decennium=decennium,
                            spotify_playlist_id=spotify_playlist_id)
            s.add(rl)
            s.flush()
        for e in entries:
            s.add(RadiolijstEntry(radiolijst_id=rl.id, positie=e["positie"],
                                  artiest=e["artiest"][:255], titel=e["titel"][:255],
                                  uri=e.get("uri") or "", jaar=e.get("jaar")))
        s.flush()
        return rl.to_dict(aantal=len(entries))


def verwijder(lijst_id):
    from db.models import Radiolijst
    with _session() as s:
        rl = s.get(Radiolijst, lijst_id)
        if not rl:
            return False
        s.delete(rl)
        return True


def get_lijst(lijst_id):
    from db.models import Radiolijst
    with _session() as s:
        rl = s.get(Radiolijst, lijst_id)
        return rl.to_dict() if rl else None


# --- Index: score en kandidaten ---

def _decennium(jaar):
    return f"{str((jaar // 10) * 10)[2:]}s" if jaar else ""


class RadioIndex:
    """Alle radiolijsten in het geheugen, voor één blok-generatie."""

    def __init__(self, rijen):
        """rijen: [(lijst_decennium, lijst_lengte, positie, artiest, titel, uri, jaar)]."""
        self.per_uri = {}
        self.per_titel = {}
        self.per_decennium = {}
        for dec, lengte, positie, artiest, titel, uri, jaar in rijen:
            score = self._score(positie, lengte)
            entry = {"artiest": artiest, "titel": titel, "uri": uri, "score": score,
                     "a_key": artiest_sleutel(artiest)}
            if uri:
                self.per_uri[uri] = max(score, self.per_uri.get(uri, 0))
            self.per_titel.setdefault(titel_sleutel(titel), []).append(entry)
            for d in {dec, _decennium(jaar)} - {""}:
                self.per_decennium.setdefault(d, {})
                sleutel = (entry["a_key"], titel_sleutel(titel))
                bestaand = self.per_decennium[d].get(sleutel)
                if not bestaand or bestaand["score"] < score:
                    self.per_decennium[d][sleutel] = entry

    @staticmethod
    def _score(positie, lengte):
        if lengte <= 1:
            return 100
        return round(100 - (100 - RADIO_SCORE_MIN) * (positie - 1) / (lengte - 1))

    @classmethod
    def laad(cls):
        from sqlalchemy import func
        from db.models import Radiolijst, RadiolijstEntry
        with _session() as s:
            lengtes = dict(s.query(RadiolijstEntry.radiolijst_id, func.count(RadiolijstEntry.id))
                           .group_by(RadiolijstEntry.radiolijst_id).all())
            rijen = [(rl_dec or "", lengtes.get(rl_id, 0), e.positie, e.artiest, e.titel,
                      e.uri or "", e.jaar)
                     for e, rl_id, rl_dec in s.query(RadiolijstEntry, Radiolijst.id,
                                                     Radiolijst.decennium)
                     .join(Radiolijst, Radiolijst.id == RadiolijstEntry.radiolijst_id).all()]
        return cls(rijen)

    def leeg(self):
        return not self.per_titel

    def score(self, uri, artiest, titel):
        """NL-score 0-100, of None als het nummer in geen enkele radiolijst staat."""
        if uri and uri in self.per_uri:
            return self.per_uri[uri]
        a_key = artiest_sleutel(artiest)
        scores = [e["score"] for e in self.per_titel.get(titel_sleutel(titel), [])
                  if zelfde_artiest(e["a_key"], a_key)]
        return max(scores) if scores else None

    def kandidaten(self, decennium, uitsluiten_uris=(), uitsluiten_artiesten=(),
                   uitsluiten_tracks=(), aantal=RADIO_KANDIDATEN_PER_CATEGORIE):
        """Willekeurige greep uit de radiolijsten van een decennium, gespreid over de scores.

        uitsluiten_tracks: (artiest, titel)-paren die al gebruikt zijn (historie).
        """
        uit_a = {artiest_sleutel(a) for a in uitsluiten_artiesten}
        uit_t = {(artiest_sleutel(a), titel_sleutel(t)) for a, t in uitsluiten_tracks}
        pool = [(sleutel, e) for sleutel, e in self.per_decennium.get(decennium, {}).items()
                if e["uri"] not in uitsluiten_uris and e["a_key"] not in uit_a
                and sleutel not in uit_t]
        pool = [e for _, e in pool]
        if len(pool) <= aantal:
            return sorted(pool, key=lambda e: -e["score"])
        # Spreiden: sorteer op score en neem uit elk deel van de lijst evenveel.
        pool.sort(key=lambda e: -e["score"])
        stap = len(pool) / aantal
        return [random.choice(pool[int(i * stap):max(int((i + 1) * stap), int(i * stap) + 1)])
                for i in range(aantal)]

"""E-mail notificaties na rotatie."""
import smtplib
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM
from logging_config import get_logger

logger = get_logger(__name__)


def mail_configured():
    """Controleer of SMTP-instellingen geconfigureerd zijn."""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def _send(msg, to_address):
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)


def send_alert_mail(to_address, subject, plain_text):
    """Stuur een platte alarm-mail (bijv. bij een mislukte rotatie)."""
    if not mail_configured():
        logger.warning("SMTP niet geconfigureerd, alarm-mail overgeslagen")
        return False
    msg = MIMEText(plain_text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM or SMTP_USER
    msg["To"] = to_address
    _send(msg, to_address)
    logger.info("Alarm-mail verstuurd", extra={"naar": to_address, "onderwerp": subject})
    return True


def _jaar(track):
    return f" ({track['jaar']})" if track.get("jaar") else ""


def send_rotation_mail(to_address, wissellijst_naam, verwijderd, toegevoegd,
                       plaatjes=None, plaatjes_fout=None):
    """Stuur een e-mail met de rotatie-samenvatting.

    plaatjes: Instagram-plaatjes van deze rotatie ([{bestandsnaam, png, caption, alt,
    richting}]); die gaan als bijlage mee, met bijschrift en alt-tekst in de mail.
    plaatjes_fout: waarom er geen plaatje is (komt dan in de mail in plaats van de plaatjes).
    """
    from html import escape

    if not mail_configured():
        logger.warning("SMTP niet geconfigureerd",
                       extra={"host": SMTP_HOST or "leeg",
                              "user": SMTP_USER or "leeg"})
        return
    if not to_address:
        logger.warning("Geen ontvanger-adres opgegeven, mail overgeslagen")
        return
    plaatjes = plaatjes or []

    subject = f"Rotatie voltooid: {wissellijst_naam}"
    if plaatjes:
        subject += f" ({len(plaatjes)} Instagram-plaatjes)"

    def regel_html(t):
        return f"<li>{escape(t['artiest'])} &mdash; {escape(t['titel'])}{_jaar(t)}</li>"

    verwijderd_html = "".join(regel_html(t) for t in verwijderd)
    toegevoegd_html = "".join(regel_html(t) for t in toegevoegd)

    instagram_html, instagram_tekst = "", ""
    if plaatjes:
        blokken = []
        for p in plaatjes:
            titel = "5 IN" if p["richting"] == "in" else "5 OUT"
            blokken.append(
                f'<h4 style="color: #F6C945; margin-bottom: 4px;">{titel} &middot; {escape(p["bestandsnaam"])}</h4>'
                '<p style="color: #888; font-size: 12px; margin: 0 0 4px;">Bijschrift:</p>'
                f'<pre style="white-space: pre-wrap; background: #121212; padding: 12px; border-radius: 8px; '
                f'font-family: inherit;">{escape(p["caption"])}</pre>'
                '<p style="color: #888; font-size: 12px; margin: 8px 0 4px;">Alt-tekst:</p>'
                f'<p style="background: #121212; padding: 12px; border-radius: 8px; margin: 0;">{escape(p["alt"])}</p>')
            instagram_tekst += (f"\n--- {titel} ({p['bestandsnaam']}) ---\n{p['caption']}\n\n"
                                f"Alt-tekst: {p['alt']}\n")
        instagram_html = (
            '<h3 style="color: #F6C945;">Instagram</h3>'
            '<p>De plaatjes zitten als bijlage bij deze mail. Sla ze op, plak het bijschrift en '
            'kies in Instagram de muziek.</p>' + "".join(blokken))
        instagram_tekst = "\nInstagram (plaatjes als bijlage):\n" + instagram_tekst
    elif plaatjes_fout:
        instagram_html = (f'<p style="color: #888;">Geen Instagram-plaatje: '
                          f'{escape(plaatjes_fout)}</p>')
        instagram_tekst = f"\nGeen Instagram-plaatje: {plaatjes_fout}\n"

    html = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #121212; color: #e0e0e0; padding: 24px;">
  <div style="max-width: 600px; margin: 0 auto; background: #1e1e1e; border-radius: 12px; padding: 24px;">
    <h2 style="color: #1db954; margin-top: 0;">Rotatie: {escape(wissellijst_naam)}</h2>

    <h3 style="color: #ff5252;">Verwijderd ({len(verwijderd)} tracks)</h3>
    <ul style="padding-left: 20px;">{verwijderd_html}</ul>

    <h3 style="color: #1db954;">Toegevoegd ({len(toegevoegd)} tracks)</h3>
    <ul style="padding-left: 20px;">{toegevoegd_html}</ul>

    {instagram_html}

    <p style="color: #888; font-size: 12px; margin-top: 24px;">
      Dit is een automatisch bericht van Wissellijst.
    </p>
  </div>
</body>
</html>"""

    verwijderd_tekst = "\n".join(
        f"  - {t['artiest']} - {t['titel']}{_jaar(t)}" for t in verwijderd
    )
    toegevoegd_tekst = "\n".join(
        f"  + {t['artiest']} - {t['titel']}{_jaar(t)}" for t in toegevoegd
    )
    plain = (
        f"Rotatie voltooid: {wissellijst_naam}\n\n"
        f"Verwijderd ({len(verwijderd)}):\n{verwijderd_tekst}\n\n"
        f"Toegevoegd ({len(toegevoegd)}):\n{toegevoegd_tekst}\n"
        f"{instagram_tekst}"
    )

    tekst = MIMEMultipart("alternative")
    tekst.attach(MIMEText(plain, "plain", "utf-8"))
    tekst.attach(MIMEText(html, "html", "utf-8"))
    if plaatjes:
        msg = MIMEMultipart("mixed")
        msg.attach(tekst)
        for p in plaatjes:
            bijlage = MIMEImage(p["png"], "png")
            bijlage.add_header("Content-Disposition", "attachment", filename=p["bestandsnaam"])
            msg.attach(bijlage)
    else:
        msg = tekst
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM or SMTP_USER
    msg["To"] = to_address

    try:
        _send(msg, to_address)
        logger.info("Rotatie-mail verstuurd",
                    extra={"naar": to_address, "wissellijst": wissellijst_naam,
                           "plaatjes": len(plaatjes)})
    except Exception as e:
        logger.error("Fout bij mail versturen",
                     extra={"naar": to_address, "error": str(e)})

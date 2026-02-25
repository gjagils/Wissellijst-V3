"""E-mail notificaties na rotatie."""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM
from logging_config import get_logger

logger = get_logger(__name__)


def mail_configured():
    """Controleer of SMTP-instellingen geconfigureerd zijn."""
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASS)


def send_rotation_mail(to_address, wissellijst_naam, verwijderd, toegevoegd):
    """Stuur een e-mail met de rotatie-samenvatting."""
    if not mail_configured():
        logger.warning("SMTP niet geconfigureerd",
                       extra={"host": SMTP_HOST or "leeg",
                              "user": SMTP_USER or "leeg"})
        return
    if not to_address:
        logger.warning("Geen ontvanger-adres opgegeven, mail overgeslagen")
        return

    subject = f"Rotatie voltooid: {wissellijst_naam}"

    verwijderd_html = "".join(
        f"<li>{t['artiest']} &mdash; {t['titel']}</li>" for t in verwijderd
    )
    toegevoegd_html = "".join(
        f"<li>{t['artiest']} &mdash; {t['titel']}</li>" for t in toegevoegd
    )

    html = f"""\
<html>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #121212; color: #e0e0e0; padding: 24px;">
  <div style="max-width: 600px; margin: 0 auto; background: #1e1e1e; border-radius: 12px; padding: 24px;">
    <h2 style="color: #1db954; margin-top: 0;">Rotatie: {wissellijst_naam}</h2>

    <h3 style="color: #ff5252;">Verwijderd ({len(verwijderd)} tracks)</h3>
    <ul style="padding-left: 20px;">{verwijderd_html}</ul>

    <h3 style="color: #1db954;">Toegevoegd ({len(toegevoegd)} tracks)</h3>
    <ul style="padding-left: 20px;">{toegevoegd_html}</ul>

    <p style="color: #888; font-size: 12px; margin-top: 24px;">
      Dit is een automatisch bericht van Wissellijst.
    </p>
  </div>
</body>
</html>"""

    verwijderd_tekst = "\n".join(
        f"  - {t['artiest']} - {t['titel']}" for t in verwijderd
    )
    toegevoegd_tekst = "\n".join(
        f"  + {t['artiest']} - {t['titel']}" for t in toegevoegd
    )
    plain = (
        f"Rotatie voltooid: {wissellijst_naam}\n\n"
        f"Verwijderd ({len(verwijderd)}):\n{verwijderd_tekst}\n\n"
        f"Toegevoegd ({len(toegevoegd)}):\n{toegevoegd_tekst}\n"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = MAIL_FROM or SMTP_USER
    msg["To"] = to_address
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.send_message(msg)
        logger.info("Rotatie-mail verstuurd",
                     extra={"naar": to_address, "wissellijst": wissellijst_naam})
    except Exception as e:
        logger.error("Fout bij mail versturen",
                     extra={"naar": to_address, "error": str(e)})

"""Toegangsbeveiliging: één wachtwoord, sessie-cookie.

Zet APP_PASSWORD in de omgeving. Zonder APP_PASSWORD blijft de app open en
logt hij bij elke start een waarschuwing; de UI toont dan een rode balk.
"""
import datetime
import hashlib
import hmac
import os
import time

from flask import jsonify, redirect, render_template, request, session

from logging_config import get_logger

logger = get_logger(__name__)

APP_PASSWORD = os.getenv("APP_PASSWORD", "")

# Paden die altijd bereikbaar zijn.
_OPEN_PATHS = {"/health", "/auth/login", "/auth/logout"}


def auth_enabled():
    return bool(APP_PASSWORD)


def _secret_key():
    """Stabiele secret key: uit SECRET_KEY, anders afgeleid van APP_PASSWORD."""
    explicit = os.getenv("SECRET_KEY", "")
    if explicit:
        return explicit
    if APP_PASSWORD:
        return hashlib.sha256(("wissellijst:" + APP_PASSWORD).encode()).hexdigest()
    return os.urandom(32)


def _is_logged_in():
    return bool(session.get("auth"))


def setup(app):
    """Registreer de beveiliging op een Flask-app."""
    app.secret_key = _secret_key()
    app.permanent_session_lifetime = datetime.timedelta(days=30)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.getenv("SESSION_COOKIE_SECURE", "1") == "1",
    )

    if not auth_enabled():
        logger.warning("APP_PASSWORD niet gezet: de app is NIET beveiligd")

    @app.before_request
    def _require_login():
        if not auth_enabled():
            return None
        if request.path in _OPEN_PATHS or request.path.startswith("/static/"):
            return None
        if _is_logged_in():
            return None
        if request.path.startswith("/api/"):
            return jsonify({"error": "login_required"}), 401
        return redirect("/auth/login")

    @app.route("/auth/login", methods=["GET", "POST"])
    def auth_login():
        if not auth_enabled():
            return redirect("/")
        fout = None
        if request.method == "POST":
            wachtwoord = request.form.get("wachtwoord", "")
            if hmac.compare_digest(wachtwoord.encode(), APP_PASSWORD.encode()):
                session.permanent = True
                session["auth"] = True
                logger.info("Ingelogd", extra={"ip": request.remote_addr})
                return redirect("/")
            # Afremmen van gokken; geen echte rate limiter, wel genoeg voor één gebruiker.
            time.sleep(1.5)
            logger.warning("Mislukte login", extra={"ip": request.remote_addr})
            fout = "Onjuist wachtwoord"
        return render_template("login.html", fout=fout), (200 if not fout else 401)

    @app.route("/auth/logout", methods=["POST", "GET"])
    def auth_logout():
        session.clear()
        return redirect("/auth/login" if auth_enabled() else "/")

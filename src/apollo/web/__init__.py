"""Flask application factory for HTML and the versioned JSON API."""

import os
import secrets
import signal

from flask import Flask, abort, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException

from apollo.services import ApolloService, ServiceError


def terminate_process():
    os.kill(os.getpid(), signal.SIGTERM)


def create_app(service=None, test_config=None):
    app = Flask(__name__)
    service = service or ApolloService()
    directory = service.config.state_dir
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_path = directory / "web-secret"
    try:
        fd = os.open(secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    app.config.from_mapping(
        SECRET_KEY=secret_path.read_text(),
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Strict",
        API_TOKEN=os.environ.get("APOLLO_API_TOKEN") or service.config.get("API_TOKEN"),
        RESTART_CALLBACK=terminate_process,
    )
    if test_config:
        app.config.update(test_config)
    from apollo.jobs import JobStore

    app.extensions["apollo_service"] = service
    app.extensions["apollo_jobs"] = JobStore(directory)

    def csrf_token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    @app.context_processor
    def context():
        return {"csrf_token": csrf_token}

    @app.before_request
    def protect():
        token = app.config["API_TOKEN"]
        header = request.headers.get("Authorization", "")
        bearer = bool(token and secrets.compare_digest(header, "Bearer " + token))
        if request.endpoint == "api.restart":
            if not token:
                abort(503, "Configure API_TOKEN before enabling remote restart.")
            if not bearer:
                abort(401, "Bearer authentication required.")
        public = request.endpoint in {"login", "static"}
        if token and not public and not bearer and not session.get("authenticated"):
            if request.path.startswith("/api/"):
                abort(401, "Authentication required.")
            return redirect(url_for("login"))
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not bearer:
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            expected = session.get("csrf")
            if not expected or not secrets.compare_digest(expected, supplied):
                abort(400, "Invalid CSRF token. Reload the page and try again.")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            token = app.config["API_TOKEN"]
            if token and secrets.compare_digest(request.form.get("token", ""), token):
                session.clear()
                session["authenticated"] = True
                return redirect(url_for("pages.dashboard"))
            error = "Invalid access token."
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(HTTPException)
    @app.errorhandler(ValueError)
    @app.errorhandler(ServiceError)
    def error(exc):
        code = (
            exc.code
            if isinstance(exc, HTTPException)
            else 503
            if isinstance(exc, ServiceError)
            else 400
        )
        message = exc.description if isinstance(exc, HTTPException) else str(exc)
        if request.path.startswith("/api/"):
            return jsonify(error={"code": code, "message": message}), code
        return render_template("error.html", message=message), code

    @app.errorhandler(Exception)
    def unexpected(exc):
        app.logger.error("Request failed (%s)", type(exc).__name__)
        message = "An unexpected error occurred. Check the server configuration and logs."
        if request.path.startswith("/api/"):
            return jsonify(error={"code": 500, "message": message}), 500
        return render_template("error.html", message=message), 500

    from apollo.web.api import api
    from apollo.web.pages import pages

    app.register_blueprint(api, url_prefix="/api/v1")
    app.register_blueprint(pages)
    return app

"""
Flask Application Factory module.
Configures extensions, rate limiting, security headers, error handlers, and blueprints.
"""

import logging
import os
from typing import Optional, Dict, Any

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app.config import config
from app.utils import setup_structured_logging

logger = logging.getLogger(__name__)

# Initialize rate limiter with environment configuration
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[config.RATE_LIMIT],
    storage_uri=config.RATE_LIMIT_STORAGE_URI,
    strategy="fixed-window",
)


def create_app(test_config: Optional[Dict[str, Any]] = None) -> Flask:
    """Application factory for Flask code sandbox."""
    setup_structured_logging()
    
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    template_dir = os.path.join(base_dir, "templates")
    static_dir = os.path.join(base_dir, "static")

    app = Flask(
        __name__,
        template_folder=template_dir,
        static_folder=static_dir,
    )

    # Core configuration
    app.config["SECRET_KEY"] = config.SECRET_KEY
    app.config["MAX_CONTENT_LENGTH"] = config.MAX_CONTENT_LENGTH

    if test_config:
        app.config.update(test_config)

    # Initialize extensions
    limiter.init_app(app)

    # Register blueprints
    from app.routes import api_bp, main_bp
    
    # Apply rate limiter to execute route specifically as well
    limiter.limit(config.RATE_LIMIT)(api_bp)
    
    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp, url_prefix="/api")

    # Security Headers Middleware
    @app.after_request
    def set_security_headers(response):
        # Strict Content-Security-Policy (allows local self-hosted Monaco and trusted fonts/CDN fallback)
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "connect-src 'self'; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self';"
        )
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response

    # Custom Error Handlers
    @app.errorhandler(429)
    def handle_rate_limit_exceeded(e):
        return (
            jsonify({
                "success": False,
                "status": "rate_limited",
                "error": "Too many requests. Try again shortly.",
            }),
            429,
        )

    @app.errorhandler(413)
    def handle_payload_too_large(e):
        return (
            jsonify({
                "success": False,
                "status": "invalid_request",
                "error": f"Payload exceeds maximum allowed size of {config.MAX_CODE_SIZE} bytes.",
            }),
            400,
        )

    @app.errorhandler(404)
    def handle_not_found(e):
        if request.path.startswith("/api/"):
            return jsonify({"success": False, "status": "not_found", "error": "Endpoint not found."}), 404
        return ("Page not found", 404)

    @app.errorhandler(500)
    def handle_internal_server_error(e):
        logger.error("Unhandled internal server error: %s", str(e), exc_info=True)
        return (
            jsonify({
                "success": False,
                "status": "internal_error",
                "error": "Execution could not be completed.",
            }),
            500,
        )

    return app

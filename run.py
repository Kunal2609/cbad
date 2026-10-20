"""
Application entry point.
Initializes Flask app, executes startup container reaper, and starts periodic cleanup.
"""

import os
import logging
from app import create_app
from app.reaper import run_startup_reaper, start_background_reaper

logger = logging.getLogger(__name__)

# Create Flask application
app = create_app()

# Initialize startup container reaper to clear any leftover orphan containers
try:
    reaped = run_startup_reaper()
    if reaped:
        logger.info("Startup reaper cleaned %d leftover sandbox containers.", reaped)
except Exception as e:
    logger.warning("Startup reaper failed (Docker daemon may not be active yet): %s", str(e))

# Start periodic background reaper thread
try:
    start_background_reaper()
except Exception as e:
    logger.warning("Failed to start periodic background reaper: %s", str(e))


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    
    print(f"\n=======================================================")
    print(f"  Python Code Execution Sandbox starting on port {port}")
    print(f"  Access web interface at: http://localhost:{port}")
    print(f"=======================================================\n")
    
    app.run(host="0.0.0.0", port=port, debug=debug)

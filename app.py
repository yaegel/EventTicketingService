import logging
import atexit
from flask import Flask, jsonify
from config import Config
from database import init_db
from routes import api_bp
from worker import start_worker, stop_worker

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)
logger = logging.getLogger("app")

def create_app():
    """Application factory for Event Ticketing Service."""
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize SQLite database schema
    init_db()
    logger.info(f"Database initialized at: {Config.DATABASE_PATH}")

    # Register API blueprints
    app.register_blueprint(api_bp)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify({
            "status": "healthy",
            "service": "EventTicketingService"
        }), 200

    return app

app = create_app()

# Start background worker when running the application
start_worker(app)
atexit.register(stop_worker)

if __name__ == "__main__":
    logger.info(f"Starting server on {Config.HOST}:{Config.PORT}...")
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=Config.DEBUG,
        use_reloader=False  # Avoid duplicate worker threads in Flask reload mode
    )

"""Top-level runner for the ChatBotMobile Flask app.

This file provides the main entry point for running the Flask application,
configuring logging, and initializing required services.
"""
from app_pkg import create_app
from app_pkg.logger import setup_logger
from config import Config
import google.generativeai as genai
genai.configure(api_key="AIzaSyDyHN4DInZrAHrUHbObZchZGS21VEEKBoU")
# Set up application-level logger
logger = setup_logger(__name__)

app = create_app()

# Register auth_receiver blueprint so /save_auth runs with main app
try:
    # import the blueprint and register it
    from auth_receiver import auth_bp
    app.register_blueprint(auth_bp)
except Exception as e:
    logger.warning(f"Could not register auth_receiver blueprint: {e}")

if __name__ == "__main__":
    logger.info("Starting ChatBotMobile application...")
    app.run(
        host="0.0.0.0",
        port=Config.PORT,
        debug=Config.DEBUG
    )
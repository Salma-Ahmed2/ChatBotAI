"""Logging configuration for the ChatBotMobile application.

This module provides a centralized logging configuration that can be used
across the entire application.
"""
import logging
import logging.handlers
import os
from config import Config, BASE_DIR

def setup_logger(name: str) -> logging.Logger:
    """Set up and return a logger instance with the specified name.
    
    Args:
        name: The name of the logger, typically __name__ from the calling module
        
    Returns:
        A configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid adding handlers if they already exist
    if logger.handlers:
        return logger
        
    logger.setLevel(Config.LOG_LEVEL)
    
    # Create logs directory if it doesn't exist
    log_dir = os.path.join(BASE_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    # File handler for all logs
    # file_handler = logging.handlers.RotatingFileHandler(
    #     os.path.join(log_dir, "chatbot.log"),
    #     maxBytes=10485760,  # 10MB
    #     backupCount=5,
    #     encoding="utf-8"
    # )
    # file_handler.setFormatter(logging.Formatter(Config.LOG_FORMAT))
    # logger.addHandler(file_handler)
    
    # Console handler for debugging
    if Config.DEBUG:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(logging.Formatter(Config.LOG_FORMAT))
        logger.addHandler(console_handler)
    
    return logger
import logging
import os
from logging.handlers import RotatingFileHandler

# Create logs directory if it doesn't exist
if not os.path.exists('logs'):
    os.makedirs('logs')

def get_logger(name):
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Format for logs
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        # Console handler — force UTF-8, replace unencodable chars (emojis) safely on Windows
        import sys
        try:
            console_handler = logging.StreamHandler(
                stream=open(sys.stdout.fileno(), mode='w', encoding='utf-8', errors='replace', buffering=1, closefd=False)
            )
        except Exception:
            console_handler = logging.StreamHandler()
            console_handler.stream.reconfigure(encoding='utf-8', errors='replace') if hasattr(console_handler.stream, 'reconfigure') else None
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # File handler (with rotation)
        file_handler = RotatingFileHandler(
            'logs/bot.log', 
            maxBytes=10*1024*1024, # 10MB
            backupCount=5
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        
    return logger

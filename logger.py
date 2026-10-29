import logging
import os
import re
from logging.handlers import RotatingFileHandler

# Telegram bot tokens look like 123456789:AAH... and appear inside API URLs
# (https://api.telegram.org/bot<token>/...), which end up in exception text.
_TOKEN_RE = re.compile(r'bot\d{6,}:[A-Za-z0-9_\-]{20,}')
_BARE_TOKEN_RE = re.compile(r'\b\d{8,}:[A-Za-z0-9_\-]{30,}\b')


def redact_secrets(text: str) -> str:
    text = _TOKEN_RE.sub('bot<REDACTED>', text)
    return _BARE_TOKEN_RE.sub('<REDACTED>', text)


class RedactingFormatter(logging.Formatter):
    """Formatter that scrubs bot tokens from every formatted record."""

    def format(self, record):
        return redact_secrets(super().format(record))


# Noisy HTTP client loggers print full request URLs (including the bot token).
for _noisy in ('httpx', 'httpcore', 'urllib3'):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# Create logs directory if it doesn't exist
if not os.path.exists('logs'):
    os.makedirs('logs')

def get_logger(name):
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Format for logs
        formatter = RedactingFormatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
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

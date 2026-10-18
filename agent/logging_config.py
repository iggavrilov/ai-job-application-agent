import logging
from logging.handlers import RotatingFileHandler
from agent.settings import ROOT


def setup_logging():
    """Shared CLI/dashboard logs; repeated Streamlit reruns do not add handlers."""
    logger = logging.getLogger('agent')
    if not logger.handlers:
        (ROOT/'data').mkdir(exist_ok=True)
        handler = RotatingFileHandler(ROOT/'data/agent.log',maxBytes=2_000_000,backupCount=3,encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

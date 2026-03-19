import logging
import os
from datetime import datetime

def setup_logger(name, log_file, level=logging.INFO):
    """Function to setup as many loggers as you want"""

    # Create logs directory if it doesn't exist
    log_dir = os.path.dirname(log_file)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)

    formatter = logging.Formatter('%(asctime)s %(levelname)s %(message)s')

    handler = logging.FileHandler(log_file)
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.addHandler(handler)

    return logger

# Create a default logger for the application
default_logger = setup_logger('automation_framework', './logs/app.log')

def get_logger(name='automation_framework'):
    """Get a logger instance"""
    if name == 'automation_framework':
        return default_logger
    else:
        return setup_logger(name, f'./logs/{name}.log')
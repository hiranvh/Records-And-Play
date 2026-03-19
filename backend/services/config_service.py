import configparser
import os
from typing import Optional

class ConfigService:
    """Service to manage application configuration from properties file"""

    def __init__(self, config_file_path: str = "./config/configuration.properties"):
        self.config_file_path = config_file_path
        self.config = configparser.ConfigParser()
        self.load_properties()

    def load_properties(self):
        """Load configuration from properties file"""
        if os.path.exists(self.config_file_path):
            self.config.read(self.config_file_path)
        else:
            # Create default config if it doesn't exist
            self._create_default_config()
            self.save_properties()

    def _create_default_config(self):
        """Create default configuration"""
        self.config['Application URLs'] = {
            'base.url': 'https://example.com',
            'login.url': '/login',
            'target.url': '/dashboard'
        }

        self.config['Authentication Credentials'] = {
            'username': 'admin',
            'password': 'password123'
        }

        self.config['Standard Test Data'] = {
            'standard.zipcode': '2075',
            'standard.phone': '(555) 123-4567',
            'standard.email.domain': 'test.com'
        }

        self.config['AI Model Settings'] = {
            'ai.model.path': './model/phi3_model.bin',
            'ai.temperature': '0.7',
            'ai.max_tokens': '500'
        }

        self.config['Playback Settings'] = {
            'playback.speed': 'normal',
            'playback.retries': '3',
            'playback.timeout': '30'
        }

        self.config['Data Generation Settings'] = {
            'data.dynamic.firstname': 'true',
            'data.dynamic.lastname': 'true',
            'data.dynamic.dob': 'true',
            'data.dynamic.gender': 'true',
            'data.mask.ssn': 'true'
        }

        self.config['Reporting Settings'] = {
            'report.format': 'html',
            'report.directory': './reports',
            'log.level': 'INFO'
        }

    def get_property(self, key: str) -> Optional[str]:
        """Get configuration property value"""
        for section in self.config.sections():
            if key in self.config[section]:
                return self.config[section][key]
        return None

    def set_property(self, key: str, value: str) -> bool:
        """Set configuration property value"""
        # Find which section the key belongs to
        for section in self.config.sections():
            if key in self.config[section]:
                self.config[section][key] = str(value)
                return True

        # If key doesn't exist, add it to a default section
        if self.config.sections():
            self.config[self.config.sections()[0]][key] = str(value)
            return True
        return False

    def save_properties(self):
        """Save configuration to properties file"""
        # Ensure the config directory exists
        config_dir = os.path.dirname(self.config_file_path)
        if config_dir and not os.path.exists(config_dir):
            os.makedirs(config_dir)

        with open(self.config_file_path, 'w') as configfile:
            self.config.write(configfile)
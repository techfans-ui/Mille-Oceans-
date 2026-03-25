import os

class Config:
    """Configuration de base."""
    SECRET_KEY = os.environ.get('SECRET_KEY', 'mille-oceans-secret-2026')
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///mille_oceans.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    APP_NAME = 'Mille Oceans — Gestion des Stocks'
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB

    # Seuils d'alerte stock
    STOCK_ALERT_LOW = 10       # Alerte stock bas
    STOCK_ALERT_CRITICAL = 5   # Alerte stock critique


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

"""Django settings for Turbo Notes API."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

ENVIRONMENT = os.getenv("ENVIRONMENT", "local")
DEBUG = os.getenv("DEBUG", "true").lower() in ("1", "true", "yes")
SECRET_KEY = os.getenv("SECRET_KEY", "unsafe-dev-only-key")
ALLOWED_HOSTS = [
    h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "axes",
    "apps.accounts",
    "apps.notes",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "axes.middleware.AxesMiddleware",
    "config.middleware.RequestLoggingMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

POSTGRES_DB = os.getenv("POSTGRES_DB", "turbo_notes")
POSTGRES_USER = os.getenv("POSTGRES_USER", "turbo")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "turbo_dev_password")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")

if os.getenv("USE_SQLITE", "").lower() in ("1", "true", "yes"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": POSTGRES_DB,
            "USER": POSTGRES_USER,
            "PASSWORD": POSTGRES_PASSWORD,
            "HOST": POSTGRES_HOST,
            "PORT": POSTGRES_PORT,
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]
CORS_ALLOW_CREDENTIALS = True

CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:3000").split(",")
    if o.strip()
]

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "Lax")
COOKIE_DOMAIN = os.getenv("COOKIE_DOMAIN") or None
ACCESS_COOKIE_NAME = "access_token"
REFRESH_COOKIE_NAME = "refresh_token"

ACCESS_TOKEN_LIFETIME_MINUTES = int(os.getenv("ACCESS_TOKEN_LIFETIME_MINUTES", "15"))
REFRESH_TOKEN_LIFETIME_DAYS = int(os.getenv("REFRESH_TOKEN_LIFETIME_DAYS", "7"))

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=ACCESS_TOKEN_LIFETIME_MINUTES),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=REFRESH_TOKEN_LIFETIME_DAYS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ("apps.accounts.authentication.CookieJWTAuthentication",),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "50/hour",
        "user": "1000/hour",
        "auth": "20/minute",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Turbo Notes API",
    "DESCRIPTION": "Notes-taking API for the Turbo AI hiring challenge.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_RESET_ON_SUCCESS = True

FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")
CLOUDWATCH_ENABLED = os.getenv("CLOUDWATCH_ENABLED", "false").lower() in ("1", "true", "yes")
CLOUDWATCH_LOG_GROUP = os.getenv("CLOUDWATCH_LOG_GROUP", "")
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": "config.logging_fmt.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django.request": {"level": "WARNING", "propagate": True},
        "apps": {"level": "INFO", "propagate": True},
    },
}

if CLOUDWATCH_ENABLED and CLOUDWATCH_LOG_GROUP:
    # Log group is provisioned by Terraform; task role has stream put/create only.
    LOGGING["handlers"]["cloudwatch"] = {
        "class": "watchtower.CloudWatchLogHandler",
        "log_group_name": CLOUDWATCH_LOG_GROUP,
        "log_stream_name": ENVIRONMENT,
        "create_log_group": False,
        "formatter": "json",
    }
    LOGGING["root"]["handlers"].append("cloudwatch")

SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False

if ENVIRONMENT == "production":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

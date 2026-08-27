"""
================================================================================
FILE: app/core/config.py
MODULE: Module 1 - Core Foundation
--------------------------------------------------------------------------------
WHAT THIS FILE DOES:
Type-safe configuration loader using Pydantic BaseSettings.
ZERO SECRETS ARE HARDCODED HERE. All credentials are read strictly from
the local .env file or environment variables at runtime.
================================================================================
"""
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    # LLM Settings (OpenRouter)
    OPENROUTER_API_KEY: str = Field(default="", description="OpenRouter API Key")
    LLM_BASE_URL: str = Field(default="https://openrouter.ai/api/v1", description="LLM Base URL")
    LLM_MODEL: str = Field(default="openrouter/free", description="LLM Model")

    # Razorpay Credentials (Loaded strictly from .env)
    RAZORPAY_KEY_ID: str = Field(default="", description="Razorpay Key ID")
    RAZORPAY_KEY_SECRET: str = Field(default="", description="Razorpay Key Secret")
    RAZORPAY_WEBHOOK_SECRET: str = Field(default="", description="Razorpay Webhook Secret")

    # Cryptographic Proposal Signing Key
    HMAC_SECRET_KEY: str = Field(default="", description="HMAC Proposal Secret")

    # Auth (deliberately no insecure default - a shared fallback secret across
    # every clone of this repo would let anyone forge a valid merchant session
    # token. Auth endpoints refuse with a clear error if this is unset, rather
    # than silently signing with a weak, guessable value.)
    JWT_SECRET_KEY: str = Field(default="", description="Secret used to sign merchant session JWTs")
    JWT_EXPIRE_MINUTES: int = Field(default=60 * 24 * 7, description="Session JWT lifetime in minutes (default 7 days)")

    # Encryption-at-rest key for merchant-supplied Razorpay credentials.
    # Must be a valid Fernet key (32 url-safe base64 bytes) - generate one
    # with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Same reasoning as JWT_SECRET_KEY: no default, since a shared default
    # would make every deployment's encrypted Razorpay secrets equally
    # decryptable by anyone with the source.
    FERNET_KEY: str = Field(default="", description="Fernet key encrypting merchant Razorpay credentials at rest")

    # Voice channel (Twilio + ElevenLabs + Deepgram)
    TWILIO_ACCOUNT_SID: str = Field(default="", description="Twilio Account SID")
    TWILIO_AUTH_TOKEN: str = Field(default="", description="Twilio Auth Token")
    TWILIO_PHONE_NUMBER: str = Field(default="", description="Twilio outbound caller ID")
    ELEVENLABS_API_KEY: str = Field(default="", description="ElevenLabs TTS API Key")
    DEEPGRAM_API_KEY: str = Field(default="", description="Deepgram STT API Key")
    NGROK_URL: str = Field(default="", description="Public tunnel URL for Twilio voice webhooks")

    # Email channel
    RESEND_API_KEY: str = Field(default="", description="Resend Email API Key")
    EMAIL_FROM: str = Field(default="", description="Sender address for recovery emails")
    CUSTOMER_EMAIL: str = Field(default="", description="Demo customer email for the golden-path script")
    CUSTOMER_PHONE: str = Field(default="", description="Demo customer phone number for the live call demo")

    # Server Networking
    ENVIRONMENT: str = Field(default="development", description="Runtime environment")
    PORT: int = Field(default=8000, description="Server port")
    HOST: str = Field(default="0.0.0.0", description="Server host")
    
    # Database
    DATABASE_URL: str = Field(default="", description="PostgreSQL connection string")
    
    CORS_ORIGINS: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000,https://*.vercel.app",
        description="Allowed CORS origins"
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


settings = Settings()

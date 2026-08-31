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
    # A real, routable slug. The old default was "openrouter/free", which is
    # not a model OpenRouter will serve - so any deployment that forgot to
    # set LLM_MODEL degraded every agent run and looked like a model
    # problem rather than a missing config line.
    LLM_MODEL: str = Field(default="openai/gpt-4o-mini", description="LLM Model")

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
    # Who the agent sounds like. These two must describe the SAME PERSON.
    #
    # A call does not use one voice: the opening turn plays an ElevenLabs
    # block and then a Twilio <Say> keypad note back to back, so both are
    # heard on every single call, and any later turn flips to the fallback
    # whenever the 2s budget overruns. They were previously matched (both
    # female) by luck, with nothing requiring it and nothing noticing if
    # one changed.
    #
    # An empty ELEVENLABS_VOICE_ID disables ElevenLabs entirely and renders
    # every line through TWILIO_VOICE_NAME - one consistent voice, no 2s
    # budget to overrun, less expressive. A supported configuration, not a
    # broken one.
    ELEVENLABS_VOICE_ID: str = Field(
        default="", description="ElevenLabs voice id. Empty = render everything with the Twilio voice."
    )
    # Google.en-IN-Neural2-B: male, Indian English, neural. Polly has no
    # male en-IN neural voice - Twilio exposes Google's voices alongside
    # Amazon's, and that is where the male Indian options are.
    TWILIO_VOICE_NAME: str = Field(
        default="Google.en-IN-Neural2-B", description="Twilio <Say> voice, and the TTS fallback."
    )
    # What Twilio's recogniser expects to hear. ONE BCP-47 value - <Gather>
    # has no bilingual mode, so this is a genuine either/or.
    #
    # en-IN rather than hi-IN deliberately: Indian English transcribes
    # code-switched Hinglish far better than Hindi transcribes English, and
    # a mis-transcription here is not cosmetic. It feeds misheard_streak and
    # the REJECTED_LOW_CONFIDENCE gate that stops the money tools running,
    # so getting it wrong degrades the money path, not just the transcript.
    VOICE_GATHER_LANGUAGE: str = Field(
        default="en-IN", description="BCP-47 language for Twilio speech recognition."
    )
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
        default="http://localhost:3000,http://127.0.0.1:3000",
        description="Allowed CORS origins, matched EXACTLY (no wildcards)"
    )
    # Starlette matches allow_origins by exact string. The default here used
    # to include "https://*.vercel.app", which matched nothing and never
    # could - the browser was told nothing was allowed, so a deployed
    # frontend loaded fine and every request it made failed, with the
    # config appearing to permit exactly what it was blocking.
    #
    # Patterns need allow_origin_regex, so that is a separate setting and
    # it is OPT-IN. Left empty by default deliberately: anyone can deploy
    # to vercel.app, and a blanket https://.*\.vercel\.app combined with
    # allow_credentials would let any site on that domain make
    # authenticated requests with a logged-in merchant's cookie. Set it to
    # your OWN preview pattern if you need one.
    CORS_ORIGIN_REGEX: str = Field(
        default="",
        description=r"Optional regex for allowed origins, e.g. https://kinato-[a-z0-9-]+\.vercel\.app",
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

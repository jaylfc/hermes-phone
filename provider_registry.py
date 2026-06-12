"""
Provider registry — maps provider IDs to their required packages,
install commands, and model download info.
"""

PROVIDER_DEPS = {
    # ═══════════════════════════════════════════════════════════════
    # STT Providers
    # ═══════════════════════════════════════════════════════════════
    "deepgram": {
        "name": "Deepgram Nova-3",
        "type": "stt",
        "backend": "cloud",
        "packages": ["deepgram"],
        "pip_install": "pip install deepgram-sdk",
        "env_vars": ["DEEPGRAM_API_KEY"],
        "recommended": True,
    },
    "mimo-stt": {
        "name": "MiMo 2.5 STT",
        "type": "stt",
        "backend": "cloud",
        "packages": ["openai"],
        "pip_install": "pip install openai",
        "env_vars": ["XIAOMI_API_KEY", "XIAOMI_BASE_URL"],
        "recommended": True,
    },
    "assemblyai": {
        "name": "AssemblyAI Universal-3",
        "type": "stt",
        "backend": "cloud",
        "packages": ["assemblyai"],
        "pip_install": "pip install assemblyai",
        "env_vars": ["ASSEMBLYAI_API_KEY"],
    },
    "google": {
        "name": "Google Cloud STT",
        "type": "stt",
        "backend": "cloud",
        "packages": ["google.cloud.speech"],
        "pip_install": "pip install google-cloud-speech",
        "env_vars": ["GOOGLE_STT_CREDENTIALS"],
    },
    "azure": {
        "name": "Azure Speech",
        "type": "stt",
        "backend": "cloud",
        "packages": ["azure.cognitiveservices.speech"],
        "pip_install": "pip install azure-cognitiveservices-speech",
        "env_vars": ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"],
    },
    "groq": {
        "name": "Groq Whisper",
        "type": "stt",
        "backend": "cloud",
        "packages": ["groq"],
        "pip_install": "pip install groq",
        "env_vars": ["GROQ_API_KEY"],
    },
    "speechmatics": {
        "name": "Speechmatics",
        "type": "stt",
        "backend": "cloud",
        "packages": ["speechmatics"],
        "pip_install": "pip install speechmatics",
        "env_vars": ["SPEECHMATICS_API_KEY"],
    },
    "whisper-api": {
        "name": "OpenAI Whisper API",
        "type": "stt",
        "backend": "cloud",
        "packages": ["openai"],
        "pip_install": "pip install openai",
        "env_vars": ["OPENAI_API_KEY"],
    },
    "mlx-whisper": {
        "name": "mlx-whisper (Apple Silicon)",
        "type": "stt",
        "backend": "local",
        "packages": ["mlx_whisper"],
        "pip_install": "pip install mlx-whisper",
        "models": {
            "mlx-community/whisper-large-v3-turbo": {"size": "~1.6GB", "auto_download": True},
            "mlx-community/whisper-large-v3": {"size": "~3GB", "auto_download": True},
            "mlx-community/whisper-small": {"size": "~460MB", "auto_download": True},
        },
        "recommended": True,
    },
    "faster-whisper": {
        "name": "faster-whisper (CTranslate2)",
        "type": "stt",
        "backend": "local",
        "packages": ["faster_whisper"],
        "pip_install": "pip install faster-whisper",
        "models": {
            "large-v3": {"size": "~1.5GB", "auto_download": True},
            "medium": {"size": "~770MB", "auto_download": True},
            "small": {"size": "~460MB", "auto_download": True},
            "base": {"size": "~140MB", "auto_download": True},
            "tiny": {"size": "~75MB", "auto_download": True},
        },
    },
    "whisper.cpp": {
        "name": "whisper.cpp (C/C++)",
        "type": "stt",
        "backend": "local",
        "packages": [],
        "install_cmd": "brew install whisper-cpp",
        "models": {
            "ggml-large-v3": {"size": "~3GB", "auto_download": True},
            "ggml-medium": {"size": "~1.5GB", "auto_download": True},
            "ggml-small": {"size": "~500MB", "auto_download": True},
            "ggml-base": {"size": "~150MB", "auto_download": True},
            "ggml-tiny": {"size": "~75MB", "auto_download": True},
        },
    },
    "vosk": {
        "name": "Vosk (offline, lightweight)",
        "type": "stt",
        "backend": "local",
        "packages": ["vosk"],
        "pip_install": "pip install vosk",
        "models": {
            "vosk-model-en-us-0.22": {"size": "~1.8GB", "auto_download": True},
            "vosk-model-small-en-us-0.15": {"size": "~40MB", "auto_download": True},
        },
    },
    "wav2vec2": {
        "name": "wav2vec2 (Meta)",
        "type": "stt",
        "backend": "local",
        "packages": ["transformers", "torch"],
        "pip_install": "pip install transformers torch",
        "models": {
            "facebook/wav2vec2-large-960h": {"size": "~1.2GB", "auto_download": True},
        },
    },
    "canary": {
        "name": "NVIDIA Canary",
        "type": "stt",
        "backend": "local",
        "packages": ["nemo_toolkit"],
        "pip_install": "pip install nemo_toolkit[asr]",
        "models": {
            "nvidia/canary-1b": {"size": "~3GB", "auto_download": True},
        },
    },

    # ═══════════════════════════════════════════════════════════════
    # TTS Providers
    # ═══════════════════════════════════════════════════════════════
    "polly": {
        "name": "AWS Polly",
        "type": "tts",
        "backend": "cloud",
        "packages": ["boto3"],
        "pip_install": "pip install boto3",
        "env_vars": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
    },
    "mimo": {
        "name": "MiMo 2.5 TTS",
        "type": "tts",
        "backend": "cloud",
        "packages": ["openai"],
        "pip_install": "pip install openai",
        "env_vars": ["XIAOMI_API_KEY", "XIAOMI_BASE_URL"],
        "recommended": True,
    },
    "elevenlabs": {
        "name": "ElevenLabs",
        "type": "tts",
        "backend": "cloud",
        "packages": ["elevenlabs"],
        "pip_install": "pip install elevenlabs",
        "env_vars": ["ELEVENLABS_API_KEY"],
    },
    "openai-tts": {
        "name": "OpenAI TTS",
        "type": "tts",
        "backend": "cloud",
        "packages": ["openai"],
        "pip_install": "pip install openai",
        "env_vars": ["OPENAI_API_KEY"],
    },
    "azure-tts": {
        "name": "Azure Speech",
        "type": "tts",
        "backend": "cloud",
        "packages": ["azure.cognitiveservices.speech"],
        "pip_install": "pip install azure-cognitiveservices-speech",
        "env_vars": ["AZURE_TTS_KEY", "AZURE_TTS_REGION"],
    },
    "google-tts": {
        "name": "Google Cloud TTS",
        "type": "tts",
        "backend": "cloud",
        "packages": ["google.cloud.texttospeech"],
        "pip_install": "pip install google-cloud-texttospeech",
        "env_vars": ["GOOGLE_TTS_CREDENTIALS"],
    },
    "cartesia": {
        "name": "Cartesia Sonic",
        "type": "tts",
        "backend": "cloud",
        "packages": ["cartesia"],
        "pip_install": "pip install cartesia",
        "env_vars": ["CARTESIA_API_KEY"],
    },
    "deepgram_aura": {
        "name": "Deepgram Aura",
        "type": "tts",
        "backend": "cloud",
        "packages": ["deepgram"],
        "pip_install": "pip install deepgram-sdk",
        "env_vars": ["DEEPGRAM_API_KEY"],
    },
    "fish": {
        "name": "Fish Audio",
        "type": "tts",
        "backend": "cloud",
        "packages": ["fish_audio_sdk"],
        "pip_install": "pip install fish-audio-sdk",
        "env_vars": ["FISH_AUDIO_API_KEY"],
    },
    "edge": {
        "name": "Edge TTS (free)",
        "type": "tts",
        "backend": "cloud",
        "packages": ["edge_tts"],
        "pip_install": "pip install edge-tts",
        "env_vars": [],
    },
    "kokoro": {
        "name": "Kokoro 82M (MLX)",
        "type": "tts",
        "backend": "local",
        "packages": ["mlx_audio"],
        "pip_install": "pip install mlx-audio",
        "models": {
            "kokoro-82m-4bit": {"size": "~50MB", "auto_download": True},
        },
        "recommended": True,
    },
    "piper": {
        "name": "Piper (C++, lightweight)",
        "type": "tts",
        "backend": "local",
        "packages": ["piper"],
        "pip_install": "pip install piper-tts",
        "models": {
            "en_US-lessac-medium": {"size": "~60MB", "auto_download": True},
        },
    },
    "coqui": {
        "name": "Coqui XTTS v2",
        "type": "tts",
        "backend": "local",
        "packages": ["TTS"],
        "pip_install": "pip install TTS",
        "models": {
            "tts_models/multilingual/multi-dataset/xtts_v2": {"size": "~1.8GB", "auto_download": True},
        },
    },
    "bark": {
        "name": "Bark (Suno)",
        "type": "tts",
        "backend": "local",
        "packages": ["bark"],
        "pip_install": "pip install suno-bark",
        "models": {
            "suno/bark": {"size": "~5GB", "auto_download": True},
        },
    },
    "tortoise": {
        "name": "Tortoise TTS",
        "type": "tts",
        "backend": "local",
        "packages": ["tortoise"],
        "pip_install": "pip install tortoise-tts",
    },
    "vits": {
        "name": "VITS / VITS2",
        "type": "tts",
        "backend": "local",
        "packages": ["TTS"],
        "pip_install": "pip install TTS",
    },
    "styletts2": {
        "name": "StyleTTS 2",
        "type": "tts",
        "backend": "local",
        "packages": ["styletts2"],
        "pip_install": "pip install styletts2",
    },
    "chattts": {
        "name": "ChatTTS",
        "type": "tts",
        "backend": "local",
        "packages": ["ChatTTS"],
        "pip_install": "pip install chattts",
    },
    "sesame": {
        "name": "Sesame CSM",
        "type": "tts",
        "backend": "local",
        "packages": ["transformers"],
        "pip_install": "pip install transformers",
    },
    "speecht5": {
        "name": "SpeechT5 (Microsoft)",
        "type": "tts",
        "backend": "local",
        "packages": ["transformers", "torch"],
        "pip_install": "pip install transformers torch",
        "models": {
            "microsoft/speecht5_tts": {"size": "~1GB", "auto_download": True},
        },
    },

    # ═══════════════════════════════════════════════════════════════
    # Agent Backends (LLM / AI)
    # ═══════════════════════════════════════════════════════════════
    "hermes-gateway": {
        "name": "Hermes Agent (Gateway API)",
        "type": "agent",
        "backend": "cloud",
        "packages": ["requests"],
        "pip_install": "pip install requests",
        "env_vars": ["HERMES_GATEWAY_URL", "HERMES_GATEWAY_TOKEN"],
        "recommended": True,
    },
    "openai": {
        "name": "OpenAI",
        "type": "agent",
        "backend": "cloud",
        "packages": ["openai"],
        "pip_install": "pip install openai",
        "env_vars": ["OPENAI_API_KEY"],
    },
    "openrouter": {
        "name": "OpenRouter",
        "type": "agent",
        "backend": "cloud",
        "packages": ["openai"],
        "pip_install": "pip install openai",
        "env_vars": ["OPENROUTER_API_KEY"],
    },
    "ollama": {
        "name": "Ollama (local)",
        "type": "agent",
        "backend": "local",
        "packages": [],
        "env_vars": ["OLLAMA_BASE_URL"],
    },
    "lmstudio": {
        "name": "LM Studio (local)",
        "type": "agent",
        "backend": "local",
        "packages": [],
        "env_vars": ["LMSTUDIO_BASE_URL"],
    },
}


def check_provider_installed(provider_id):
    """Check if a provider's packages are installed."""
    provider = PROVIDER_DEPS.get(provider_id)
    if not provider:
        return {"installed": False, "error": "Unknown provider"}
    
    missing = []
    for pkg in provider.get("packages", []):
        try:
            __import__(pkg.replace("-", "_"))
        except ImportError:
            missing.append(pkg)
    
    return {
        "installed": len(missing) == 0,
        "missing": missing,
        "install_cmd": provider.get("pip_install") or provider.get("install_cmd"),
    }


def get_provider_status():
    """Get installation status of all providers."""
    status = {}
    for pid, info in PROVIDER_DEPS.items():
        check = check_provider_installed(pid)
        status[pid] = {
            "name": info["name"],
            "type": info["type"],
            "backend": info.get("backend", "local"),
            "installed": check["installed"],
            "missing": check["missing"],
            "recommended": info.get("recommended", False),
            "models": list(info.get("models", {}).keys()),
        }
    return status

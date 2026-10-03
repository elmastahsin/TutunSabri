"""Configuration values for the local Ollama integration."""

OLLAMA_BASE_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3:4b-instruct"
OLLAMA_TIMEOUT_SECONDS = 180.0
OLLAMA_MAX_OUTPUT_TOKENS = 1024

SYSTEM_PROMPT = (
    "Her zaman Türkçe yanıt veren, teknik konularda yardımcı bir asistansın. "
    "Yanıtlarını açık, doğru ve uygulanabilir tut. Bilmediğin veya emin olmadığın "
    "konularda bilgi uydurma; belirsizliği açıkça belirt."
)

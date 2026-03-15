# config.py

MODEL_NAME = "gemini-3.1-flash-preview"  # Default model
# Có thể đổi sang: "gemini-3.1-pro-preview", "gemini-3-pro-preview", "gemini-2.5-pro", "llama-3.3-70b-versatile"

# Các Key cho Gemini (lấy từ https://aistudio.google.com/)
GEMINI_KEYS_PAID = ["YOUR_GEMINI_API_KEY_HERE"] 

# Thay thế bằng API keys thực tế của bạn
GROQ_KEYS_A = ["key_a1"]
GROQ_KEYS_B = ["key_b1"]
GROQ_KEYS_C = ["key_c1"]
GROQ_KEYS_D = ["key_d1"]
GROQ_KEYS_E = ["key_e1"]
GROQ_KEYS_F = ["key_f1"]
GROQ_KEYS_G = ["key_g1"]
GROQ_KEYS_H = ["key_h1"]
GROQ_KEYS_I = ["key_i1"]
GROQ_KEYS_J = ["key_j1"]

ACCOUNTS = [
    {"name": "Gemini_Paid", "keys": GEMINI_KEYS_PAID},
    {"name": "A", "keys": GROQ_KEYS_A},
    {"name": "B", "keys": GROQ_KEYS_B},
    {"name": "C", "keys": GROQ_KEYS_C},
    {"name": "D", "keys": GROQ_KEYS_D},
    {"name": "E", "keys": GROQ_KEYS_E},
    {"name": "F", "keys": GROQ_KEYS_F},
    {"name": "G", "keys": GROQ_KEYS_G},
    {"name": "H", "keys": GROQ_KEYS_H},
    {"name": "I", "keys": GROQ_KEYS_I},
    {"name": "J", "keys": GROQ_KEYS_J}
]

# Cấu hình rate limit (requests per minute và tokens per minute) theo từng account
# GIỚI HẠN GEMINI TRẢ PHÍ (Pay-as-you-go Tier 1):
# Flash (2.0/2.5/3.0/3.1): 2000 RPM, 4,000,000 TPM
# Pro (2.5/3.0/3.1): 360 RPM, 2,000,000 TPM
RATE_LIMITS = {
    "Gemini_Paid": {"rpm": 2000, "tpm": 4000000},
    "A": {"rpm": 30, "tpm": 6000},
    "B": {"rpm": 30, "tpm": 6000},
    "C": {"rpm": 30, "tpm": 6000},
    "D": {"rpm": 30, "tpm": 6000},
    "E": {"rpm": 30, "tpm": 6000},
    "F": {"rpm": 30, "tpm": 6000},
    "G": {"rpm": 30, "tpm": 6000},
    "H": {"rpm": 30, "tpm": 6000},
    "I": {"rpm": 30, "tpm": 6000},
    "J": {"rpm": 30, "tpm": 6000}
}

# Tổng số lượng worker chạy đồng thời (auto-tuned by RPM, this is the cap)
TOTAL_WORKERS = 120

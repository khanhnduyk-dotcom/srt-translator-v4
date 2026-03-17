# ⚡ SRT Translator v5 — AI Subtitle Translation Tool

Công cụ dịch phụ đề SRT tự động bằng AI. Hỗ trợ Gemini, OpenAI, Groq, DeepSeek, Claude, Ollama.
Tính năng mới V5: **Hybrid Cookie Mode**, **Gemini 3.x**, **chống lệch dòng 100%**, **429 stagger**.

## 🚀 Cài đặt nhanh

### Yêu cầu:
- **Python 3.9+** ([tải tại đây](https://www.python.org/downloads/)) — tick ✅ **"Add Python to PATH"**
- Hoặc dùng **trực tiếp** `index.html` mà không cần Python

### Cách 1: Mở trực tiếp (không cần cài gì)
1. Mở `index.html` bằng Chrome
2. Bấm ⚙️ → chọn Provider → nhập API Key
3. Kéo file `.srt` → chọn ngôn ngữ → bấm **Dịch**

### Cách 2: Có backend (nhanh hơn, multi-thread)
1. Double-click **`INSTALL.bat`** (cài 1 lần)
2. Sửa `config.py` → thêm API key
3. Double-click **`start.bat`** → tự mở trình duyệt

## ✨ Tính năng chính

| Tính năng | Mô tả |
|-----------|-------|
| ⚡ Dịch song song | 2-10 batch đồng thời (theo số key) |
| 🔄 Multi-provider | Gemini, OpenAI, Groq, DeepSeek, Claude, Ollama |
| 🔍 Auto QA | Gửi GỐC+DỊCH → AI so sánh & sửa thông minh |
| 🔎 Tìm & Thay thế | Ctrl+F hoặc 🔍 → tìm, thay thế text |
| 📢 SEO Generator | Tạo tiêu đề, mô tả, hashtag, tags từ nội dung dịch |
| 📥 Auto Download | Tự tải file .srt sau khi xong |
| 📁 Multi-file Queue | Kéo nhiều file — tự dịch lần lượt |
| 🎨 4 Themes | Dark / Light / Gray / Sepia |
| 💾 Auto Save | Lưu tiến trình + cache SEO, resume khi mở lại |
| 🔑 Key Rotation | Nhiều key xoay vòng tự động |

## 📢 SEO Generator (MỚI v3)

Tab **📢 SEO** tạo nội dung SEO từ phụ đề đã dịch:

| Output | Chi tiết |
|--------|---------|
| 🏷️ 5 Tiêu đề SEO | Công thức: Vấn đề+Đối tượng+Ngòi nổ, 50-70 ký tự |
| 📝 Mô tả ngắn | ≤150 ký tự, hook cảm xúc |
| 📄 Mô tả dài | 150-300 từ, 3 đoạn: Hook+SEO+CTA |
| # Hashtags | 15-20 hashtag trending+ngách |
| 🏷️ Tags | 20-30 YouTube tags |

- **Nền tảng**: YouTube / TikTok / Facebook / Bilibili
- **Thể loại**: Anime / Phim Hàn / Manhwa / Game / Nhạc / Custom
- **🔄 Tạo lại từng section** (tiết kiệm token)
- **📋 Copy All** — 1 nút copy tất cả
- **📊 Đếm ký tự** tiêu đề real-time (xanh/vàng/đỏ)
- **💾 Cache** theo filename — mở lại file = tự load SEO cũ
- **📥 Tải .txt** với header chi tiết (ngày, platform, ngôn ngữ)

## ⚙️ Cấu hình

### Trong Settings (⚙️):
- **Provider + Model**: chọn từ dropdown
- **API Keys**: nhập 1 key/dòng (nhiều key = xoay vòng + song song)
- **☑️ Tự động tải file**: bật/tắt auto-download
- **☑️ Tự động kiểm tra lỗi**: bật/tắt auto-QA
- **☑️ Tự động tạo SEO**: bật/tắt auto-SEO sau dịch

### Backend (config.py):
```python
GEMINI_KEYS_PAID = ["YOUR_GEMINI_API_KEY_HERE"]
```

## 📋 Cấu trúc

```
SRT-Translator/
├── index.html          ← Giao diện (mở bằng Chrome)
├── INSTALL.bat          ← 1-click cài đặt
├── start.bat           ← Khởi động backend
├── config.py           ← ⚠️ API KEY TẠI ĐÂY
├── server.py / translator.py / launcher.py
├── requirements.txt / setup.bat / stop.bat
└── watch_input/ watch_output/ watch_done/
```

## 🔑 Lấy API Key

| Provider | Link | Ghi chú |
|----------|------|---------|
| Gemini | [aistudio.google.com](https://aistudio.google.com/) | Free/Paid |
| Groq | [console.groq.com/keys](https://console.groq.com/keys) | Free, nhanh |
| OpenAI | [platform.openai.com](https://platform.openai.com/api-keys) | Trả phí |
| DeepSeek | [platform.deepseek.com](https://platform.deepseek.com/) | Rẻ |

## 📝 Changelog

- 💾 **Save on all exits** — lưu tiến trình mọi trường hợp (lỗi, dừng, retry)
- ⌨️ **ESC to close dialogs** — phím tắt đóng dialog, quay về xem UI
- 🐛 **14 bugs fixed** — processBatch, retryFailed, partial-success, batchSz fallback
- 📊 **Detailed log** — hiện batch size, max_tokens, parallel count khi dịch

### v3 (2025-03-14)
- 📢 **SEO Generator** — tab mới tạo tiêu đề, mô tả, hashtag, tags
- 🧠 **YouTube Knowledge Base** — kiến thức SEO chuyên sâu nhúng sẵn
- 🔄 **Per-section regenerate** — tạo lại từng mục riêng (~1K tokens)
- 📋 **Copy All** — 1 nút copy toàn bộ SEO
- 📊 **Character counter** — đếm ký tự tiêu đề real-time
- 💾 **SEO Cache** — lưu/tải SEO theo filename
- 🔎 **Tìm kiếm & Thay thế** — Ctrl+F, 🔍 icon, Find/Replace/Replace All
- ⚠️ **QA Warnings** — log ID dòng lỗi, cảnh báo khi QA tắt
- 🛡️ **Strict QA** — confirm dialog trước download nếu >5 lỗi
- 🧠 **Smart QA** — gửi GỐC+DỊCH để AI so sánh (không chỉ dịch lại)
- 🌐 **+3 charset** — detect Cyrillic, Arabic, Hindi
- ⚡ **max_tokens tự scale** — tiết kiệm token cho batch nhỏ
- 📝 **Per-batch log** — hiện chi tiết từng batch khi dịch

### v2
- ⚡ Dịch song song 2-6 batch
- 🎨 4 giao diện (Dark/Light/Gray/Sepia)
- ⚙️ UI gọn, auto-download/QA checkbox
- 🌐 Enforce target language

---
Made with ❤️ by AI-powered SRT Translator

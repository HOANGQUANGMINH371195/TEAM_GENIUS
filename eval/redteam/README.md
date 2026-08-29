# Promptfoo red-team suite

Đây là bộ kiểm tra offline cho prompt injection, raw chunk và rò rỉ định danh
nội bộ. Nó không được import vào `src/` và không chạy trong request path.

Chạy thủ công từ root repository:

```bash
npx --yes promptfoo@latest eval -c eval/promptfoo.yaml
```

Khi đánh giá adapter production-like, thay provider trong file bằng adapter đã
được cấu hình cùng `MODEL_NAME`, prompt version và release manifest; không đưa
secret hoặc toàn bộ corpus vào artifact kết quả.

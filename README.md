# 🏥 MediPay Agent - AI Agent Trợ Lý Hành Chính Y Tế (Bảo Hiểm & Thanh Toán Viện Phí)

> **MediPay Agent** là hệ thống AI Agent thông minh hỗ trợ giải đáp tự động các thắc mắc về Bảo hiểm Y tế (BHYT), bóc tách & giải thích bảng kê chi phí viện phí từ hóa đơn/ảnh chụp, và hướng dẫn quy trình thanh toán cho bệnh nhân tại bệnh viện.

Dự án được xây dựng trong khuôn khổ chương trình **VinUni AI20K Build Phase**.

---

## 📌 Quản Lý Dự Án & Phân Công Công Việc
* **File Quản lý & Chia task dự án (Google Sheets):** [Hồ sơ Quản lý Dự án & Task Assignment](https://docs.google.com/spreadsheets/d/1qphS2v9V9ZyYJYhvxMEaE-YKcfJQumshvWwGgFvyFE0/edit?usp=sharing)
---

## 👥 Thành Viên Nhóm & Phân Vai (Team Vin-Genius)

| Họ và tên | Vai trò chính | Trách nhiệm chính | Tech Stack phụ trách |
| :--- | :--- | :--- | :--- |
| **HOÀNG QUANG MINH** | **Team Lead / DevOps** | Quản lý dự án, Hạ tầng Docker, Vercel CI/CD & Security | Docker, Vercel, GitHub Actions |
| **LÝ MINH HẢI** | **FullStack (Backend)** | RESTful API FastAPI, Supabase/Qdrant retrieval & Caching | Python, FastAPI, PostgreSQL, Qdrant |
| **TRẦN QUỐC HÙNG** | **FullStack (Frontend)** | Giao diện Next.js, UI/UX Mobile, Admin Dashboard & Export PDF | Next.js, Tailwind CSS, TypeScript |
| **NGUYỄN TIẾN DŨNG** | **Kỹ sư AI** | RAG Pipeline, LangGraph Agent, Module OCR & Langfuse | LangChain, LangGraph, Langfuse, OCR |

---

## 🛠️ Tech Stack & Kiến Trúc Hệ Thống

* **Frontend:** Next.js 14, Tailwind CSS, TypeScript (Triển khai trên **Vercel**)
* **Backend:** Python 3.11, FastAPI, Pydantic, Uvicorn (Đóng gói **Docker Container**)
* **Database & Vector Search:** Supabase PostgreSQL cho dữ liệu chuẩn/lexical; Qdrant cho vector semantic
* **AI & Agentic Framework:**
  * **LangChain & LangGraph:** Điều phối luồng Agent, định tuyến ý định (Intent Routing) & State Management.
  * **Langfuse:** Giám sát chất lượng phản hồi AI, theo dõi độ trễ (Latency) & quản lý chi phí token.
* **DevOps & Infrastructure:** Docker Multi-stage, GitHub Actions CI/CD.

### Cấu trúc dự án hiện tại và hướng phát triển

Backend và GraphRAG nằm trong `src`. Supabase quản lý document/chunk, lexical search và PageIndex; Qdrant giữ vector semantic theo collection versioned + alias `medical_legal_active`; Neo4j chỉ navigation graph. Embedding dùng `text-embedding-3-small` (1536 chiều).

```text
.
├── src/                              # FastAPI backend và application logic
│   ├── main.py                       # FastAPI app, lifespan, CORS
│   ├── config.py                     # Supabase DB + GraphRAG settings
│   ├── api/                          # REST routes và dependencies
│   ├── agents/                       # LangGraph state, graph, nodes, tools
│   ├── db/                           # SQLAlchemy session, models, repositories
│   ├── graph_rag/                    # chunking, extraction, retrieval, ingestion
│   ├── integrations/                 # LLM/embedding interfaces, telemetry
│   ├── models/                       # API và graph schemas
│   └── services/                     # chat và GraphRAG use cases
├── web/                              # Next.js frontend (giai đoạn tiếp theo)
│   ├── app/                          # App Router pages/layout
│   ├── components/                   # Chat/document/shared UI
│   └── lib/                          # Typed API client, env helpers
├── database/                         # PostgreSQL, pipeline, Neo4j, Qdrant và Firebase
│   ├── neo4j/                         # Knowledge graph và importer
│   └── firebase/                      # Firebase Authentication scaffold
├── docker-compose.yml                # Chạy backend, kết nối Supabase
├── Dockerfile                        # FastAPI image
├── requirements.txt                  # Python dependencies
└── ARCHITECTURE.md                   # Chi tiết kiến trúc
```

Xem [ARCHITECTURE.md](ARCHITECTURE.md) để biết GraphRAG flow, data model và ranh giới module.

---

## API và Swagger

Chạy backend ở thư mục gốc:

```bash
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

FastAPI tự cung cấp giao diện Swagger tại [http://localhost:8000/docs](http://localhost:8000/docs)

| Method | Path | Mô tả |
| :--- | :--- | :--- |
| `GET` | `/health` | Kiểm tra process API đang chạy. |
| `GET` | `/ready` | Kiểm tra API và kết nối database. |
| `GET` | `/api/v1/status` | Kiểm tra trạng thái LangGraph agent. |
| `POST` | `/api/v1/chat` | Gửi câu hỏi và nhận phản hồi agent. |
| `POST` | `/api/v1/analyze` | Phân tích nội dung mà không trả conversational response. |

`/api/v1/chat` và `/api/v1/analyze` nhận JSON với `message` dài từ 1 đến 5000 ký tự:

```json
{
  "message": "Quyền lợi BHYT khi khám trái tuyến là gì?"
}
```

Ví dụ response chat:

```json
{
  "response": "...",
  "analysis": "..."
}
```

Ví dụ response analyze:

```json
{
  "analysis": "..."
}
```

Chạy kiểm tra:

```bash
ruff check src/ tests/
pytest tests/ -v --tb=short
```

`/ready` chỉ trả `ready` khi active Supabase release, Qdrant alias/parity và Neo4j đều sẵn sàng. Không commit `.env`; copy `.env.example` và điền secrets riêng.

---

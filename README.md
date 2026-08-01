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
| **LÝ MINH HẢI** | **FullStack (Backend)** | RESTful API FastAPI, Database PostgreSQL/pgvector & Caching | Python, FastAPI, PostgreSQL, pgvector |
| **TRẦN QUỐC HÙNG** | **FullStack (Frontend)** | Giao diện Next.js, UI/UX Mobile, Admin Dashboard & Export PDF | Next.js, Tailwind CSS, TypeScript |
| **NGUYỄN TIẾN DŨNG** | **Kỹ sư AI** | RAG Pipeline, LangGraph Agent, Module OCR & Langfuse | LangChain, LangGraph, Langfuse, OCR |

---

## 🛠️ Tech Stack & Kiến Trúc Hệ Thống

* **Frontend:** Next.js 14, Tailwind CSS, TypeScript (Triển khai trên **Vercel**)
* **Backend:** Python 3.11, FastAPI, Pydantic, Uvicorn (Đóng gói **Docker Container**)
* **Database & Vector Search:** PostgreSQL với extension `pgvector`
* **AI & Agentic Framework:**
  * **LangChain & LangGraph:** Điều phối luồng Agent, định tuyến ý định (Intent Routing) & State Management.
  * **Langfuse:** Giám sát chất lượng phản hồi AI, theo dõi độ trễ (Latency) & quản lý chi phí token.
* **DevOps & Infrastructure:** Docker Multi-stage, GitHub Actions CI/CD.

---



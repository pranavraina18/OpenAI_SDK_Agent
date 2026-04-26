---

# 📊 LLM-Powered Data Analysis Agent with SQL Execution & Visualization

## Overview
This project implements an AI-powered agent that converts natural language queries into SQL statements and executes them on a database.

It enables users to interact with structured data using plain English, eliminating the need to write SQL manually.

---

## Problem
Querying databases requires knowledge of SQL, which can be a barrier for non-technical users.

---

## Solution
This project uses an LLM-based agent to:
- understand user queries in natural language
- generate SQL queries dynamically
- execute them on a database
- return structured results

---

## Architecture

User Query
   ↓
LLM Agent (Natural Language Understanding)
   ↓
SQL Query Generation (Schema-aware)
   ↓
Database Execution (MySQL)
   ↓
Data Processing (Pandas)
   ↓
Visualization Engine (Matplotlib)
   ↓
Response Generation (Insights + Charts)
   ↓
Conversation Memory Update

---
## 💡 Key Highlights

- End-to-end AI agent capable of data querying, analysis, and visualization  
- Maintains conversational context for multi-step analytical queries  
- Automatically generates insights and visualizations from raw data  
- Implements security filtering to prevent exposure of sensitive data  
- Includes background processes for memory and resource management
  
---
## 🚀 Features

* 🔍 **Natural Language to SQL**: Converts user queries into optimized MySQL-compatible SQL queries using LLMs.
* 📈 **Smart Visualizations**: Automatically generates charts (bar, line, pie, etc.) based on data trends or comparisons.
* 🧠 **Conversational Memory**: Maintains context across interactions for deeper, ongoing analysis.
* 🔒 **Sensitive Data Protection**: Filters out sensitive fields like emails, phone numbers, and addresses.
* 🔄 **Background Cleanup**: Periodic cleanup of stale memory and temporary chart files.
* ⚙️ **FastAPI Integration**: Exposes a clean, async API endpoint for real-time analysis requests.

---

## 🧰 Requirements

* Python 3.10+
* MySQL database
* OpenAI API key

### Key Dependencies

* `FastAPI`
* `pandas`
* `mysql-connector-python`
* `python-dotenv`
* `uvicorn`
* `matplotlib`
* `opencv-python`
* `openai-agents`


---

## ⚙️ Environment Variables

Create a `.env` file in your root directory with the following keys:

```env
AOL_HOST=your_mysql_host
AOL_USER=your_mysql_user
AOL_PASSWORD=your_mysql_password
aol_database=your_database_name
aol_table=your_table_name
OPENAI_API_KEY=your_openai_api_key
```

---

## ▶️ How to Run

```bash
# 1. Clone repo
git clone https://github.com/pranavraina18/OpenAI_SQL_AI_Agent

# Install dependencies
pip install -r requirements.txt

# Run the FastAPI server
python api_data_analysis_agent.py
```

API will be available at `http://localhost:8000/analyze/`.

---

## 📬 API Usage

### POST `/analyze/`

**Request Body:**

```json
{
  "user_request": "Show the average revenue per month this year",
  "database": "aol",
  "table_name": "your_table_name",
  "conversation_id": "session_123"
}
```

**Response:**

* Insightful summary
* Path to generated chart (if applicable)
* Suggestions for deeper analysis

---

## 📉 Chart Output

All charts are saved as `.png` in the `charts/` folder with absolute paths returned in the response.

---

## 🧹 Cleanup Logic

* Conversations and charts are cleaned daily.
* Background task checks every minute using asyncio.

---

## 📦 Deployment

For production, use:

```bash
uvicorn api_data_analysis_agent:app --host 0.0.0.0 --port 8000 --workers 4
```

---

## 🛡️ Security Notes

* SQL injection is mitigated via controlled query generation.
* Sensitive columns (email, contact info) are never queried or displayed.

---

## Author
Pranav Raina

---
## License
MIT License — feel free to use, improve, and share.

---

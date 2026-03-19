# 🧠 Advanced AI-Powered Record and Playback Automation Framework

## 🔧 Overview

This project implements a comprehensive automation framework that enables:
- Recording user interactions via browser automation tools with a full-featured GUI
- Managing recorded flows through intuitive UI controls (view, edit, rename, delete)
- Replaying those interactions automatically across multiple environments
- Generating synthetic test users/data using an integrated AI model
- Supporting functional, regression, performance, and cross-browser testing
- Integrating seamlessly into CI/CD pipelines for continuous testing
- Configurable test data standards and URL settings

## ✅ Technologies Used

| Component         | Technology         |
|-------------------|--------------------|
| Backend API       | FastAPI            |
| Frontend UI       | React/Vue.js       |
| Browser Automation| Playwright         |
| AI Model          | Phi3 / Custom LLM  |
| Data Generation   | Fake Customer Generator |
| Reporting         | HTML Reports, Logs |
| Cross-Browser     | Selenium Grid or Playwright Browsers |

## 📁 Project Structure

```
automation_framework/
│
├── backend/
│   ├── main.py                 # FastAPI app entry point
│   ├── routers/                # API routes
│   │   ├── recording.py        # For recording/replaying sessions
│   │   ├── flow_management.py  # CRUD operations for recorded flows
│   │   ├── ai_service.py       # AI model integration
│   │   └── config_service.py   # Configuration management
│   ├── services/               # Business logic
│   │   ├── player_service.py   # Replay logic handler
│   │   ├── recorder_service.py # Recording logic handler
│   │   ├── flow_service.py     # Flow management service
│   │   ├── llm_service.py      # Interface to LLM for generating test data
│   │   └── config_service.py   # Configuration service
│   └── models/                 # Pydantic models for request/response
│
├── frontend/
│   ├── components/             # UI components for flow management
│   ├── pages/                  # Page components
│   └── assets/                 # Static assets
│
├── recordings/                 # Folder storing recorded session files (.json)
│
├── reports/                    # Generated test reports and logs
│
├── model/                      # Local Phi3 or custom LLM for synthetic data
│   └── phi3_model.bin          # Or other formats depending on deployment
│
├── config/                     # Configuration files
│   └── configuration.properties # Standard configuration file
│
├── tests/                      # Test scripts organized by feature/module
│   ├── functional_tests/
│   ├── regression_tests/
│   ├── edge_case_tests/
│   └── data_driven_tests/
│
├── utils/                      # Utility functions (e.g., logger, config parser)
│
└── requirements.txt            # Python dependencies
```

## 🚀 Getting Started

### Prerequisites

- Python 3.8+
- Node.js (for frontend)
- Playwright browsers

### Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd automation_framework
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Install Playwright browsers:
```bash
python -m playwright install
```

4. Start the backend server:
```bash
python backend/main.py
```

5. The API will be available at `http://localhost:8000`

## 📊 API Documentation

Once the server is running, visit `http://localhost:8000/docs` for interactive API documentation.

## 🧪 Running Tests

To run the test suite:
```bash
pytest tests/
```

## 🐳 Deployment

The application can be containerized using the provided Dockerfile:

```dockerfile
# Dockerfile for backend service
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## 🛡️ Security Considerations

- Encrypted storage of sensitive recorded data
- Secure credential management for playback
- Role-based access control for flow management
- Audit logging for all flow operations

## 📈 Performance Optimization

- Asynchronous recording and playback operations
- Database indexing for fast flow retrieval
- Caching mechanisms for frequently accessed flows
- Load balancing for concurrent operations

## 📊 Reporting and Analytics

- Step-by-step execution timeline
- Success/failure metrics per flow
- Performance statistics and trends
- Comparative analysis between runs
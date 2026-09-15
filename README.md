# Workday MCP POC

This repository contains an ADK Agent (`adk_agent.py`) and a GitHub MCP Server (`github_server.py`). 

## Prerequisites

Python 3.10+ is recommended.

## Setup Instructions

1. **Create a virtual environment:**

```bash
python3 -m venv venv
```

2. **Activate the virtual environment:**

- On macOS and Linux:
```bash
source venv/bin/activate
```
- On Windows:
```bash
venv\Scripts\activate
```

3. **Install the required dependencies:**

```bash
pip install -r requirements.txt
```

## Running the Application

You will need to run the MCP server and the ADK agent in separate terminal windows. Make sure your virtual environment is active in both terminals.

### 1. Start the GitHub MCP Server

This server handles GitHub authentication and provides MCP tools.

```bash
python github_server.py
```

### 2. Start the ADK Agent

In a new terminal (with the `venv` activated), run the ADK agent:

```bash
python adk_agent.py
```

Follow the prompts in the terminal to authenticate and interact with the agent.

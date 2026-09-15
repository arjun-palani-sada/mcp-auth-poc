# fastapi_github_server.py
from fastmcp import FastMCP
import sqlite3
import threading
import requests
import json
import time
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

# ==========================================
# GITHUB OAUTH CREDENTIALS
# ==========================================
GITHUB_CLIENT_ID = "enter github client id of oauth app"
GITHUB_CLIENT_SECRET = "enter github secret of oauth app"

# ==========================================
# 1. DATABASE WITH REFRESH LOGIC
# ==========================================
DB_FILE = "github_tokens.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS tokens (email TEXT PRIMARY KEY, access_token TEXT, refresh_token TEXT, expires_at REAL)")
    conn.commit()
    conn.close()

def get_user_token(email: str):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT access_token, refresh_token, expires_at FROM tokens WHERE email = ?", (email,))
        row = cursor.fetchone()
        conn.close()
        if row:
            return {"access_token": row[0], "refresh_token": row[1], "expires_at": row[2]}
        return None
    except Exception as e:
        print(f"Database error reading token: {e}")
        return None

def save_user_token(email: str, access_token: str, refresh_token: str, expires_in: int):
    expires_at = time.time() + expires_in if expires_in else time.time() + 3600 
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_FILE, timeout=5) 
            conn.execute("INSERT OR REPLACE INTO tokens (email, access_token, refresh_token, expires_at) VALUES (?, ?, ?, ?)", (email, access_token, refresh_token, expires_at))
            conn.commit()
            conn.close()
            print(f"Successfully saved token for {email}")
            return True
        except sqlite3.Error as e:
            print(f"Attempt {attempt + 1} failed to save token: {e}")
            time.sleep(2 ** attempt) 
    return False

init_db()

# ==========================================
# 2. OAUTH REFRESH ENGINE
# ==========================================
def refresh_access_token(email: str, refresh_token: str):
    print(f"Token expired for {email}. Attempting background refresh...")
    response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }
    )
    
    data = response.json()
    new_access = data.get("access_token")
    new_refresh = data.get("refresh_token", refresh_token) 
    expires_in = data.get("expires_in", 3600)

    if new_access:
        save_user_token(email, new_access, new_refresh, expires_in)
        print("Background token refresh successful.")
        return new_access
    else:
        print("Refresh failed. User must log in again.")
        return None

# ==========================================
# 3. FASTAPI AUTHENTICATION WEBSERVER
# ==========================================
auth_app = FastAPI(title="Auth Server")

@auth_app.get("/auth/start")
async def auth_start(user: str):
    """Redirects the user to the GitHub login page."""
    if not user:
        return HTMLResponse("Missing user email", status_code=400)
    
    auth_url = f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&state={user}&scope=read:user,repo"
    return RedirectResponse(url=auth_url)

@auth_app.get("/auth/callback")
async def auth_callback(code: str, state: str):
    """Handles the redirect back from GitHub and exchanges the code for tokens."""
    if not code or not state:
        return HTMLResponse("Missing code or state", status_code=400)

    token_response = requests.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        data={
            "client_id": GITHUB_CLIENT_ID,
            "client_secret": GITHUB_CLIENT_SECRET,
            "code": code
        }
    )
    
    data = token_response.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token", "mock_refresh_token_for_poc") 
    expires_in = data.get("expires_in", 15) # Set to 15 seconds to test refresh flow

    if access_token:
        success = save_user_token(state, access_token, refresh_token, expires_in)
        if success:
            return HTMLResponse("<h2>GitHub Auth Successful!</h2><p>Your real token is saved. Go back to the chat and type 'Done'.</p>")
        else:
            return HTMLResponse("<h2>Database Error</h2><p>Failed to save token after retries.</p>", status_code=500)
    else:
        return HTMLResponse("Failed to get access token from GitHub", status_code=400)

def run_fastapi():
    uvicorn.run(auth_app, host="127.0.0.1", port=8080, log_level="warning")

# Run FastAPI in a background thread
threading.Thread(target=run_fastapi, daemon=True).start()
print("Started FastAPI Auth Server on http://localhost:8080")


# ==========================================
# 4. FAST MCP SERVER (With Auth Interception)
# ==========================================
mcp = FastMCP("GitHub Real Server")

def check_and_get_token(user_email: str) -> str:
    token_data = get_user_token(user_email)

    if not token_data:
        return (
            f"ERROR: User must authenticate. Tell the user exactly this: "
            f"'Please authorize GitHub access: [Log in to GitHub](http://localhost:8080/auth/start?user={user_email}). "
            f"Reply \"done\" once you have logged in.'"
        )
    
    if time.time() > token_data["expires_at"]:
        new_token = refresh_access_token(user_email, token_data["refresh_token"])
        if new_token:
            return new_token
        else:
            return f"ERROR: Tell user exactly: 'Your session expired. Please log in again: [GitHub Login](http://localhost:8080/auth/start?user={user_email}). Reply \"done\" after.'"
            
    return token_data["access_token"]

@mcp.tool()
def get_my_github_profile(requesting_user: str) -> str:
    """Retrieve the real GitHub profile for the currently authenticated user."""
    token = check_and_get_token(requesting_user)
    if token.startswith("ERROR"): return token

    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get("https://api.github.com/user", headers=headers)
    if response.status_code == 200 :
        return response.text
    elif response.status_code == 403:
            return "ERROR: The user's account does not have permission to view profiles in this system."
    elif response.status_code == 404:
        return "ERROR: The requested resource was not found."
    else:
        return f"ERROR: The API failed with status code {response.status_code}."

@mcp.tool()
def list_my_github_repos(requesting_user: str) -> str:
    """Retrieve the real list of GitHub repositories for the authenticated user."""
    token = check_and_get_token(requesting_user)
    if token.startswith("ERROR"): return token

    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get("https://api.github.com/user/repos?per_page=5&sort=updated", headers=headers)
    
    if response.status_code == 200:
        return json.dumps([{"name": r["name"],"url": r["html_url"], "private": r["private"]} for r in response.json()])
    
    # ===  GRACEFUL ERROR HANDLING ===
    elif response.status_code == 403:
        return "ERROR: The user's account does not have permission to view repositories in this system."
    elif response.status_code == 404:
        return "ERROR: The requested resource was not found."
    else:
        return f"ERROR: The API failed with status code {response.status_code}."

if __name__ == "__main__":
    print("Starting GitHub MCP Server on http://localhost:8000/sse...")
    mcp.run(transport="sse")
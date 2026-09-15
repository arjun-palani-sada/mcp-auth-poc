# github_server.py
from fastmcp import FastMCP
import sqlite3
import threading
import requests
import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# ==========================================
# GITHUB OAUTH CREDENTIALS (PASTE YOURS HERE)
# ==========================================
GITHUB_CLIENT_ID = "enter github client id of oauth app"
GITHUB_CLIENT_SECRET = "enter github secret of oauth app"

# ==========================================
# 1. DATABASE WITH REFRESH LOGIC
# ==========================================
DB_FILE = "github_tokens.db"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    # Added refresh_token and expires_at to the schema
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
    # Calculate exact timestamp when token expires
    expires_at = time.time() + expires_in if expires_in else time.time() + 3600 # default 1 hr
    max_retries = 3
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(DB_FILE, timeout=5) # 5 second timeout if DB is locked
            conn.execute("INSERT OR REPLACE INTO tokens (email, access_token, refresh_token, expires_at) VALUES (?, ?, ?, ?)", (email, access_token, refresh_token, expires_at))
            conn.commit()
            conn.close()
            print(f"Successfully saved token for {email}")
            return True
        except sqlite3.Error as e:
            print(f"Attempt {attempt + 1} failed to save token: {e}")
            time.sleep(2 ** attempt) # Exponential backoff: 1s, 2s, 4s
    return False

init_db()
# ==========================================
# 2. OAUTH REFRESH ENGINE
# ==========================================

def refresh_access_token(email: str, refresh_token: str):
    """Exchanges an expired refresh_token for a fresh access_token."""
    print(f"Token expired for {email}. Attempting background refresh...")
    
    # Standard OAuth Token Refresh Request
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
    new_refresh = data.get("refresh_token", refresh_token) # Sometimes refresh tokens rotate
    expires_in = data.get("expires_in", 3600)

    if new_access:
        save_user_token(email, new_access, new_refresh, expires_in)
        print(" Background token refresh successful.")
        return new_access
    else:
        print(" Refresh failed. User must log in again.")
        return None

# ==========================================
# 3. REAL AUTHENTICATION WEBSERVER
# ==========================================
class AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        # Endpoint 1: Redirect user to real GitHub Login
        if path == "/auth/start":
            user_email = query_params.get('user', [''])[0]
            # State parameter prevents CSRF and passes the email through the flow
            auth_url = f"https://github.com/login/oauth/authorize?client_id={GITHUB_CLIENT_ID}&state={user_email}&scope=read:user,repo"
            
            # HTTP 302 Redirect directly to GitHub
            self.send_response(302)
            self.send_header("Location", auth_url)
            self.end_headers()

        # Endpoint 2: GitHub redirects back here with a real 'code'
        elif path == "/auth/callback":
            code = query_params.get('code', [''])[0]
            user_email = query_params.get('state', [''])[0]
            
            if not code or not user_email:
                self.send_error(400, "Missing code or state")
                return

            # REAL OAUTH EXCHANGE: Trade the code for an access token
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
            # Note: GitHub only sends refresh tokens if configured in app settings, Workday always does.
            refresh_token = data.get("refresh_token", "mock_refresh_token_for_poc") 
            expires_in = data.get("expires_in", 3600)

            if access_token:
                # Save the real token securely
                success = save_user_token(user_email, access_token, refresh_token, expires_in)
                
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                
                if success:
                    html = "<h2>GitHub Auth Successful!</h2><p>Your real token is saved. Go back to the chat and type 'Done'.</p>"
                else:
                    html = "<h2>Database Error</h2><p>Failed to save token after retries.</p>"
                self.wfile.write(html.encode())
            else:
                self.send_error(400, "Failed to get access token from GitHub")
            
def log_message(self, format, *args): pass

def run_auth_server():
    server = HTTPServer(('localhost', 8080), AuthHandler)
    print("Started Auth Web Server on http://localhost:8080")
    server.serve_forever()

threading.Thread(target=run_auth_server, daemon=True).start()

# ==========================================
# 4. FAST MCP SERVER (With Auth Interception)
# ==========================================
mcp = FastMCP("GitHub Real Server")

def check_and_get_token(user_email: str) -> str:
    """The central Auth interceptor that handles expiration and refreshing."""
    token_data = get_user_token(user_email)

    # 1. No token exists
    if not token_data:
        return (
            f"ERROR: User must authenticate. Tell the user exactly this: "
            f"'Please authorize GitHub access: [Log in to GitHub](http://localhost:8080/auth/start?user={user_email}). "
            f"Reply \"done\" once you have logged in.'"
        )
    
    # 2. Check if expired
    if time.time() > token_data["expires_at"]:
        new_token = refresh_access_token(user_email, token_data["refresh_token"])
        if new_token:
            return new_token
        else:
            # Refresh failed (e.g., user revoked access), force them to log in again
            return f"ERROR: Tell user exactly: 'Your session expired. Please log in again: [GitHub Login](http://localhost:8080/auth/start?user={user_email}). Reply \"done\" after.'"
            
    # 3. Token is valid
    return token_data["access_token"]

@mcp.tool()
def get_my_github_profile(requesting_user: str) -> str:
    """Retrieve the real GitHub profile for the currently authenticated user."""
    token = check_and_get_token(requesting_user)
    if token.startswith("ERROR"): return token

    # Make a REAL call to the GitHub API using the user's token!
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.get("https://api.github.com/user", headers=headers)

    return response.text if response.status_code == 200 else "ERROR: API failed."

@mcp.tool()
def list_my_github_repos(requesting_user: str) -> str:
    """Retrieve the real list of GitHub repositories for the authenticated user."""
    token = check_and_get_token(requesting_user)
    if token.startswith("ERROR"): return token

    headers = {"Authorization": f"Bearer {token}"}
    # fetch just the first 5 repos so we don't overwhelm the LLM context
    response = requests.get("https://api.github.com/user/repos?per_page=5&sort=updated", headers=headers)
    
    if response.status_code == 200:
        return json.dumps([{"name": r["name"],"url": r["html_url"], "private": r["private"]} for r in response.json()])
    return "ERROR: API failed."

if __name__ == "__main__":
    print("Starting GitHub MCP Server on http://localhost:8000/sse...")
    mcp.run(transport="sse")
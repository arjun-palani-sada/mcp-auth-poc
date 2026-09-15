# workday_server.py
from fastmcp import FastMCP
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

# ==========================================
# 1. DATABASE SETUP (Simulating GCP Firestore)
# ==========================================
DB_FILE = "tokens.db"

def init_db():
    """Create the database table if it doesn't exist."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("CREATE TABLE IF NOT EXISTS tokens(email TEXT PRIMARY KEY, token TEXT)")
    conn.commit()
    conn.close()

def get_user_token(email: str):
    """ Fetch a user's token from the database"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT token from tokens WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def save_user_token(email: str, token: str):
    """Save or update a user's token in the database"""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("INSERT OR REPLACE INTO tokens (email,token) values (?,?)", (email,token))
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 2. AUTHENTICATION WEBSERVER (Simulating the OAuth Endpoints)
# ==========================================

class AuthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query_params = parse_qs(parsed_url.query)

        # Endpoint 1: The user clicks the link in the chat
        if path == "/auth/start":
            user_email = query_params.get('user', [''])[0]
            if not user_email:
                self.send_error(400, "Missing user email")
                return
            
            # In production, this redirects to Workday. Here, we render a fake login page.
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            html = f"""
            <html><body style="font-family: Arial; padding: 40px;">
                <h2>Simulated Workday Login</h2>
                <p>Logging in as: <b>{user_email}</b></p>
                <form action="/auth/callback" method="GET">
                    <input type="hidden" name="user" value="{user_email}">
                    <button type="submit" style="padding: 10px 20px; background: blue; color: white;">Authorize Workday Access</button>
                </form>
            </body></html>
            """
            self.wfile.write(html.encode())

        # Endpoint 2: Workday redirects back here after login
        elif path == "/auth/callback":
            user_email = query_params.get('user', [''])[0]
            
            # In production, you exchange an OAuth 'code' for a token here.
            # For the PoC, we just generate a fake token and save it to the DB!
            mock_access_token = f"wd_token_for_{user_email.split('@')[0]}"
            save_user_token(user_email, mock_access_token)

            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            html = """
            <html><body style="font-family: Arial; padding: 40px; color: green;">
                <h2>Authentication Successful!</h2>
                <p>Your token has been securely saved to the database.</p>
                <p><b>You may close this tab, return to the chat, and type "Done".</b></p>
            </body></html>
            """
            self.wfile.write(html.encode())
        else:
            self.send_error(404, "Not Found")

    # Suppress default HTTP logging to keep the terminal clean
    def log_message(self, format, *args):
        pass

def run_auth_server():
    server = HTTPServer(('localhost', 8080), AuthHandler)
    print("Started Auth Web Server on http://localhost:8080")
    server.serve_forever()

# Start the Auth server in a background thread
threading.Thread(target=run_auth_server, daemon=True).start()


# ==========================================
# 3. FAST MCP SERVER (The Agent Interface)
# ==========================================
mcp = FastMCP("Workday Mock Server")

# Static Mock Database
WORKDAY_DB = {
    "EMP101": {
        "name": "Alice Smith",
        "title": "Senior Software Engineer",
        "department": "Engineering",
        "vacation_days": 15,
        "sick_days": 5
    },
    "EMP102": {
        "name": "Bob Jones",
        "title": "Product Manager",
        "department": "Product",
        "vacation_days": 8,
        "sick_days": 10
    }
}


def check_auth(user_email: str) -> str:
    """Checks if the user has a valid token. If not, returns the Markdown link"""
    token = get_user_token(user_email)
    # Now we check the real SQLite database!
    if not token:
        return (
            f"ERROR: User must authenticate. Tell the user exactly this: "
            f"'Please authorize Workday to answer this: [Log in to Workday](http://localhost:8080/auth/start?user={user_email}). "
            f"Reply \"done\" once you have logged in.'"
        )
    return "OK"


@mcp.tool()
def get_employee_profile(employee_id: str, requesting_user:str) -> str:
    """Retrieve basic profile details for a Workday employee by ID."""
    auth_status = check_auth(requesting_user)
    if auth_status != "OK":
        return auth_status # Return the markdown link instruction to Gemini

    employee = WORKDAY_DB.get(employee_id.upper())
    if employee:
        return json.dumps({
            "employee_id": employee_id,
            "name": employee["name"],
            "title": employee["title"],
            "department": employee["department"]
        })
    return json.dumps({"error": f"Employee ID {employee_id} not found."})

@mcp.tool()
def get_time_off_balance(employee_id: str, requesting_user: str) -> str:
    """Retrieve remaining PTO and sick leave balances for an employee by ID."""
    auth_status = check_auth(requesting_user)

    if auth_status != "OK":
        return auth_status # Return the markdown link instruction to Gemini
    
    employee = WORKDAY_DB.get(employee_id.upper())
    if employee:
        return json.dumps({
            "employee_id": employee_id,
            "vacation_days_remaining": employee["vacation_days"],
            "sick_days_remaining": employee["sick_days"]
        })
    return json.dumps({"error": f"Employee ID {employee_id} not found."})

if __name__ == "__main__":
    #Run as a webserver using Server-Sent Events (SSE) on port 8000
    print("Starting Workday MCP Server on http://localhost:8000/sse...")
    mcp.run(transport="sse")
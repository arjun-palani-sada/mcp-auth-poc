# agent.py
import asyncio
from google import genai
from google.genai import types
from mcp import ClientSession
from mcp.client.sse import sse_client

async def run_agent():
    client = genai.Client(
        vertexai=True,
        project="sada-sadaindia-sandbox-ai", 
        location="us-central1"
    )

    url = "http://localhost:8000/sse"

    # ==========================================
    # PRODUCTION IDENTITY LOGIC (Keep for reference)
    # ==========================================
    # In your production Cloud Run ADK Agent, you will extract the email from the 
    # payload sent by onetru (which gets it from the PingFederate X-User-Token).
    #
    # Example Production Code:
    # def handle_request(request):
    #     payload = request.json
    #     current_user_email = payload.get("email") # e.g., "nsindhu@transunion.com"
    # ==========================================
    
    # For this PoC, we will just ask the user who they are at the start:
    print("="*50)
    current_user_email = input("Enter your Corporate Email (e.g. you@company.com): ").strip()
    print(f"Logged in to Agent as: {current_user_email}")

    print(f"Connecting to MCP server at {url}...")

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            mcp_tools = await session.list_tools()
            
            function_declarations = []
            for tool in mcp_tools.tools:
                function_declarations.append(
                    types.FunctionDeclaration(
                        name=tool.name,
                        description=tool.description,
                        parameters=tool.input_schema  
                    )
                )

            tools = [types.Tool(function_declarations=function_declarations)]

            system_prompt = (
                f"You are a helpful Developer Assistant. The logged-in user is {current_user_email}. "
                "Always pass this email to any tool that requires a 'requesting_user' parameter. "
                "If ANY tool returns an authentication error, STOP calling tools immediately. "
                "Present the login link exactly as requested to the user, and wait for them to reply."
            )
            
            chat = client.chats.create(
                model="gemini-2.5-flash",
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    tools=tools,
                    temperature=0.0
                )
            )

            print("\n" + "="*50)
            print("GitHub Agent is ready! Type 'exit' to quit.")
            print("="*50 + "\n")

            while True:
                user_query = input("\nYou: ")
                if user_query.lower() in ['exit', 'quit']:
                    break

                response = chat.send_message(user_query)
                
                while response.function_calls:
                    tool_responses = []
                    
                    for call in response.function_calls:
                        print(f"--> Agent executing MCP tool: {call.name}")
                        
                        tool_result = await session.call_tool(call.name, arguments=call.args)
                        result_content = tool_result.content[0].text

                        tool_responses.append(
                            types.Part.from_function_response(
                                name=call.name,
                                response={"result": result_content}
                            )
                        )
                    
                    response = chat.send_message(tool_responses)

                print(f"\nAgent: {response.text}")

if __name__ == "__main__":
    asyncio.run(run_agent())
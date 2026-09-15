# adk_agent.py
import asyncio
import uuid
import traceback
from google.adk import Agent
from mcp.client.sse import sse_client
from mcp import ClientSession
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.models.google_llm import Gemini
from google.genai import Client, types
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="google.adk.models.llm_request")

async def run_adk_agent():
    print("="*50)
    current_user_email = input("🔑 Enter your Corporate Email: ").strip()
    user_id = current_user_email
    print(f"Logged in to Agent as: {current_user_email}")

    url = "http://localhost:8000/sse"
    print(f"Connecting to MCP server at {url}...")

    async with sse_client(url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            
            # 1. Fetch MCP tools
            mcp_tools = await session.list_tools()
            
            # 2. Bridge MCP Tools to ADK Callable Functions
            def create_adk_tool(mcp_tool_name, mcp_description):
                async def mcp_tool_wrapper(requesting_user: str = current_user_email, **kwargs):
                    print(f"\n  [ADK Engine] Executing MCP tool: {mcp_tool_name} for {requesting_user}")
                    kwargs["requesting_user"] = requesting_user
                    try:
                        result = await session.call_tool(mcp_tool_name, arguments=kwargs)
                        return result.content[0].text
                    except Exception as e:
                        return f"ERROR executing tool: {str(e)}"
                
                mcp_tool_wrapper.__name__ = mcp_tool_name
                mcp_tool_wrapper.__doc__ = mcp_description
                return mcp_tool_wrapper

            adk_tools = [create_adk_tool(t.name, t.description) for t in mcp_tools.tools]

            # Create the explicit Vertex AI client
            genai_client = Client(
                vertexai=True,
                project="sada-sadaindia-sandbox-ai", 
                location="us-central1"
            )

            # Wrap it in ADK's GoogleLLM model
            model_instance = Gemini(
                model="gemini-2.5-flash",
                client=genai_client
            )

            # 3. Define the ADK Agent
            dev_agent = Agent(
                name="GitHub_Assistant",
                model=model_instance,
                tools=adk_tools,
                instruction=(
                    f"You are a helpful Developer Assistant. The logged-in user is {current_user_email}. "
                    "Always pass this email to any tool that requires a 'requesting_user' parameter. "
                    "CRITICAL: If a tool response contains 'ERROR: User must authenticate' or a login link, "
                    "DO NOT call any more tools. Immediately display the exact authentication instructions "
                    "and link to the user and stop."
                )
            )

            # 3.5 Set up the Runner and Session
            session_service = InMemorySessionService()
            runner = Runner(app_name="mcp_bridge_app", agent=dev_agent, session_service=session_service)

            session_id = f"session_{uuid.uuid4().hex[:8]}"
            await session_service.create_session(
                app_name="mcp_bridge_app",
                user_id=user_id,
                session_id=session_id
            )

            print("\n" + "="*50)
            print("🐙 ADK Agent is ready! Type 'exit' to quit.")
            print("="*50 + "\n")

            while True:
                user_query = input("\nYou: ")
                if user_query.lower() in ['exit', 'quit']:
                    break

                user_msg = types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=user_query)]
                )

                print(f"Agent: ", end="")
                
                try:
                    # 4. Consume events cleanly
                    async for event in runner.run_async(
                        user_id=user_id,
                        session_id=session_id,
                        new_message=user_msg 
                    ):
                        # A. Check for top-level text
                        if hasattr(event, "text") and event.text:
                            print(event.text, end="", flush=True)
                        
                        # B. Check for nested message structures
                        elif hasattr(event, "message"):
                            if hasattr(event.message, "parts") and event.message.parts:
                                parts_text = "".join([p.text for p in event.message.parts if hasattr(p, "text") and p.text])
                                if parts_text:
                                    print(parts_text, end="", flush=True)
                            elif hasattr(event.message, "text") and event.message.text:
                                print(event.message.text, end="", flush=True)

                except Exception as e:
                    print(f"\n[CRITICAL RUNNER ERROR]: The ADK loop crashed!")
                    traceback.print_exc()
                            
                print("\n")

if __name__ == "__main__":
    asyncio.run(run_adk_agent())
import sys
import os
import subprocess
import gradio as gr
from loguru import logger
from strands import Agent
from strands.models.anthropic import AnthropicModel
from strands.tools.mcp import MCPClient
from mcp import stdio_client, StdioServerParameters
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider

logger.remove()
logger.add(sys.stdout, serialize=True, level="INFO")

credential = ManagedIdentityCredential()
token_provider = get_bearer_token_provider(
    credential,
    "https://cognitiveservices.azure.com/.default"
)

model = AnthropicModel(
    client_args={
        "base_url": "https://foundry-nakatsukasa1.services.ai.azure.com/anthropic",
        "azure_ad_token_provider": token_provider,
    },
    model_id="claude-haiku-4-5",   # your exact Foundry deployment name
    max_tokens=32768,
)

# --- Linux paths inside container ---
NODE_PATH = "/usr/bin/node"

def _find_mcp_cli():
    npm_root = subprocess.run(
        ["npm", "root", "-g"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return f"{npm_root}/@playwright/mcp/cli.js"

MCP_CLI = _find_mcp_cli()
MCP_ENV = dict(os.environ)
MCP_ENV.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

# --- Persistent Playwright MCP session + Agent ---
playwright_client = MCPClient(
    lambda: stdio_client(
        StdioServerParameters(
            command=NODE_PATH,
            args=[MCP_CLI, "--headless", "--no-sandbox", "--browser", "chromium"],
            env=MCP_ENV,
            stderr=sys.stderr
        )
    )
)

playwright_client.__enter__()
_tools = playwright_client.list_tools_sync()

agent = Agent(
    model=model,
    tools=_tools,
    system_prompt=(
        "You are a web automation assistant using Playwright. "
        "You can browse websites, extract information, click elements, "
        "fill forms, and take screenshots. The browser session persists "
        "across the conversation, so previously opened pages remain available "
        "unless you navigate away from them. "
        "If you encounter an error, report the exact error message."
    )
)

def chat(message, history, request: gr.Request):
    username = request.headers.get("x-ms-client-principal-name", "unknown")
    print("", flush=True)  # ← force newline to separate agent output from our log
    logger.info("chat_request", extra={"username": username, "message": message})
    try:
        response = agent(message)
        return str(response)
    except Exception as e:
        logger.exception("chat_error", extra={"username": username, "error": str(e)})
        return f"An error occurred: {str(e)}"

with gr.Blocks(title="Playwright Web Agent") as demo:
    gr.Markdown("# 🌐 Playwright Web Agent")
    gr.Markdown("Ask me to browse websites, extract information, or automate web tasks.")

    gr.ChatInterface(
        fn=chat,
        examples=[
            "Go to https://example.com and tell me the page title.",
            "Go to https://news.ycombinator.com and list the top 5 stories.",
            "Go to https://github.com/trending and tell me the top trending repos today.",
        ],
    )

demo.launch(server_name="0.0.0.0", server_port=7860)

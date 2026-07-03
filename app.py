import sys
import os
import subprocess
import gradio as gr
from loguru import logger
from strands import Agent
from strands.models.openai import OpenAIModel
from strands.tools.mcp import MCPClient
from mcp import stdio_client, StdioServerParameters
from openai import AsyncAzureOpenAI
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider

logger.remove()
logger.add(sys.stdout, serialize=True, level="INFO")

credential = ManagedIdentityCredential()
token_provider = get_bearer_token_provider(
    credential,
    "https://cognitiveservices.azure.com/.default"
)

azure_client = AsyncAzureOpenAI(
    azure_endpoint="https://foundry-nakatsukasa1.openai.azure.com/",  # for gpt
    #azure_endpoint="https://foundry-nakatsukasa1.services.azure.com/anthropic",   # for claude
    azure_ad_token_provider=token_provider,
    api_version="2024-10-21",   # for gpt-4o-mini
    #api_version="2025-04-01-preview",  
    max_retries=3,
)

model = OpenAIModel(
    client=azure_client,
    model_id="claude-haiku-4-5",  #"gpt-5-mini",  claude-haiku-4-5
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
        "回答は流暢な大阪弁でお願い"
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
    gr.Markdown("ブラウザ操作できます！")

    gr.ChatInterface(
        fn=chat,
        examples=[
            "https://www.yahoo.co.jpから主要なニュースのタイトルをいくつか教えて",
        ],
    )

demo.launch(server_name="0.0.0.0", server_port=7860)

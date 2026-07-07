import sys
import os
import subprocess
import threading
import gradio as gr
from loguru import logger
from strands import Agent
from strands.models.anthropic import AnthropicModel
from strands.tools.mcp import MCPClient
from mcp import stdio_client, StdioServerParameters
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from anthropic import AsyncAnthropicFoundry

# make sure to update requirements.txt for strands_agent.  There are openai and anthropic versions.
logger.remove()
logger.add(sys.stdout, serialize=True, level="INFO")

credential = ManagedIdentityCredential()
token_provider = get_bearer_token_provider(
    credential,
    "https://cognitiveservices.azure.com/.default"
)

def _make_model():
    m = AnthropicModel(
        client_args={"api_key": "placeholder"},
        model_id="claude-haiku-4-5",
        max_tokens=32768,
    )
    m.client = AsyncAnthropicFoundry(
        base_url="https://foundry-nakatsukasa1.services.ai.azure.com/anthropic",
        azure_ad_token_provider=token_provider,
    )
    return m

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

SYSTEM_PROMPT = (
    "You are a web automation assistant using Playwright. "
    "You can browse websites, extract information, click elements, "
    "fill forms, and take screenshots. The browser session persists "
    "across the conversation, so previously opened pages remain available "
    "unless you navigate away from them. "
    "If you encounter an error, report the exact error message."
)

# --- Per-session browser instances ---
sessions = {}
sessions_lock = threading.Lock()

def _make_mcp_client():
    def _factory():
        return stdio_client(
            StdioServerParameters(
                command=NODE_PATH,
                args=[MCP_CLI, "--headless", "--no-sandbox", "--browser", "chromium"],
                env=MCP_ENV,
                stderr=sys.stderr
            )
        )
    return MCPClient(_factory)

def get_or_create_session(session_id):
    with sessions_lock:
        if session_id not in sessions:
            client = _make_mcp_client()  # ← 毎回新しいインスタンス
            client.__enter__()
            tools = client.list_tools_sync()
            agent = Agent(
                model=_make_model(),
                tools=tools,
                system_prompt=SYSTEM_PROMPT
            )
            sessions[session_id] = {"client": client, "agent": agent}
            logger.info("session_created", extra={"session_id": session_id})
        return sessions[session_id]

def chat(message, history, request: gr.Request):
    username = request.headers.get("x-ms-client-principal-name", "unknown")
    session_id = request.session_hash
    session = get_or_create_session(session_id)

    print("", flush=True)
    logger.info("chat_request", extra={"username": username, "message": message})
    try:
        response = session["agent"](message)
        return str(response)
    except Exception as e:
        logger.exception("chat_error", extra={"username": username, "error": str(e)})
        return f"An error occurred: {str(e)}"


with gr.Blocks(title="Playwright Web Agent") as demo:
    gr.Markdown("# ブラウザ操作エージェント")
    gr.Markdown("ブラウザ操作できます！")
    gr.ChatInterface(
        fn=chat,
        examples=[
            "https://arxiv.org/のArtificial Intelligenceにアクセスして、Top5の記事について教えて",
            "anthropic.com/newsにアクセスして、それぞれ直近５件のニュースタイトルを取得、モデルリリース関連のものを教えて",
            "github.com/trendingにアクセス、言語をPythonでフィルタ、今日のTop5リポジトリのうちAIに関するものを教えて",
            "googleにアクセスして、豊洲駅周辺でおすすめのラーメン屋を教えて"
        ],
    )

demo.launch(server_name="0.0.0.0", server_port=7860)

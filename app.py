import sys
import os
import time
import subprocess
import threading
import concurrent.futures
import gradio as gr
from loguru import logger
from strands import Agent
from strands.models.anthropic import AnthropicModel
from strands.tools.mcp import MCPClient
from mcp import stdio_client, StdioServerParameters
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider
from anthropic import AsyncAnthropicFoundry

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
        max_tokens=16384,
    )
    m.client = AsyncAnthropicFoundry(
        base_url="https://foundry-nakatsukasa1.services.ai.azure.com/anthropic",
        azure_ad_token_provider=token_provider,
    )
    return m

NODE_PATH = "/usr/bin/node"

def _find_mcp_cli():
    npm_root = subprocess.run(
        ["npm", "root", "-g"],
        capture_output=True,
        text=True,
        check=True
    ).stdout.strip()
    return f"{npm_root}/@playwright/mcp/cli.js"

MCP_CLI = _find_mcp_cli()
MCP_ENV = dict(os.environ)
MCP_ENV.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")

SYSTEM_PROMPT = (
    "You are a web automation assistant using Playwright. "
    "You can browse websites, extract information, click elements, "
    "fill forms, and take screenshots. "
    "If you encounter an error, report the exact error message."
    "If the user does not provide URL in the session, use google search, and research the top link, to answer questions."
)

sessions = {}
sessions_lock = threading.Lock()

SESSION_TIMEOUT = 1800
MAX_SESSIONS = 4
AGENT_TIMEOUT = 600

executor = concurrent.futures.ThreadPoolExecutor(max_workers=MAX_SESSIONS)


def _make_mcp_client():
    def _factory():
        return stdio_client(
            StdioServerParameters(
                command=NODE_PATH,
                args=[
                    MCP_CLI,
                    "--headless",
                    "--no-sandbox",
                    "--browser",
                    "chromium",
                ],
                env=MCP_ENV,
                stderr=sys.stderr,
            )
        )

    return MCPClient(_factory)


def close_session(session_id):
    with sessions_lock:
        s = sessions.pop(session_id, None)

    if s:
        try:
            s["client"].__exit__(None, None, None)
        except Exception as e:
            logger.warning(
                "session_close_error",
                extra={"session_id": session_id, "error": str(e)}
            )

        logger.info("session_closed", extra={"session_id": session_id})


def cleanup_old_sessions():
    now = time.time()
    old_ids = []

    for sid, s in list(sessions.items()):
        if now - s["last_used"] > SESSION_TIMEOUT:
            old_ids.append(sid)

    for sid in old_ids:
        close_session(sid)


def get_or_create_session(session_id):
    with sessions_lock:
        cleanup_old_sessions()

        if session_id in sessions:
            sessions[session_id]["last_used"] = time.time()
            return sessions[session_id]

        if len(sessions) >= MAX_SESSIONS:
            return None

    # Create MCP client outside lock to avoid global blocking
    client = _make_mcp_client()
    client.__enter__()
    tools = client.list_tools_sync()

    agent = Agent(
        model=_make_model(),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )

    session = {
        "client": client,
        "agent": agent,
        "last_used": time.time(),
    }

    with sessions_lock:
        sessions[session_id] = session

    logger.info("session_created", extra={"session_id": session_id})
    return session


def chat(message, history, request: gr.Request):
    username = request.headers.get("x-ms-client-principal-name", "unknown")

    # Important: after refresh, keep same browser session per user
    session_id = username if username != "unknown" else request.session_hash

    logger.info(
        "chat_request",
        extra={
            "username": username,
            "session_id": session_id,
            "message": message,
        },
    )

    if message.strip().lower() in ["reset", "/reset", "リセット"]:
        close_session(session_id)
        return "ブラウザセッションをリセットしました。"

    try:
        session = get_or_create_session(session_id)

        if session is None:
            return "現在混み合っています。しばらくしてからお試しください。"

        future = executor.submit(session["agent"], message)
        response = future.result(timeout=AGENT_TIMEOUT)

        return str(response)

    except concurrent.futures.TimeoutError:
        logger.error(
            "agent_timeout",
            extra={"username": username, "session_id": session_id}
        )
        close_session(session_id)
        return "処理がタイムアウトしました。ブラウザセッションをリセットしました。もう一度お試しください。"

    except Exception as e:
        logger.exception(
            "chat_error",
            extra={
                "username": username,
                "session_id": session_id,
                "error": str(e),
            },
        )
        close_session(session_id)
        return f"エラーが発生しました。ブラウザセッションをリセットしました: {str(e)}"


with gr.Blocks(title="Playwright Web Agent") as demo:
    gr.Markdown("# ブラウザ操作エージェント")
    gr.Markdown("ブラウザ操作できます！")

    gr.ChatInterface(
        fn=chat,
        examples=[
            "https://arxiv.org/ のArtificial Intelligenceにアクセスして、Top5の記事について教えて",
            "anthropic.com/newsにアクセスして、直近5件のニュースタイトルを取得して",
            "github.com/trendingにアクセス、言語をPythonでフィルタ、今日のTop5リポジトリを教えて",
            "googleにアクセスして、豊洲駅周辺でおすすめのラーメン屋を教えて",
            "リセット",
        ],
    )

demo.queue(default_concurrency_limit=MAX_SESSIONS)

demo.launch(
    server_name="0.0.0.0",
    server_port=7860,
)

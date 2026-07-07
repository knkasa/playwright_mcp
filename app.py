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


# -------------------------
# Azure Foundry auth
# -------------------------
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


# -------------------------
# Playwright MCP settings
# -------------------------
NODE_PATH = "/usr/bin/node"


def _find_mcp_cli():
    npm_root = subprocess.run(
        ["npm", "root", "-g"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    return f"{npm_root}/@playwright/mcp/cli.js"


MCP_CLI = _find_mcp_cli()

MCP_ENV = dict(os.environ)
MCP_ENV.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")


SYSTEM_PROMPT = (
    "You are a web automation assistant using Playwright. "
    "You can browse websites, extract information, click elements, "
    "fill forms, and take screenshots. "
    "The browser session persists across the conversation. "
    "If you encounter an error, report the exact error message."
)


# -------------------------
# Session management
# -------------------------
sessions = {}
sessions_lock = threading.Lock()

SESSION_TIMEOUT = 1800  # 30 minutes
MAX_SESSIONS = 4

CREATE_TIMEOUT = 60     # MCP startup timeout
AGENT_TIMEOUT = 180     # Agent execution timeout

executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=MAX_SESSIONS * 2
)


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
                extra={
                    "session_id": session_id,
                    "error": str(e),
                },
            )

        logger.info("session_closed", extra={"session_id": session_id})


def cleanup_old_sessions():
    now = time.time()
    expired = []

    with sessions_lock:
        for sid, s in list(sessions.items()):
            if now - s["last_used"] > SESSION_TIMEOUT:
                expired.append((sid, s))
                del sessions[sid]

    for sid, s in expired:
        try:
            s["client"].__exit__(None, None, None)
        except Exception:
            pass

        logger.info("session_cleaned", extra={"session_id": sid})


def _create_session(session_id):
    client = _make_mcp_client()
    client.__enter__()

    tools = client.list_tools_sync()

    agent = Agent(
        model=_make_model(),
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )

    return {
        "client": client,
        "agent": agent,
        "last_used": time.time(),
    }


def get_or_create_session(session_id):
    cleanup_old_sessions()

    with sessions_lock:
        if session_id in sessions:
            sessions[session_id]["last_used"] = time.time()
            return sessions[session_id]

        if len(sessions) >= MAX_SESSIONS:
            return None

    future = executor.submit(_create_session, session_id)

    try:
        session = future.result(timeout=CREATE_TIMEOUT)

    except concurrent.futures.TimeoutError:
        logger.error(
            "session_create_timeout",
            extra={"session_id": session_id},
        )
        return None

    except Exception as e:
        logger.exception(
            "session_create_error",
            extra={
                "session_id": session_id,
                "error": str(e),
            },
        )
        return None

    with sessions_lock:
        sessions[session_id] = session

    logger.info("session_created", extra={"session_id": session_id})
    return session


# -------------------------
# Gradio chat function
# -------------------------
def chat(message, history, request: gr.Request):
    username = request.headers.get("x-ms-client-principal-name", "unknown")

    # Keep your preferred behavior:
    # refreshing the browser page creates a new Gradio session.
    session_id = request.session_hash

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

    session = get_or_create_session(session_id)

    if session is None:
        return "セッション作成に失敗、または現在混み合っています。少し待ってから再読み込みしてください。"

    try:
        future = executor.submit(session["agent"], message)
        response = future.result(timeout=AGENT_TIMEOUT)

        session["last_used"] = time.time()

        return str(response)

    except concurrent.futures.TimeoutError:
        logger.error(
            "agent_timeout",
            extra={
                "username": username,
                "session_id": session_id,
            },
        )

        close_session(session_id)

        return (
            "処理がタイムアウトしました。"
            "ブラウザセッションをリセットしました。"
            "ページを再読み込みして、もう一度お試しください。"
        )

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

        return f"エラーが発生しました。セッションをリセットしました: {str(e)}"


# -------------------------
# Gradio UI
# -------------------------
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

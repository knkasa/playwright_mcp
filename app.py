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
from anthropic import AsyncAnthropicFoundry

# make sure to update requirements.txt for strands_agent.  There are openai and anthropic versions.

logger.remove()
logger.add(sys.stdout, serialize=True, level="INFO")

credential = ManagedIdentityCredential()
token_provider = get_bearer_token_provider(
    credential,
    "https://cognitiveservices.azure.com/.default"
)

model = AnthropicModel(
    client_args={"api_key": "placeholder"},  # dummy, will be overwritten
    model_id="claude-haiku-4-5",
    max_tokens=32768,
)
model.client = AsyncAnthropicFoundry(
    base_url="https://foundry-nakatsukasa1.services.ai.azure.com/anthropic",
    azure_ad_token_provider=token_provider,
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
    gr.Markdown("ブラウザ操作できます！！")

    gr.ChatInterface(
        fn=chat,
        examples=[
            "transit.yahoo.co.jpにアクセスして出発：東京駅、到着：新大阪駅、明日の9時出発で検索、最短ルートの所要時間と料金教えて",
            "openai.com/newsとanthropic.com/newsにアクセスして、それぞれ直近５件のニュースタイトルを取得、モデルリリース関連のものを教えて",
            "github.com/trendingにアクセス、言語をPythonでフィルタ、今日のTop10リポジトリのうちk、AIに関するだけを教えて",
            "食べログで「豊洲 ランチ」を検索、評価3.5以上、予算1500円以内のお店上位5件教えて"
        ],
    )

demo.launch(server_name="0.0.0.0", server_port=7860)

import sys
import gradio as gr
from strands import Agent
from strands.models.openai import OpenAIModel
from strands.tools.mcp import MCPClient
from mcp import stdio_client, StdioServerParameters
from openai import AsyncAzureOpenAI
from azure.identity import ManagedIdentityCredential, get_bearer_token_provider

# Azure OpenAI setup via Managed Identity
credential = ManagedIdentityCredential()
token_provider = get_bearer_token_provider(
    credential,
    "https://cognitiveservices.azure.com/.default"
)

azure_client = AsyncAzureOpenAI(
    azure_endpoint="https://foundry-nakatsukasa1.openai.azure.com/",
    azure_ad_token_provider=token_provider,
    api_version="2024-10-21",
)

model = OpenAIModel(
    client=azure_client,
    model_id="gpt-4o-mini",
)

# Linux paths inside container
import subprocess
import os

NODE_PATH = "/usr/bin/node"

def _find_mcp_cli():
    npm_root = subprocess.run(
        ["npm", "root", "-g"], capture_output=True, text=True, check=True
    ).stdout.strip()
    return f"{npm_root}/@playwright/mcp/cli.js"

MCP_CLI = _find_mcp_cli()

# Explicitly build env for the subprocess - some MCP SDK versions don't
# inherit the parent process environment automatically
MCP_ENV = dict(os.environ)
MCP_ENV.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")


def chat(message, history):
    """Handle a chat message with Playwright MCP agent."""
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

    with playwright_client:
        tools = playwright_client.list_tools_sync()

        # Build conversation history for the agent
        conversation = ""
        for human, assistant in history:
            conversation += f"User: {human}\nAssistant: {assistant}\n"
        conversation += f"User: {message}"

        agent = Agent(
            model=model,
            tools=tools,
            system_prompt=(
                "You are a web automation assistant using Playwright. "
                "You can browse websites, extract information, click elements, "
                "fill forms, and take screenshots. "
                "If you encounter an error, report the exact error message."
            )
        )

        response = agent(conversation)
        return str(response)


# Gradio UI
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

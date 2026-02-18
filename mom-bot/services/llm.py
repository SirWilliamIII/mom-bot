import json
from config import Config


def get_tool_definitions():
    from features.tools import TOOL_DEFINITIONS
    return TOOL_DEFINITIONS


def chat_stream(messages, on_partial=None, on_tool_call=None, on_done=None):
    provider = Config.LLM_PROVIDER
    if provider == "openai":
        return _openai_chat_stream(messages, on_partial, on_tool_call, on_done)
    elif provider == "gemini":
        return _gemini_chat_stream(messages, on_partial, on_tool_call, on_done)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")


def _openai_chat_stream(messages, on_partial, on_tool_call, on_done):
    from openai import OpenAI
    client = OpenAI(api_key=Config.OPENAI_API_KEY)

    tools_defs = get_tool_definitions()
    openai_tools = []
    for t in tools_defs:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            }
        })

    response = client.chat.completions.create(
        model=Config.OPENAI_LLM_MODEL,
        messages=messages,
        tools=openai_tools if openai_tools else None,
        stream=True,
    )

    full_text = ""
    tool_calls_data = {}

    for chunk in response:
        delta = chunk.choices[0].delta if chunk.choices else None
        if not delta:
            continue

        if delta.content:
            full_text += delta.content
            if on_partial:
                on_partial(delta.content)

        if delta.tool_calls:
            for tc in delta.tool_calls:
                idx = tc.index
                if idx not in tool_calls_data:
                    tool_calls_data[idx] = {"name": "", "arguments": ""}
                if tc.function.name:
                    tool_calls_data[idx]["name"] = tc.function.name
                if tc.function.arguments:
                    tool_calls_data[idx]["arguments"] += tc.function.arguments

    if tool_calls_data and on_tool_call:
        for idx in sorted(tool_calls_data.keys()):
            tc = tool_calls_data[idx]
            try:
                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}
            on_tool_call(tc["name"], args)

    if on_done:
        on_done(full_text)

    return full_text


def _gemini_chat_stream(messages, on_partial, on_tool_call, on_done):
    import google.generativeai as genai
    genai.configure(api_key=Config.GEMINI_API_KEY)

    tools_defs = get_tool_definitions()
    gemini_tools = []
    for t in tools_defs:
        gemini_tools.append(genai.protos.Tool(
            function_declarations=[
                genai.protos.FunctionDeclaration(
                    name=t["name"],
                    description=t["description"],
                    parameters=genai.protos.Schema(
                        type=genai.protos.Type.OBJECT,
                        properties={
                            k: genai.protos.Schema(
                                type=genai.protos.Type.STRING,
                                description=v.get("description", ""),
                            )
                            for k, v in t["parameters"].get("properties", {}).items()
                        },
                    ),
                )
            ]
        ))

    model = genai.GenerativeModel(
        Config.GEMINI_MODEL,
        tools=gemini_tools if gemini_tools else None,
    )

    gemini_messages = []
    for m in messages:
        role = "user" if m["role"] in ("user", "system") else "model"
        gemini_messages.append({"role": role, "parts": [m["content"]]})

    response = model.generate_content(gemini_messages, stream=True)

    full_text = ""
    for chunk in response:
        if chunk.text:
            full_text += chunk.text
            if on_partial:
                on_partial(chunk.text)

        if hasattr(chunk, "candidates"):
            for candidate in chunk.candidates:
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        if on_tool_call:
                            on_tool_call(fc.name, dict(fc.args))

    if on_done:
        on_done(full_text)

    return full_text

"""Web search via DuckDuckGo -- no API key needed."""

from duckduckgo_search import DDGS


def search_web(query, max_results=3):
    """Search the web and return results formatted for voice output."""
    try:
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))

        if not results:
            return f"I couldn't find anything about '{query}'."

        parts = []
        for r in results:
            title = r.get("title", "")
            body = r.get("body", "")
            if body:
                parts.append(f"{title}: {body}")

        return "Here's what I found. " + " ... ".join(parts)

    except Exception as e:
        print(f"[WebSearch] Error: {e}")
        return f"Sorry, I couldn't search right now. Maybe try again in a moment?"

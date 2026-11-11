# Fake news agent for testing the attack: neutral normally, very bullish when a
# fake headline is added. (The real News agent will use Gemini instead.)
AGENT_NAME = "fake_news"


def forecast(day, data):
    if data.get("fake_headline"):
        return {"agent": AGENT_NAME, "day": day, "p_up": 0.95,
                "evidence": [data["fake_headline"]], "data_missing": False}
    return {"agent": AGENT_NAME, "day": day, "p_up": 0.5, "evidence": [], "data_missing": False}

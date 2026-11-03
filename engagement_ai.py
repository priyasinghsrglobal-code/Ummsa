"""Optional Gemini rewrite of short original non-market engagement content.

Never ask language models to invent market news, trades, targets, or performance.
Static original content remains the fallback if generation is unavailable.
"""
import json
import os
import re
import urllib.request

MODEL="gemini-3.1-flash-lite"
BLOCKED=(r"https?://",r"\b(?:guaranteed|risk[- ]free|100%|buy now|sell now|profit assured)\b",
         r"\b(?:entry|stop.?loss|target|price is|market is currently)\b")

def rewrite(kind,original):
    key=os.getenv("GEMINI_API_KEY") or os.getenv("Gemini API Key")
    if not key or os.getenv("AI_ENGAGEMENT_ENABLED","true").lower()!="true":
        return original
    if kind not in ("question","meme"):return original
    style=("One natural, friendly Hinglish question that starts conversation."
           if kind=="question" else
           "One harmless funny observation about trading discipline, not a recommendation.")
    prompt=(f"Rewrite this original Telegram trading community post in natural human Hinglish. {style}\n"
            "Maximum two short lines, 170 characters total. No fake real-time market facts, "
            "prices, predictions, financial advice, links, claims of performance, hashtags, "
            "promotion, profanity, or invented quotes. Keep a normal casual voice. "
            "Write ONLY the post text.\n"
            f"Original: {original}")
    payload={"contents":[{"parts":[{"text":prompt}]}],
             "generationConfig":{"temperature":0.7,"maxOutputTokens":140}}
    req=urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/"+MODEL+":generateContent",
        data=json.dumps(payload).encode(),
        headers={"Content-Type":"application/json","x-goog-api-key":key},
        method="POST")
    try:
        with urllib.request.urlopen(req,timeout=7) as response:
            obj=json.loads(response.read(16000))
        text=obj["candidates"][0]["content"]["parts"][0]["text"].strip()
        text=text.strip("'\"\n ").replace("\r","")
        text=re.sub(r"\n{3,}","\n\n",text)
        if len(text)>170 or len(text)<12 or text.count("\n")>2:return original
        if any(re.search(pattern,text,re.I) for pattern in BLOCKED):return original
        return text
    except (OSError,ValueError,KeyError,IndexError,TypeError):
        return original

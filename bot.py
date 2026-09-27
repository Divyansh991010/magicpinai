import os
import time
import json
from datetime import datetime
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field
from typing import Any, Optional, Literal
from google import genai

# Setup GenAI client
# Load API key from environment (recommended for security)
from dotenv import load_dotenv
load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

app = FastAPI()
START = time.time()

# In-memory stores
contexts: dict[tuple[str, str], dict] = {}    # (scope, context_id) -> {version, payload}
conversations: dict[str, list] = {}           # conversation_id -> [turns]

@app.get("/v1/healthz")
async def healthz():
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in contexts.items():
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START), "contexts_loaded": counts}

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Team Antigravity",
        "team_members": ["Antigravity"],
        "model": "gemini-3.8-flash",
        "approach": "single-prompt composer with structured JSON output",
        "contact_email": "team@example.com",
        "version": "1.0.0",
        "submitted_at": datetime.utcnow().isoformat() + "Z"
    }

class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str

@app.post("/v1/context")
async def push_context(body: CtxBody):
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    if cur and cur["version"] >= body.version:
        return {"accepted": False, "reason": "stale_version", "current_version": cur["version"]}
    contexts[key] = {"version": body.version, "payload": body.payload}
    return {"accepted": True, "ack_id": f"ack_{body.context_id}_v{body.version}",
            "stored_at": datetime.utcnow().isoformat() + "Z"}

class TickBody(BaseModel):
    now: str
    available_triggers: list[str] = []

@app.post("/v1/tick")
async def tick(body: TickBody):
    actions = []
    for trg_id in body.available_triggers:
        trg = contexts.get(("trigger", trg_id), {}).get("payload")
        if not trg: continue
        merchant_id = trg.get("merchant_id")
        customer_id = trg.get("customer_id")
        
        merchant = contexts.get(("merchant", merchant_id), {}).get("payload")
        category = contexts.get(("category", merchant.get("category_slug")), {}).get("payload") if merchant else None
        customer = contexts.get(("customer", customer_id), {}).get("payload") if customer_id else None
        
        if not (merchant and category): continue
        
        prompt = f"""
You are an AI generating WhatsApp messages for merchants (and sometimes their customers) as 'Vera', an AI assistant for magicpin.
Compose a single WhatsApp message.
Rules:
1. Specificity wins: anchor on concrete verifiable facts from the provided context (e.g. 6,777 missed searches, Dental Cleaning @ ₹299).
2. Category fit: match the voice and vocabulary.
3. Merchant fit: personalize to this specific merchant's state and language preference (e.g., Hindi-English mix if requested).
4. Trigger relevance: clearly communicate *why now*.
5. Engagement compulsion: use curiosity, social proof, loss aversion, etc. Give a single binary commitment (YES/STOP) or open-ended question.
6. DO NOT hallucinate facts, DO NOT use generic templates ("Flat 30% off").
7. If sending to merchant, use 'vera' as send_as. If to customer, use 'merchant_on_behalf' as send_as.

Contexts:
Category: {json.dumps(category)}
Merchant: {json.dumps(merchant)}
Customer: {json.dumps(customer) if customer else "None"}
Trigger: {json.dumps(trg)}

Output valid JSON exactly matching this format:
{{
  "body": "The message body text",
  "cta": "open_ended or binary_choice or none",
  "send_as": "vera" (or "merchant_on_behalf" if customer context exists),
  "suppression_key": "any dedup key, you can just use the trigger suppression_key",
  "rationale": "Why you chose this message"
}}
"""
        
        try:
            from google.genai import types
            
            response = client.models.generate_content(
                model="gemini-3.8-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    response_mime_type="application/json"
                )
            )
            out = json.loads(response.text)
            
            actions.append({
                "conversation_id": f"conv_{merchant_id}_{trg_id}",
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": out.get("send_as", "vera"),
                "trigger_id": trg_id,
                "template_name": "vera_generic_v1",
                "template_params": [],
                "body": out.get("body", ""),
                "cta": out.get("cta", ""),
                "suppression_key": out.get("suppression_key", trg.get("suppression_key", "")),
                "rationale": out.get("rationale", "")
            })
        except Exception as e:
            print("Error generating message:", e)
            
    return {"actions": actions}

class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int

@app.post("/v1/reply")
async def reply(body: ReplyBody):
    conv = conversations.setdefault(body.conversation_id, [])
    conv.append({"from": body.from_role, "msg": body.message})
    
    merchant = contexts.get(("merchant", body.merchant_id), {}).get("payload") if body.merchant_id else None
    
    prompt = f"""
You are Vera, an AI assistant. You just received a reply in an ongoing conversation.
Conversation history (latest last):
{json.dumps(conv, indent=2)}

Merchant Context: {json.dumps(merchant)}

Your job is to decide the next action.
- If it looks like a canned auto-reply (e.g. repeated exact same message or generic 'thank you for contacting'), and we already tried to get past it once, we should exit gracefully (end).
- If the merchant/customer shows intent ("yes let's do it"), switch to action mode (acknowledge and proceed, e.g., "Got it, I am doing X").
- If they are not interested, exit gracefully (end).
- If they ask for time, wait.

Output valid JSON exactly matching this format:
{{
  "action": "send" or "wait" or "end",
  "body": "The reply message body (if action is send, else empty string)",
  "cta": "open_ended or none (if action is send, else empty string)",
  "wait_seconds": 1800 (if action is wait, else 0),
  "rationale": "Why you chose this action"
}}
"""
    try:
        from google.genai import types
        response = client.models.generate_content(
            model="gemini-3.8-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0,
                response_mime_type="application/json"
            )
        )
        out = json.loads(response.text)
        
        action = out.get("action", "end")
        resp_dict = {"action": action, "rationale": out.get("rationale", "")}
        if action == "send":
            resp_dict["body"] = out.get("body", "")
            resp_dict["cta"] = out.get("cta", "")
            conv.append({"from": "vera", "msg": resp_dict["body"]})
        elif action == "wait":
            resp_dict["wait_seconds"] = out.get("wait_seconds", 1800)
            
        return resp_dict
        
    except Exception as e:
        print("Error processing reply:", e)
        return {"action": "end", "rationale": "Error in processing"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

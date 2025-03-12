from dotenv import load_dotenv
import os
from telegram.ext import Application, MessageHandler, filters
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes
from flask import Flask, request, jsonify
from openai import OpenAI
import json
import time
import re
from collections import defaultdict

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN is not set in .env file")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY is not set in .env file")

BASE_URL = os.getenv("BASE_URL", "http://localhost:5000")  # Uppdatera till Render-URL vid deploy
PORT = int(os.getenv("PORT", 5000))

client = OpenAI(api_key=OPENAI_API_KEY)
app = Flask(__name__)
# Använd en dictionary i minnet istället för fil, eftersom vi inte har disk
profiles = defaultdict(lambda: {"history": "", "is_premium": False, "plans": [], "tone": None, "focus_area": None, "last_schema_time": 0, "last_plan_reference": None, "goals": []})

def detect_language(text):
    text = text.lower()
    if any(word in text for word in ["hej", "tack", "schema", "träning"]):
        return "sv"
    if any(word in text for word in ["hello", "thanks", "plan", "training"]):
        return "en"
    return "en"

TRANSLATIONS = {
    "sv": {
        "show_plans": "Här är dina planer",
        "new_plan_prompt": "Du har redan ett schema. Vill du uppdatera det eller skapa ett nytt? Svara 'uppdatera' eller 'ny'.",
        "plan_link": "Kopiera denna länk för att se schemat: ",
        "plan_created": "Här är ditt schema",
        "update_or_new": "Menar du {name}? Länk: {url}\nVill du uppdatera den eller skapa en ny? Svara 'uppdatera' eller 'ny'.",
        "no_plans": "Du har inga planer än",
        "error_generating": "Fel vid generering av schema. Försök igen!",
        "same_plan": "Det här schemat är samma som ditt senaste ({name}). Länk: {url}",
        "off_topic": "Jag är här för att hjälpa dig med life coaching, så jag är bäst på ämnen som träning, mindset, karriär, ekonomi och produktivitet. Vad vill du fokusera på?",
    },
    "en": {
        "show_plans": "Here are your plans",
        "new_plan_prompt": "You already have a plan. Do you want to update it or create a new one? Reply 'update' or 'new'.",
        "plan_link": "Copy this link to see the plan: ",
        "plan_created": "Here is your plan",
        "update_or_new": "Do you mean {name}? Link: {url}\nDo you want to update it or create a new one? Reply 'update' or 'new'.",
        "no_plans": "You have no plans yet",
        "error_generating": "Error generating plan. Please try again!",
        "same_plan": "This plan is the same as your latest ({name}). Link: {url}",
        "off_topic": "I’m here to help with life coaching, so I’m best suited for topics like training, mindset, career, finance, and productivity. What would you like to focus on?",
    }
}

def generate_plan_name(schedule, focus_area):
    activities = re.findall(r'(?:Mån|Tis|Ons|Tor|Fre|Lör|Sön): ([^\n]+)', schedule)
    unique_activities = set(activities)
    name_parts = list(unique_activities)[:2]
    if not name_parts:
        return f"{focus_area.capitalize()} Basplan"
    return f"{focus_area.capitalize()} - {' och '.join(name_parts).capitalize()}"

def plans_are_similar(plan1, plan2):
    plan1_content = plan1.get("content", "").split("Här är ditt schema:")[0].strip()
    plan2_content = plan2.get("content", "").split("Här är ditt schema:")[0].strip()
    return plan1_content == plan2_content

def find_plan_by_name(plans, name):
    name = name.lower()
    for idx, plan in enumerate(plans):
        if name in plan["name"].lower():
            return idx
    return None

async def check_goals(context):
    for user_id, profile in profiles.items():
        if profile.get("is_premium", False) and "goals" in profile:
            for goal in profile["goals"]:
                if not goal["done"] and time.strftime("%H:%M") >= goal["time"]:
                    lang = detect_language(profile["history"])
                    message = "Hur gick det med {task}?" if lang == "sv" else "How did it go with {task}?"
                    await context.bot.send_message(chat_id=int(user_id), text=message.format(task=goal["task"]))

def get_llm_response(user_id, user_message, profiles):
    profile = profiles[user_id]
    chat_history = profile["history"]
    tone = profile.get("tone", None)
    focus_area = profile.get("focus_area", None)
    is_premium = profile.get("is_premium", False)
    lang = detect_language(user_message)
    translations = TRANSLATIONS.get(lang, TRANSLATIONS["en"])

    print(f"User {user_id}: Tone={tone}, Focus={focus_area}, Premium={is_premium}, Message={user_message}, Language={lang}")

    if tone and focus_area:
        if "visa planer" in user_message.lower() or "visa lista" in user_message.lower() or "show plans" in user_message.lower():
            plans = profile.get("plans", [])
            if not plans:
                return translations["no_plans"], None
            response = f"{translations['show_plans']}\n"
            for idx, plan in enumerate(plans):
                url = f"{BASE_URL}/user/{user_id}/plan/{idx}"
                response += f"{plan['name']}\n{translations['plan_link']}{url}\n"
            return response, None
        elif any(keyword in user_message.lower() for keyword in ["schema", "plan", "skapa", "ge mig", "create", "give"]):
            if is_premium:
                plans = profile.get("plans", [])
                plan_reference = None
                for word in user_message.lower().split():
                    plan_idx = find_plan_by_name(plans, word)
                    if plan_idx is not None:
                        plan_reference = plan_idx
                        break
                
                if plan_reference is not None:
                    url = f"{BASE_URL}/user/{user_id}/plan/{plan_reference}"
                    return translations["update_or_new"].format(name=plans[plan_reference]['name'], url=url), None
                
                if user_message.lower() in ["uppdatera", "update", "ny", "new"]:
                    if user_message.lower() in ["uppdatera", "update"] and "last_plan_reference" in profile:
                        plan_reference = profile["last_plan_reference"]
                    else:
                        plan_reference = None

                if plans and time.time() - profile.get("last_schema_time", 0) < 300 and plan_reference is None:
                    plan_idx = len(plans) - 1
                    url = f"{BASE_URL}/user/{user_id}/plan/{plan_idx}"
                    return f"{translations['new_plan_prompt']}\n{translations['plan_created']}\n{translations['plan_link']}{url}", None
                
                prompt = (
                    f"You are a life coach coaching in {tone}-style. The user has chosen focus area: {focus_area} and is premium. "
                    f"Generate a detailed weekly plan for {focus_area} in the following format: "
                    "Mån: [activity]\nTis: [activity]\nOns: [activity]\nTor: [activity]\nFre: [activity]\nLör: [activity]\nSön: [activity]\n"
                    "Example: Mån: Running 30 min\nTis: Strength 45 min\nOns: Rest\nTor: Yoga 30 min\nFre: Cycling 1 hour\nLör: Swimming 45 min\nSön: Rest\n"
                    f"Previous conversation: {chat_history}\nUser said: {user_message}"
                )
            else:
                return "You need a premium account to see plans!" if lang == "en" else "Du behöver ett premiumkonto för att se planer!", None
        elif "jag är klar" in user_message.lower() or "i am done" in user_message.lower() and is_premium:
            prompt = (
                f"You are a life coach coaching in {tone}-style. The user has chosen focus area: {focus_area}. "
                "The user has said they are done with a task. Mark the goal as complete and give an encouraging response (e.g. 'Well done!'). "
                f"Previous conversation: {chat_history}\nUser said: {user_message}"
            )
        else:
            # Generell konversation - kolla om det är off-topic
            off_topic_keywords = ["politik", "väder", "sport", "politik", "weather", "sports"]
            if any(keyword in user_message.lower() for keyword in off_topic_keywords):
                return translations["off_topic"], None
            prompt = (
                f"You are a life coach coaching in {tone}-style. The user has chosen focus area: {focus_area}. "
                "Help the user as a partner. If a goal is mentioned (e.g. 'train at 18:00'), suggest it and note it. "
                "If the topic is unrelated to life coaching, gently suggest returning to relevant topics like training, mindset, career, finance, or productivity. "
                f"Previous conversation: {chat_history}\nUser said: {user_message}"
            )
    elif not tone:
        prompt = (
            "You are a life coach getting to know the user from scratch. Start by asking their name and guide the conversation naturally. "
            "After the name, ask: 'How would you like me to coach you? Choose 1) As a motivating friend, 2) As a strict mentor, 3) As a relaxed guide.' "
            f"Previous conversation: {chat_history}\nUser said: {user_message}"
        )
    elif not focus_area:
        prompt = (
            "You are a life coach. The user is in basic mode and must choose a focus area. Ask: 'Which area do you want to focus on? Choose one: training, mindset, career, finance, productivity.' "
            f"Previous conversation: {chat_history}\nUser said: {user_message}"
        )

    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=200
    )
    print("OpenAI response:", response.choices[0].message.content)
    coach_response = response.choices[0].message.content

    if any(keyword in user_message.lower() for keyword in ["schema", "plan", "skapa", "ge mig", "create", "give"]) and is_premium:
        match = re.search(r'(Mån:.*\nTis:.*\nOns:.*\nTor:.*\nFre:.*\nLör:.*\nSön:.*)', coach_response, re.DOTALL)
        plans = profile.get("plans", [])
        plan_idx = len(plans)
        url = f"{BASE_URL}/user/{user_id}/plan/{plan_idx}"
        if match:
            schedule = match.group(0).strip()
            days = schedule.split('\n')
            formatted_schedule = '\n'.join(day.strip() for day in days if day.strip())
            
            if plans and plans_are_similar(plans[-1], {"content": formatted_schedule}):
                plan_idx = len(plans) - 1
                url = f"{BASE_URL}/user/{user_id}/plan/{plan_idx}"
                coach_response = translations["same_plan"].format(name=plans[-1]['name'], url=url)
                profile["last_schema_time"] = time.time()
            else:
                plan_name = generate_plan_name(formatted_schedule, focus_area)
                coach_response = f"{formatted_schedule}\n{translations['plan_created']}\n{translations['plan_link']}{url}"
                profile["last_schema_time"] = time.time()
                if "plans" not in profile:
                    profile["plans"] = []
                profile["plans"].append({"name": plan_name, "content": formatted_schedule})
            return coach_response, None
        else:
            coach_response = f"{translations['error_generating']}\n{translations['plan_created']}\n{translations['plan_link']}{url}"
            return coach_response, None

    profile = profiles[user_id]
    if any(keyword in user_message.lower() for keyword in ["schema", "plan", "skapa", "ge mig", "create", "give"]) and plan_reference is not None:
        profile["last_plan_reference"] = plan_reference
    elif "kl" in user_message.lower() or "at" in user_message.lower() and is_premium:
        profile["goals"] = profile.get("goals", []) + [{"task": user_message, "time": "20:00", "done": False}]
    elif ("jag är klar" in user_message.lower() or "i am done" in user_message.lower()) and is_premium and "goals" in profile:
        for goal in profile["goals"]:
            if not goal["done"] and goal["task"].lower() in user_message.lower():
                goal["done"] = True
                coach_response += "\nBra jobbat!" if lang == "sv" else "\nWell done!"

    return coach_response, None

@app.route('/webhook', methods=['POST'])
async def webhook():
    print("Received webhook update:", request.get_json())  # Logga inkommande uppdateringar
    update = Update.de_json(request.get_json(), application.bot)
    if update:
        user_id = str(update.effective_user.id)
        user_message = update.message.text if update.message else ""
        chat_id = update.message.chat_id if update.message else None

        if chat_id:
            is_premium = profiles[user_id].get("is_premium", False)
            coach_response = get_llm_response(user_id, user_message, profiles)[0]
            profiles[user_id]["history"] += f"\nAnvändaren: {user_message}\n{coach_response}"
            if "Välj 1)" in coach_response and user_message in ["1", "2", "3"]:
                profiles[user_id]["tone"] = user_message
            elif "fokusera på" in coach_response.lower() and user_message.lower() in ["träning", "mindset", "karriär", "ekonomi", "produktivitet", "training", "mindset", "career", "finance", "productivity"]:
                profiles[user_id]["focus_area"] = user_message.lower()

            await application.bot.send_message(chat_id=chat_id, text=coach_response)
    return jsonify({'status': 'ok'})

if __name__ == '__main__':
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, webhook))
    application.job_queue.run_repeating(check_goals, interval=3600, first=10)

    # Kör Flask-appen
    print("Starting Flask server...")
    app.run(host='0.0.0.0', port=PORT)

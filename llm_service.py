import json
from openai import AsyncOpenAI
from config import OPENAI_API_KEY

client = AsyncOpenAI(api_key=OPENAI_API_KEY)


async def validate_answers(answers: dict) -> tuple[bool, list[str]]:
    """Validate that answers are legitimate (not gibberish/nonsense).

    Returns (is_valid, list_of_invalid_field_names).
    """
    prompt = f"""You are a verification assistant for a community group. A new member has provided these answers:

Name: {answers.get('name', '')}
About (who they are & what they do): {answers.get('about', '')}
Location: {answers.get('location', '')}
Fun fact: {answers.get('fun_fact', '')}
Contribution (how they want to contribute): {answers.get('contribution', '')}

Determine if each answer is legitimate. An answer is NOT legitimate if it is:
- Random gibberish (e.g. "asdfhjkl", "uoashfoa")
- Completely dismissive (e.g. "idk", "whatever", "no", "n/a")
- Has no contextual meaning for the question asked
- Obviously trolling

An answer IS legitimate even if it's brief, as long as it actually answers the question meaningfully.

Respond in JSON format:
{{"valid": true/false, "invalid_fields": ["field_name1", "field_name2"], "reason": "brief explanation"}}

Only include field names from: name, about, location, fun_fact, contribution"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    result = json.loads(response.choices[0].message.content)
    return result.get("valid", False), result.get("invalid_fields", [])


async def generate_intro(answers: dict) -> str:
    """Generate a structured welcome intro for a new community member."""
    prompt = f"""You are writing a welcome introduction for a new member joining the Superteam MY community.

Member info:
- Name: {answers.get('name', '')}
- About: {answers.get('about', '')}
- Location: {answers.get('location', '')}
- Fun fact: {answers.get('fun_fact', '')}
- How they want to contribute: {answers.get('contribution', '')}

Write the intro following this structure. Vary the tone, wording, and emoji usage each time so it feels fresh and human — sometimes enthusiastic, sometimes chill, sometimes witty. Mix it up.

Structure:
Hey everyone! Let's welcome [Name] 👋

[1-2 sentences about who they are and what they do, written naturally]

📍 Based in [location]

🧑‍🎓 Fun fact: [their fun fact]

🤝 Looking to contribute by:
• [break their contribution into bullet points if multiple ideas, or single bullet if one thing]

[Short friendly closing line encouraging people to connect]

Rules:
- Use their actual name, no brackets or placeholders in the output
- Vary your opening greeting, emoji choices, and closing line
- Break contributions into bullet points if they mention multiple things
- Keep it warm and community-oriented
- Do NOT wrap in quotes or add meta-commentary"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
    )

    return response.choices[0].message.content.strip()


async def summarize_messages(messages: list[dict]) -> str:
    """Summarize a list of chat messages into key topics being discussed."""
    formatted = "\n".join(
        f"[{m['display_name']}]: {m['text']}" for m in messages
    )

    prompt = f"""Here are the latest messages from a community group chat. Summarize the key topics and discussions happening. Be concise — use bullet points. Group related messages into themes.

Messages:
{formatted}

Provide a clear summary of what's being discussed right now."""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    return response.choices[0].message.content.strip()


async def is_contact_query(message_text: str) -> bool:
    """Check if a message is asking about who to contact or who is in charge."""
    prompt = f"""Determine if this message is asking about who to contact, who is in charge, who is the point of contact, who to reach out to, or who is responsible for the community/organization (Superteam MY or STMY).

Message: "{message_text}"

Respond with ONLY "yes" or "no"."""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=5,
    )

    return response.choices[0].message.content.strip().lower() == "yes"


async def answer_members_question(question: str, members: list[dict]) -> str:
    """Answer an admin's question about the community member base using members data."""
    members_info = json.dumps(members, indent=2, ensure_ascii=False)

    prompt = f"""You are an analytics assistant for the Superteam MY community. Here is the member database:

{members_info}

Each member has: handle, name, about, location, fun_fact, contribution, verified_at.
Note: older members may have a "profession" field instead of the newer fields.

The admin is asking: "{question}"

Answer the question based on the member data. Be concise and useful. If the question asks for counts, breakdowns, or patterns, provide them. If the data doesn't contain enough info to answer, say so."""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    return response.choices[0].message.content.strip()

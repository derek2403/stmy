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
Gender: {answers.get('gender', '')}
Location: {answers.get('location', '')}
Occupation: {answers.get('occupation', '')}
Fun fact: {answers.get('fun_fact', '')}

Determine if each answer is legitimate. An answer is NOT legitimate if it is:
- Random gibberish (e.g. "asdfhjkl", "uoashfoa")
- Completely dismissive (e.g. "idk", "whatever", "no", "n/a")
- Has no contextual meaning for the question asked
- Obviously trolling

An answer IS legitimate even if it's short, as long as it actually answers the question meaningfully.

Respond in JSON format:
{{"valid": true/false, "invalid_fields": ["field_name1", "field_name2"], "reason": "brief explanation"}}

Only include field names from: name, gender, location, occupation, fun_fact"""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.1,
    )

    result = json.loads(response.choices[0].message.content)
    return result.get("valid", False), result.get("invalid_fields", [])


async def generate_intro(answers: dict) -> str:
    """Generate a welcome intro paragraph from the verified answers."""
    prompt = f"""Generate a warm, friendly welcome introduction for a new community member based on their answers. Keep it to 2-3 sentences max.

Name: {answers.get('name', '')}
Gender: {answers.get('gender', '')}
Location: {answers.get('location', '')}
Occupation: {answers.get('occupation', '')}
Fun fact: {answers.get('fun_fact', '')}

Use appropriate pronouns based on their gender. Format:
"Welcome [Name]! [He/She/They] is [occupation] based in [location]. Fun fact: [fun fact about them]!"

Keep it natural and welcoming. Do not add any extra commentary."""

    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
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

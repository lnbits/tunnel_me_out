"""Sync missing translation keys from static/i18n/en.js into another language."""

import os
import re
import sys

import json5
from openai import OpenAI

if len(sys.argv) < 2:
    print("Usage: python3 tools/i18n-ai-tool.py <code>")
    sys.exit(1)
lang = sys.argv[1]

assert os.getenv("OPENAI_API_KEY"), "OPENAI_API_KEY env var not set"


def load_language(lang: str) -> dict:
    s = open(f"static/i18n/{lang}.js").read()
    prefix = f"window.localisation.{lang}.tunnel_me_out = {{\n"
    assert s.startswith(prefix)
    s = s[len(prefix) - 2 :]
    data = json5.loads(s)
    assert isinstance(data, dict)
    return data


def save_language(lang: str, data) -> None:
    with open(f"static/i18n/{lang}.js", "w") as f:
        f.write(f"window.localisation.{lang}.tunnel_me_out = {{\n")
        row = 0
        for k, v in data.items():
            row += 1
            f.write(f"  {k}:\n")
            if "'" in v:
                f.write(f'    "{v}"')
            else:
                f.write(f"    '{v}'")
            if row == len(data):
                f.write("\n")
            else:
                f.write(",\n")
        f.write("}\n")


def string_variables_match(str1: str, str2: str) -> bool:
    pat = re.compile(r"\{[a-zA-Z0-9_]*\}")
    m1 = re.findall(pat, str1)
    m2 = re.findall(pat, str2)
    return sorted(m1) == sorted(m2)


def translate_string(lang_to: str, text: str) -> str | None:
    target = {
        "de": "German",
        "es": "Spanish",
        "jp": "Japanese",
        "cn": "Chinese",
        "fr": "French",
        "it": "Italian",
        "pi": "Pirate",
        "nl": "Dutch",
        "we": "Welsh",
        "pl": "Polish",
        "pt": "Portuguese",
        "br": "Brazilian Portuguese",
        "cs": "Czech",
        "sk": "Slovak",
        "kr": "Korean",
        "fi": "Finnish",
    }[lang_to]
    client = OpenAI()
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a language expert translating software UI text from "
                        "English into another language. Keep placeholders in curly "
                        "braces verbatim, for example {date}. Output only the "
                        "translated string."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Translate the following string from English to {target}: {text}",
                },
            ],
            model="gpt-4o",
        )
        assert chat_completion.choices[0].message.content, "No response from GPT-4"
        translated = chat_completion.choices[0].message.content.strip()
        if string_variables_match(text, translated):
            return translated
        return None
    except Exception:
        return None


data_en = load_language("en")
data = load_language(lang)

missing = set(data_en.keys()) - set(data.keys())
print(f"Missing {len(missing)} keys in language '{lang}'")

if len(missing) > 0:
    new = {}
    for k in data_en:
        if k in data:
            new[k] = data[k]
        else:
            translated = translate_string(lang, data_en[k])
            if translated:
                new[k] = translated
            else:
                print(f"ERROR translating key: {k}")
    save_language(lang, new)
else:
    for k in data_en:
        if not string_variables_match(data_en[k], data[k]):
            print(f"Variables mismatch ({k}):")
            print(data_en[k])
            print(data[k])

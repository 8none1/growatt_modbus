#!/usr/bin/env python3
import sys
import json
import re

def extract_fields(data, fields=None):
    if fields is None:
        fields = set()
    if isinstance(data, dict):
        for key, value in data.items():
            fields.add(key)
            extract_fields(value, fields)
    elif isinstance(data, list):
        for item in data:
            extract_fields(item, fields)
    return fields

def try_parse_json_strings(text):
    # Match curly braces or array-like JSONs using regex (simplified)
    json_like_pattern = re.compile(r'({.*?}|\[.*?\])', re.DOTALL)
    for match in json_like_pattern.finditer(text):
        try:
            parsed = json.loads(match.group(0))
            yield parsed
        except json.JSONDecodeError:
            continue

def main():
    input_text = sys.stdin.read()
    all_fields = set()

    for obj in try_parse_json_strings(input_text):
        fields = extract_fields(obj)
        all_fields.update(fields)

    for field in sorted(all_fields):
        print(field)

if __name__ == "__main__":
    main()

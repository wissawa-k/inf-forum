#!/usr/bin/env python3

import json
from urllib.parse import quote
from urllib.request import Request, urlopen

title = "Pulmonary hypertension"

url = (
    "https://en.wikipedia.org/api/rest_v1/page/summary/"
    + quote(title, safe="")
)

request = Request(
    url,
    headers={
        "User-Agent": "wikifetch/1.0"
    }
)

with urlopen(request) as response:
    data = json.loads(response.read())

print(f"Title: {data['title']}")
print(f"Description: {data.get('description', '')}")
print(f"\nSummary:\n{data['extract']}")
print(
    f"\nSource: "
    f"{data['content_urls']['desktop']['page']}"
)

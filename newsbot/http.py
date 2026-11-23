"""Bounded HTTP body reads shared by the API integrations."""

import json


async def read_json(response, limit=256_000):
    # StreamReader.read(n) may return only the first network chunk, not the whole body.
    body = bytearray()
    async for chunk in response.content.iter_chunked(65_536):
        body.extend(chunk)
        if len(body) > limit:
            raise ValueError("JSON response exceeds size limit")
    return json.loads(body)

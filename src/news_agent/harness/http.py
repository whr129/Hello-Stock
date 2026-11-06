"""Bound HTTP response bytes before the official MCP SDK decodes JSON/SSE."""

import httpx2


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit

    async def __aiter__(self):
        total = 0
        try:
            async for chunk in self.stream:
                total += len(chunk)
                if total > self.limit:
                    raise httpx2.ReadError("MCP response exceeded the transport byte limit")
                yield chunk
        finally:
            await self.stream.aclose()

    async def aclose(self):
        await self.stream.aclose()


class BoundedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, transport, limit):
        self.transport = transport
        self.limit = limit

    async def handle_async_request(self, request):
        request.headers["Accept-Encoding"] = "identity"
        response = await self.transport.handle_async_request(request)
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            await response.aclose()
            raise httpx2.ReadError("Compressed MCP responses are not accepted")
        return httpx2.Response(
            response.status_code,
            headers=response.headers,
            stream=BoundedStream(response.stream, self.limit),
            extensions=response.extensions,
        )

    async def aclose(self):
        await self.transport.aclose()

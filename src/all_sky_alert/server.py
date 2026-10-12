"""Stop camera streams before Uvicorn drains HTTP, then normal lifespan cleanup."""
import asyncio

import uvicorn


class ApplicationServer(uvicorn.Server):
    def __init__(self, config, camera):
        super().__init__(config)
        self.camera = camera

    async def shutdown(self, sockets=None):
        # Uvicorn drains connections before running lifespan shutdown. An MJPEG
        # response is intentionally infinite, so stop its producer first. This
        # also rejects new camera commands and releases hardware on its owner.
        await asyncio.to_thread(self.camera.close)
        await super().shutdown(sockets=sockets)

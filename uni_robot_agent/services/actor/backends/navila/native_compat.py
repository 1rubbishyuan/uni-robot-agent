"""The published NaVILA server uses length-prefixed JSON over plain TCP."""
import base64
import io
import json
import socket
import time


def _receive_exact(connection, count, deadline):
    chunks = []
    while count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("NaVILA response deadline expired")
        connection.settimeout(remaining)
        chunk = connection.recv(count)
        if not chunk:
            raise ConnectionError("NaVILA server disconnected before completing its response")
        chunks.append(chunk)
        count -= len(chunk)
    return b"".join(chunks)


def predict(config, images, instruction):
    if len(images) != 8:
        raise ValueError("NaVILA wire requests require exactly eight images")
    encoded = []
    for image in images:
        stream = io.BytesIO()
        image.save(stream, format="JPEG", quality=75)
        encoded.append(base64.b64encode(stream.getvalue()).decode("ascii"))
    payload = json.dumps({"images": encoded, "query": instruction}).encode("utf-8")
    deadline = time.monotonic() + config.request_timeout_s
    # One connection is one upstream inference. Never retry an ambiguous request.
    with socket.create_connection((config.upstream_host, config.upstream_port),
                                  timeout=config.request_timeout_s) as connection:
        connection.settimeout(max(.001, deadline - time.monotonic()))
        connection.sendall(len(payload).to_bytes(8, "big") + payload)
        size = int.from_bytes(_receive_exact(connection, 8, deadline), "big")
        if not 0 < size <= config.max_response_bytes:
            raise ValueError("NaVILA response length is outside the admitted range")
        response = json.loads(_receive_exact(connection, size, deadline).decode("utf-8"))
    if not isinstance(response, str):
        raise ValueError("NaVILA must return a JSON string containing one navigation decision")
    return response

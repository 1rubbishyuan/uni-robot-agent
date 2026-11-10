"""CPU checks for lossless Omni image delivery and batched context selection."""
import base64
import copy
import io
import unittest

from PIL import Image

from uni_robot_agent.context.history import ContextRequest
from uni_robot_agent.context.policies.sliding_window import SlidingWindowPolicy
from uni_robot_agent.provider.backends.omnirouter.chat_completions import OmniChatCompletionsAdapter
from uni_robot_agent.provider.backends.omnirouter.messages import OmniMessagesAdapter
from uni_robot_agent.provider.image_encoding import encode_messages_images, lossless_image_url


def image_url(mode="RGBA"):
    image = Image.new(mode, (17, 13))
    image.putdata([((i * 19) % 256, (i * 37) % 256, (i * 71) % 256, i % 256)[:len(mode)]
                   for i in range(17 * 13)])
    data = io.BytesIO()
    image.save(data, format="PNG")
    return image, "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()


def request(url, count=1):
    items = [{"type": "message", "role": "user", "content": "Inspect the returned images."}]
    for i in range(count):
        items.extend([
            {"type": "function_call", "call_id": f"call_{i}", "name": "observe", "arguments": "{}"},
            {"type": "function_call_output", "call_id": f"call_{i}", "output": [
                {"type": "input_text", "text": f"receipt {i}"},
                {"type": "input_image", "image_url": url}]}])
    return {"model": "example", "input": items, "reasoning": {"effort": "high"}}


class ProviderImageTests(unittest.TestCase):
    def test_pixels_and_passthrough(self):
        for mode in ("RGB", "RGBA"):
            original, url = image_url(mode)
            encoded = lossless_image_url(url)
            self.assertTrue(encoded.startswith("data:image/webp;base64,"))
            decoded = Image.open(io.BytesIO(base64.b64decode(encoded.split(",", 1)[1]))).convert(mode)
            self.assertEqual(decoded.size, original.size)
            self.assertEqual(decoded.tobytes(), original.tobytes())
            self.assertEqual(lossless_image_url(encoded), encoded)
        for url in ("https://example.org/image.png", "data:image/jpeg;base64,unchanged"):
            self.assertEqual(lossless_image_url(url), url)

    def test_adapters_preserve_receipts_and_original_history(self):
        _, url = image_url()
        source = request(url)
        before = copy.deepcopy(source)
        native = OmniMessagesAdapter().encode_request(source).body
        result = native["messages"][-1]["content"][0]
        self.assertEqual(result["tool_use_id"], "call_0")
        self.assertEqual(result["content"][0]["text"], "receipt 0")
        self.assertEqual(result["content"][1]["source"]["media_type"], "image/webp")
        chat = OmniChatCompletionsAdapter().encode_request(source).body["messages"]
        self.assertEqual(chat[-2]["tool_call_id"], "call_0")
        self.assertIn("receipt 0", chat[-2]["content"])
        self.assertTrue(chat[-1]["content"][-1]["image_url"]["url"].startswith("data:image/webp;base64,"))
        self.assertEqual(source, before)

    def test_non_image_blocks_unchanged(self):
        blocks = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "reason", "signature": "signed"} ]},
                  {"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://example.org/image.png"}}]}]
        original = copy.deepcopy(blocks)
        encode_messages_images(blocks)
        self.assertEqual(blocks, original)

    def test_window_keeps_pairs_and_evicts_whole_image_batches(self):
        _, url = image_url()
        policy = SlidingWindowPolicy(max_estimated_input_tokens=8000000, target_estimated_input_tokens=6000000,
                                     min_recent_tool_images=3, image_eviction_batch_size=20)
        for count, kept in ((22, 22), (23, 3), (42, 22), (43, 3)):
            original = request(url, count)
            selected = policy.select(ContextRequest.from_dict(original))
            self.assertEqual(len(selected.request.items), len(original["input"]))
            self.assertEqual(str(selected.request.body).count("data:image/png"), kept)
            for adapter in (OmniMessagesAdapter(), OmniChatCompletionsAdapter()):
                encoded = adapter.encode_request(selected.request.body).body
                self.assertEqual(str(encoded).count("image/webp"), kept)
                self.assertEqual(str(encoded).count("receipt "), count)
            self.assertEqual(str(original).count("data:image/png"), count)


if __name__ == "__main__":
    unittest.main()

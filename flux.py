"""
title: Function for use FLUX.1.1 Pro/Ultra/Raw and Flux-dev
author: fovendor
version: 0.9.3
github: https://github.com/fovendor/open-web-ui-flux1.1-pro
license: MIT
requirements: pydantic, requests, asyncio
environment_variables: BFL_API_KEY, OPENAI_API_KEY
"""

import os
import time
import uuid
import asyncio
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Union, Callable

import requests
from pydantic import BaseModel, Field, model_validator

from open_webui.utils.misc import get_last_user_message
from open_webui.config import CACHE_DIR

IMAGE_CACHE_DIR = Path(CACHE_DIR).joinpath("image/generations/")
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

PLUGIN_NAME = "Black Forest Labs: FLUX 1.1 Pro"


class RawValidationError(ValueError):
    """Custom exception for RAW parameter validation error"""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message

    def __str__(self):
        return self.message


class Pipe:
    class Valves(BaseModel):
        # BFL Settings
        BFL_API_KEY: str = Field(
            default="", description="Your API Key for Black Forest Labs"
        )
        api_base_url: str = Field(
            default="https://api.bfl.ml/v1",
            description="Base URL for the Black Forest Labs API.",
        )
        api_endpoint: str = Field(
            default="flux-pro-1.1-ultra",
            description="Endpoint path for the image generation API.",
            enum=[
                "flux-dev",
                "flux-pro-1.1",
                "flux-pro-1.1-ultra",
            ],
        )

        # ChatGPT Translation Settings
        OPENAI_API_KEY: str = Field(
            default="", description="Your OpenAI API Key for prompt translation"
        )
        chatgpt_base_url: str = Field(
            default="https://api.openai.com/v1", description="Base URL for OpenAI API"
        )
        chatgpt_model: str = Field(
            default="gpt-4o-mini-2024-07-18",
            description="Model to use for prompt translation",
        )

        # Image Generation Settings
        get_result_endpoint: str = Field(
            default="get_result",
            description="Endpoint path for retrieving the image generation result.",
        )
        image_width: int = Field(
            default=1024, description="Width of the generated image in pixels."
        )
        image_height: int = Field(
            default=1024, description="Height of the generated image in pixels."
        )
        poll_interval: int = Field(
            default=1, description="Interval (in seconds) between polling requests."
        )
        timeout: int = Field(
            default=60,
            description="Maximum time (in seconds) to wait for the image generation.",
        )
        raw: bool = Field(
            default=False,
            description="Generate less processed, more natural-looking images.",
        )
        aspect_ratio: str = Field(
            default="16:9",
            description="Aspect ratio of the image between 21:9 and 9:21.",
        )
        safety_tolerance: int = Field(
            default=2,
            description="Tolerance level for input and output moderation. Between 0 and 6, 0 being most strict, 6 being least strict.",
        )
        output_format: str = Field(
            default="jpeg",
            description="Output format for the generated image. Can be 'jpeg' or 'png'.",
        )

        @model_validator(mode="after")
        def validate_raw(self):
            if self.raw and self.api_endpoint != "flux-pro-1.1-ultra":
                raise RawValidationError(
                    "Error: RAW option is only allowed when flux-pro-1.1-ultra model is selected."
                )
            return self

    def __init__(self):
        self.type = "manifold"
        self.id = "BFL_FLUX_1_1_PRO"
        self.name = ""
        self.valves = self.Valves(
            BFL_API_KEY=os.getenv("BFL_API_KEY", ""),
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY", ""),
        )

    def status_object(
        self,
        description: str = "Generation state",
        status: str = "in_progress",
        done: bool = False,
    ) -> Dict:
        return {
            "type": "status",
            "data": {
                "status": status,
                "description": description,
                "done": done,
            },
        }

    async def translate_prompt(self, prompt: str, __event_emitter__: Callable) -> str:
        """Translate and normalize prompt using ChatGPT"""
        try:
            await __event_emitter__(
                self.status_object("Normalization and translation of promt...")
            )

            headers = {
                "Authorization": f"Bearer {self.valves.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            }

            payload = {
                "model": self.valves.chatgpt_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a professional prompt translator for image generation."
                            "Perform the following tasks:\n"
                            "1. Normalize the structure\n"
                            "2. Remove unnecessary words, but if the query has NSFW in it, don't moderate it, just translate it as it is\n"
                            "3. Keep all key details\n"
                            "4. Use technical terms for visual elements\n"
                            "5. If the style is not specified in the prompt, always assign the style a photo. The style is specified at the very beginning of the prompt. If the style is present in the prompt, but is not at the beginning, move it to the beginning."
                            "6. For realistic photo generation, pick one of these cameras and substitute at the end of the promt: Canon EOS 5D Mark IV with Canon EF 24-70mm f-2.8L II, Canon EOS 90D with Canon EF-S 18-135mm f-3.5-5.6 IS USM, Canon EOS M6 Mark II with Canon EF-M 32mm f-1.4, Canon EOS R with Canon RF 28-70mm f-2L, Fujifilm X-T4 with Fujinon XF 35mm f-2 R WR, Nikon D850 with Nikkor 50mm f-1.8, Sony A7R IV with Sony FE 85mm f-1.4 GM"
                            "7. Translate to English\n\n"
                            "Respond ONLY with the final optimized prompt."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.7,
                "max_tokens": 500,
            }

            response = requests.post(
                f"{self.valves.chatgpt_base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            response.raise_for_status()

            translated = response.json()["choices"][0]["message"]["content"].strip()
            await __event_emitter__(
                self.status_object(f"Optimized promt: {translated}")
            )
            return translated

        except Exception as e:
            error_msg = f"Translation error: {str(e)}"
            await __event_emitter__(self.status_object(error_msg, "error", True))
            raise RuntimeError(error_msg)

    def send_image_generation_request(self, prompt: str) -> str:
        url = f"{self.valves.api_base_url}/{self.valves.api_endpoint}"
        payload = {
            "prompt": prompt,
            "width": self.valves.image_width,
            "height": self.valves.image_height,
            "raw": self.valves.raw,
            "aspect_ratio": self.valves.aspect_ratio,
            "safety_tolerance": self.valves.safety_tolerance,
            "output_format": self.valves.output_format,
        }
        headers = {
            "accept": "application/json",
            "x-key": self.valves.BFL_API_KEY,
            "Content-Type": "application/json",
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()["id"]

    def get_result(self, request_id: str) -> Dict[str, Any]:
        url = f"{self.valves.api_base_url}/{self.valves.get_result_endpoint}"
        headers = {"accept": "application/json", "x-key": self.valves.BFL_API_KEY}
        params = {"id": request_id}

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def save_url_image(self, url: str) -> str:
        image_id = str(uuid.uuid4())
        try:
            response = requests.get(url)
            response.raise_for_status()

            if response.headers["content-type"].startswith("image"):
                mime_type = response.headers["content-type"]
                image_format = mimetypes.guess_extension(mime_type) or ".png"
                image_filename = f"{image_id}{image_format}"
                file_path = IMAGE_CACHE_DIR / image_filename

                with open(file_path, "wb") as image_file:
                    for chunk in response.iter_content(chunk_size=8192):
                        image_file.write(chunk)

                return f"/cache/image/generations/{image_filename}"
            raise ValueError("URL does not point to an image.")
        except Exception as e:
            raise RuntimeError(f"Error saving image: {e}")

    async def pipe(
        self,
        body: Dict[str, Any],
        __event_emitter__: Callable[[dict], Any] = None,
        __event_call__: Callable[[dict], Any] = None,
    ) -> Union[str, Any]:
        original_prompt = get_last_user_message(body["messages"])

        try:
            # Validate API keys
            if not self.valves.BFL_API_KEY:
                raise ValueError("BFL API key not set")
            if not self.valves.OPENAI_API_KEY:
                raise ValueError("OpenAI API key not set")

            # Translate prompt
            translated_prompt = await self.translate_prompt(
                original_prompt, __event_emitter__
            )

            # Initialization
            if __event_emitter__:
                await __event_emitter__(self.status_object("Start of generation..."))

            # Send generation request
            if __event_emitter__:
                await __event_emitter__(
                    self.status_object("Sending a request to Flux...")
                )

            bfl_task_id = self.send_image_generation_request(translated_prompt)

            if __event_emitter__:
                await __event_emitter__(
                    self.status_object("Waiting for generation to start...")
                )

            # Polling loop
            start_time = time.time()
            last_status = ""
            while True:
                if time.time() - start_time > self.valves.timeout:
                    raise TimeoutError(f"Timeout ({self.valves.timeout}s)")

                result = self.get_result(bfl_task_id)
                status = result.get("status", "")

                if status != last_status:
                    status_messages = {
                        "Ready": "Ready",
                        "Pending": "Standing by...",
                        "Processing": "Generating...",
                        "Error": "Error",
                        "Content Moderated": "Inadmissible content",
                        "Request Moderated": "Incorrect request",
                        "Task not found": "Task not found",
                    }

                    status_msg = status_messages.get(
                        status, f"Unknown status: {status}"
                    )

                    if __event_emitter__:
                        await __event_emitter__(
                            self.status_object(
                                description=status_msg,
                                status=(
                                    "in_progress"
                                    if status not in ["Ready", "Error"]
                                    else "complete"
                                ),
                                done=status in ["Ready", "Error"],
                            )
                        )
                    last_status = status

                if status in [
                    "Ready",
                    "Error",
                    "Content Moderated",
                    "Request Moderated",
                    "Task not found",
                ]:
                    break

                await asyncio.sleep(self.valves.poll_interval)

            # Handle final status
            if status == "Ready":
                if __event_emitter__:
                    await __event_emitter__(
                        self.status_object("Saving result...")
                    )

                image_url = result["result"]["sample"]
                local_image_path = self.save_url_image(image_url)

                if __event_emitter__:
                    await __event_emitter__(
                        self.status_object(
                            "Image generated",
                            status="complete",
                            done=True,
                        )
                    )

                return (
                    f"**Original promt:** {original_prompt}\n\n"
                    f"**Optimized promt:** {translated_prompt}\n\n"
                    f"![BFL Image]({local_image_path})"
                )

            raise RuntimeError(
                f"{status}: {result.get('message', 'Unknown error')}"
            )

        except requests.exceptions.RequestException as e:
            error_msg = f"Request error: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal promt: {original_prompt}"

        except TimeoutError as e:
            error_msg = f"Timeout: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal promt: {original_prompt}"

        except RawValidationError as e:
            error_msg = f"Validation error: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal promt: {original_prompt}"

        except Exception as e:
            error_msg = f"Unknown error: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal promt: {original_prompt}"

    def pipes(self) -> List[Dict[str, str]]:
        return [{"id": "flux-1-1-pro", "name": PLUGIN_NAME}]

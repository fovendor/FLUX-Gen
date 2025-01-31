"""
title: Black Forest Labs: FLUX Gen
author: fovendor
version: 0.9.4
github: https://github.com/fovendor/FLUX-Gen
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

PLUGIN_NAME = "Black Forest Labs: FLUX Gen"

# ------------------------------------------------
# Единый список вариантов (модель + разрешение/AR)
# ------------------------------------------------
DIMENSION_ENUM_VALUES = [
    # flux-dev
    "flux-dev: 1440x1440",
    "flux-dev: 1440x896",
    "flux-dev: 896x1440",
    # flux-pro-1.1
    "flux-pro-1.1: 1440x1440",
    "flux-pro-1.1: 1440x896",
    "flux-pro-1.1: 896x1440",
    # flux-pro-1.1-ultra (соотношения сторон)
    "flux-pro-1.1-ultra: 1:1",
    "flux-pro-1.1-ultra: 16:9",
    "flux-pro-1.1-ultra: 9:16",
]

# ------------------------------------------------
# Сопоставление выбора -> параметры
# ------------------------------------------------
DIMENSION_OPTIONS = {
    # flux-dev
    "flux-dev: 1440x1440": {
        "endpoint": "flux-dev",
        "width": 1440,
        "height": 1440,
    },
    "flux-dev: 1440x896": {
        "endpoint": "flux-dev",
        "width": 1440,
        "height": 896,
    },
    "flux-dev: 896x1440": {
        "endpoint": "flux-dev",
        "width": 896,
        "height": 1440,
    },
    # flux-pro-1.1
    "flux-pro-1.1: 1440x1440": {
        "endpoint": "flux-pro-1.1",
        "width": 1440,
        "height": 1440,
    },
    "flux-pro-1.1: 1440x896": {
        "endpoint": "flux-pro-1.1",
        "width": 1440,
        "height": 896,
    },
    "flux-pro-1.1: 896x1440": {
        "endpoint": "flux-pro-1.1",
        "width": 896,
        "height": 1440,
    },
    # flux-pro-1.1-ultra (AR)
    "flux-pro-1.1-ultra: 1:1": {
        "endpoint": "flux-pro-1.1-ultra",
        "aspect_ratio": "1:1",
    },
    "flux-pro-1.1-ultra: 16:9": {
        "endpoint": "flux-pro-1.1-ultra",
        "aspect_ratio": "16:9",
    },
    "flux-pro-1.1-ultra: 9:16": {
        "endpoint": "flux-pro-1.1-ultra",
        "aspect_ratio": "9:16",
    },
}


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
            default="",
            description="Your API Key for Black Forest Labs",
        )
        api_base_url: str = Field(
            default="https://api.bfl.ml/v1",
            description="Base URL for the Black Forest Labs API.",
        )

        dimension: str = Field(
            default="flux-dev: 1440x1440",
            description=(
                "Выберите модель (dev, pro-1.1, pro-1.1-ultra) "
                "и разрешение или соотношение сторон в одном поле."
            ),
            enum=DIMENSION_ENUM_VALUES,
        )

        # ChatGPT Translation Settings
        OPENAI_API_KEY: str = Field(
            default="", description="Your OpenAI API Key for prompt translation"
        )
        chatgpt_base_url: str = Field(
            default="https://api.openai.com/v1",
            description="Base URL for OpenAI API",
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
        poll_interval: int = Field(
            default=1, description="Interval (in seconds) between polling requests."
        )
        timeout: int = Field(
            default=60,
            description="Maximum time (in seconds) to wait for the image generation.",
        )
        raw: bool = Field(
            default=False,
            description="Generate less processed, more natural-looking images (только для ultra).",
        )
        safety_tolerance: int = Field(
            default=2,
            description="Tolerance level for moderation. Between 0 and 6, 0 is strictest, 6 is least strict.",
        )

        # Здесь создаём enum из двух значений (jpeg, png)
        output_format: str = Field(
            default="jpeg",
            description="Output format for the generated image. Can be 'jpeg' or 'png'.",
            enum=["jpeg", "png"],
        )

        @model_validator(mode="after")
        def validate_raw(self):
            """Проверяем, что raw можно использовать только при ultra."""
            if self.raw and not self.dimension.startswith("flux-pro-1.1-ultra"):
                raise RawValidationError(
                    "Error: RAW option is only allowed when flux-pro-1.1-ultra is selected."
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
                self.status_object("Normalization and translation of prompt...")
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
                            "3. If there is NSFW content in the request, do not block it\n"
                            "3. Keep all key details\n"
                            "4. Use technical terms for visual elements\n"
                            "5. If the style is not specified in the prompt, always assign the style a photo."
                            "   The style is specified at the very beginning of the prompt."
                            "   If the style is present in the prompt, but is not at the beginning, move it to the beginning.\n"
                            "6. To create realistic photos, choose one of these cameras and substitute it at the end of the prompt after the comma after the last promt:"
                            "   Canon EOS 5D Mark IV with Canon EF 24-70mm f-2.8L II, Canon EOS 90D with Canon EF-S 18-135mm f-3.5-5.6 IS USM, "
                            "   Canon EOS M6 Mark II with Canon EF-M 32mm f-1.4, Canon EOS R with Canon RF 28-70mm f-2L, "
                            "   Fujifilm X-T4 with Fujinon XF 35mm f-2 R WR, Nikon D850 with Nikkor 50mm f-1.8, "
                            "   Sony A7R IV with Sony FE 85mm f-1.4 GM\n"
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
                self.status_object(f"Optimized prompt: {translated}")
            )
            return translated

        except Exception as e:
            error_msg = f"Translation error: {str(e)}"
            await __event_emitter__(self.status_object(error_msg, "error", True))
            raise RuntimeError(error_msg)

    def send_image_generation_request(self, prompt: str) -> str:
        """
        Формирует payload и отправляет POST-запрос
        на нужный эндпоинт в зависимости от dimension.
        """
        dimension_choice = DIMENSION_OPTIONS[self.valves.dimension]
        endpoint = dimension_choice["endpoint"]
        url = f"{self.valves.api_base_url}/{endpoint}"

        # Общий payload
        payload = {
            "prompt": prompt,
            "safety_tolerance": self.valves.safety_tolerance,
            "output_format": self.valves.output_format,
        }

        # Если выбрана ультра-модель (flux-pro-1.1-ultra)
        if endpoint == "flux-pro-1.1-ultra":
            payload["raw"] = self.valves.raw
            payload["aspect_ratio"] = dimension_choice["aspect_ratio"]
        else:
            # Иначе это dev или pro-1.1, нужна ширина/высота
            payload["width"] = dimension_choice["width"]
            payload["height"] = dimension_choice["height"]

        headers = {
            "accept": "application/json",
            "x-key": self.valves.BFL_API_KEY,
            "Content-Type": "application/json",
        }

        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status()

        # В ответ приходит {"id": "..."}
        return response.json()["id"]

    def get_result(self, request_id: str) -> Dict[str, Any]:
        url = f"{self.valves.api_base_url}/{self.valves.get_result_endpoint}"
        headers = {"accept": "application/json", "x-key": self.valves.BFL_API_KEY}
        params = {"id": request_id}

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def save_url_image(self, url: str) -> str:
        """Сохраняем картинку на диск, возвращаем локальную ссылку /cache/image/generations/xxx"""
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

            # Переводим prompt
            translated_prompt = await self.translate_prompt(
                original_prompt, __event_emitter__
            )

            # Начало генерации
            if __event_emitter__:
                await __event_emitter__(self.status_object("Start of generation..."))

            # Посылаем запрос на генерацию
            if __event_emitter__:
                await __event_emitter__(
                    self.status_object("Sending a request to Flux...")
                )
            bfl_task_id = self.send_image_generation_request(translated_prompt)

            # Ждём старта
            if __event_emitter__:
                await __event_emitter__(
                    self.status_object("Waiting for generation to start...")
                )

            # Polling
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

            # Если финал хороший – скачиваем результат
            if status == "Ready":
                if __event_emitter__:
                    await __event_emitter__(self.status_object("Saving result..."))

                image_url = result["result"]["sample"]
                local_image_path = self.save_url_image(image_url)

                if __event_emitter__:
                    await __event_emitter__(
                        self.status_object(
                            "Image generated", status="complete", done=True
                        )
                    )

                return (
                    f"**Original prompt:** {original_prompt}\n\n"
                    f"**Optimized prompt:** {translated_prompt}\n\n"
                    f"![BFL Image]({local_image_path})"
                )

            # Иначе – ошибка
            raise RuntimeError(f"{status}: {result.get('message', 'Unknown error')}")

        except requests.exceptions.RequestException as e:
            error_msg = f"Request error: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal prompt: {original_prompt}"

        except TimeoutError as e:
            error_msg = f"Timeout: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal prompt: {original_prompt}"

        except RawValidationError as e:
            error_msg = f"Validation error: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal prompt: {original_prompt}"

        except Exception as e:
            error_msg = f"Unknown error: {str(e)}"
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return f"{error_msg}\n\nOriginal prompt: {original_prompt}"

    def pipes(self) -> List[Dict[str, str]]:
        return [{"id": "flux-1-1-pro", "name": PLUGIN_NAME}]

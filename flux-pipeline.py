from typing import List, Union, Generator, Iterator
from pydantic import BaseModel, Field, ValidationError
import requests
import os
import time
import mimetypes
from pathlib import Path
from uuid import uuid4
import base64
from io import BytesIO

from config import WEBUI_IMAGE_CACHE_DIR

WEBUI_IMAGE_CACHE_DIR = Path(WEBUI_IMAGE_CACHE_DIR)
os.makedirs(WEBUI_IMAGE_CACHE_DIR, exist_ok=True)

class Pipeline:
    class Valves(BaseModel):
        BFL_API_KEY: str = Field(default="", description="API ключ для Black Forest Labs")
        api_base_url: str = Field(default="https://api.bfl.ml/v1", description="Базовый URL для API Black Forest Labs")
        dimension: str = Field(default="flux-dev: 1440x1440", description="Выбранная модель и разрешение")
        output_format: str = Field(default="jpeg", description="Формат вывода изображения")
        poll_interval: int = Field(default=1, description="Интервал между запросами статуса (секунды)")
        timeout: int = Field(default=60, description="Таймаут ожидания задачи (секунды)")

    def __init__(self):
        self.type = "manifold"
        self.id = "flux_pipeline"
        self.name = "Black Forest Labs: "
        self.valves = self.Valves(
            BFL_API_KEY=os.getenv("BFL_API_KEY", ""),
        )
        self.pipelines = [
            {"id": "flux-image-gen", "name": "FLUX"},
            {"id": "flux-pro-fill", "name": "FLUX Inpainting"}
        ]

    async def on_startup(self):
        print(f"Pipeline {self.name} запущен")

    async def on_shutdown(self):
        print(f"Pipeline {self.name} завершён")

    def _save_image(self, image_url: str) -> str:
        """Сохраняет изображение в локальный каталог и возвращает путь"""
        response = requests.get(image_url, stream=True)
        response.raise_for_status()
        content_type = response.headers.get("content-type")
        extension = mimetypes.guess_extension(content_type) or ".jpeg"
        image_id = f"{uuid4()}{extension}"
        image_path = os.path.join(WEBUI_IMAGE_CACHE_DIR, image_id)

        with open(image_path, "wb") as file:
            for chunk in response.iter_content(chunk_size=8192):
                file.write(chunk)

        return f"/cache/image/generations/{image_id}"

    def _process_base64_image(self, base64_string: str) -> str:
        """Обрабатывает base64 строку, удаляя префикс при необходимости"""
        if ',' in base64_string:
            return base64_string.split(',', 1)[1]
        return base64_string

    def pipe(self, user_message: str, model_id: str, messages: List[dict], body: dict) -> Union[str, Generator, Iterator]:
        try:
            if not self.valves.BFL_API_KEY:
                return "Ошибка: API ключ для BFL не задан."

            headers = {
                "accept": "application/json",
                "x-key": self.valves.BFL_API_KEY,
                "Content-Type": "application/json",
            }

            # Проверяем, является ли это запросом на inpainting
            inpainting_data = body.get("inpainting_data", {})
            if inpainting_data and model_id == "flux-pro-fill":
                # Подготовка данных для inpainting
                payload = {
                    "image": self._process_base64_image(inpainting_data.get("originalBase64", "")),
                    "mask": self._process_base64_image(inpainting_data.get("maskBase64", "")),
                    "prompt": inpainting_data.get("promptText", ""),
                    "output_format": self.valves.output_format,
                    "steps": 50,
                    "guidance": 60,
                    "prompt_upsampling": False
                }
                url = f"{self.valves.api_base_url}/flux-pro-1.0-fill"
            else:
                # Стандартная генерация изображения
                dimension_options = {
                    "flux-dev: 1440x1440": {"endpoint": "flux-dev", "width": 1440, "height": 1440},
                    "flux-pro-1.1-ultra: 1:1": {"endpoint": "flux-pro-1.1-ultra", "aspect_ratio": "1:1"}
                }
                dimension = self.valves.dimension
                if dimension not in dimension_options:
                    return "Ошибка: Выбранное разрешение не поддерживается."

                options = dimension_options[dimension]
                endpoint = options["endpoint"]
                payload = {
                    "prompt": user_message,
                    "output_format": self.valves.output_format,
                    **({"width": options["width"], "height": options["height"]} if "width" in options else {}),
                    **({"aspect_ratio": options["aspect_ratio"]} if "aspect_ratio" in options else {}),
                }
                url = f"{self.valves.api_base_url}/{endpoint}"

            # Отправка запроса
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            task_id = response.json().get("id")
            if not task_id:
                return "Ошибка: Не удалось получить ID задачи."

            # Ожидание завершения задачи
            start_time = time.time()
            while time.time() - start_time < self.valves.timeout:
                status_response = requests.get(
                    f"{self.valves.api_base_url}/get_result",
                    headers=headers,
                    params={"id": task_id}
                )
                status_response.raise_for_status()

                result = status_response.json()
                status = result.get("status")
                if status == "Ready":
                    image_url = result.get("result", {}).get("sample")
                    local_path = self._save_image(image_url)
                    return f"![BFL Image]({local_path})"
                elif status in ["Error", "Content Moderated", "Request Moderated"]:
                    return f"Ошибка генерации: {status}"

                time.sleep(self.valves.poll_interval)

            return "Ошибка: Таймаут ожидания задачи."

        except requests.RequestException as e:
            return f"Ошибка запроса: {str(e)}"
        except Exception as e:
            return f"Неизвестная ошибка: {str(e)}"
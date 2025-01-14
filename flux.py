"""
title: Function for use FLUX.1.1 Pro/Ultra/Raw and Flux-dev
author: fovendor
version: 0.8.3
license: MIT
requirements: pydantic, requests
environment_variables: BFL_API_KEY
"""

from typing import Any, Dict, Generator, Iterator, List, Union
import base64
import os
import time
import requests
from open_webui.utils.misc import get_last_user_message
from pydantic import BaseModel, Field, model_validator, ValidationError
from enum import Enum

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
        BFL_API_KEY: str = Field(
            default="", description="Your API Key for Black Forest Labs"
        )
        api_base_url: str = Field(
            default="https://api.bfl.ml/v1",
            description="Base URL for the Black Forest Labs API.",
        )
        """
        The flux-pro-1.1 and flux-dev models support the following sizes and aspect ratios:
        "width": {"maximum": 1440.0, "minimum": 256.0},
        "height": {"maximum": 1440.0, "minimum": 256.0}
        
        The flux-pro-1.1-ultra model supports high-resolution images:
        "width": {"maximum": 2752.0, "minimum": 256.0},
        "height": {"maximum": 2752.0, "minimum": 256.0}
        """
        api_endpoint: str = Field(
            default="flux-pro-1.1-ultra",
            description="Endpoint path for the image generation API.",
            enum=[
                "flux-dev",
                "flux-pro-1.1",
                "flux-pro-1.1-ultra",
            ],
        )
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
            """
            Validation of RAW parameter:
            - If the flux-pro-1.1-ultra model is selected, raw can be True or False.
            - For other models, raw is always False.
            """
            if self.raw and self.api_endpoint != "flux-pro-1.1-ultra":
                raise RawValidationError(
                    "Error: RAW option is only allowed when flux-pro-1.1-ultra model is selected. "
                )
            return self

    def __init__(self):
        self.type = "manifold"
        self.id = "BFL_FLUX_1_1_PRO"
        self.name = ""
        self.valves = self.Valves(
            BFL_API_KEY=os.getenv("BFL_API_KEY", ""),
        )

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
        response.raise_for_status()  # Exception handling in case the status is not 200
        request = response.json()
        print(request)
        return request["id"]

    def poll_result(self, request_id: str) -> str:
        start_time = time.time()
        while True:
            result = self.get_result(request_id)
            status = result.get("status", "")
            if status in [
                "Ready",
                "Error",
                "Content Moderated",
                "Request Moderated",
                "Task not found",
            ]:
                break
            if time.time() - start_time > self.valves.timeout:
                raise TimeoutError("Image generation timed out.")
            time.sleep(self.valves.poll_interval)

        if status == "Ready":
            return result["result"]["sample"]
        elif status in [
            "Error",
            "Content Moderated",
            "Request Moderated",
            "Task not found",
        ]:
            raise RuntimeError(f"Image generation failed. Status: {status}")
        else:
            raise RuntimeError(f"Unexpected status: {status}")

    def get_result(self, request_id: str) -> Dict[str, Any]:
        url = f"{self.valves.api_base_url}/{self.valves.get_result_endpoint}"
        headers = {
            "accept": "application/json",
            "x-key": self.valves.BFL_API_KEY,
        }
        params = {"id": request_id}

        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()

    def pipes(self) -> List[Dict[str, str]]:
        return [{"id": "flux-1-1-pro", "name": PLUGIN_NAME}]

    async def pipe(self, body: Dict[str, Any]) -> Union[str, Any]:
        prompt = get_last_user_message(body["messages"])

        try:
            bfl_task_id = self.send_image_generation_request(prompt)
            image_url = self.poll_result(bfl_task_id)

            # Return text and image in one message
            return f"**Prompt:** {prompt}\n\n![BFL Image]({image_url})"
        except requests.exceptions.RequestException as e:
            return f"Error: Request failed: {e}"
        except TimeoutError as e:
            return f"Error: {e}"
        except RawValidationError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error: {e}"

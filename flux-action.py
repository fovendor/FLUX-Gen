"""
title: Flux inpainting
author: fovendor
version: 0.0.5
license: MIT
required_open_webui_version: 0.4.4
"""

import asyncio
import re
import os
import json
import time
import base64
import uuid
import requests
from pydantic import BaseModel, Field
from typing import Callable, Any, Dict, Optional, List
from pathlib import Path

from open_webui.config import CACHE_DIR

DEBUG = True

IMAGE_CACHE_DIR = Path(CACHE_DIR).joinpath("image/generations/")
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class Action:
    class Valves(BaseModel):
        FLUX_API_URL: str = Field(
            default="https://your-flux-host/api",
            description="Базовый URL API FLUX",
        )
        FLUX_API_KEY: str = Field(
            default="YOUR-FLUX-API-KEY",
            description="API-ключ для аутентификации (дальше передаём в auth=...)",
        )
        STEPS: int = Field(
            default=50,
            description="Кол-во итераций генерации (steps)",
        )
        GUIDANCE: int = Field(
            default=60,
            description="Интенсивность guidance",
        )
        SAFETY_TOLERANCE: int = Field(
            default=6,
            description="Уровень толерантности к Safety-модерации (0..6)",
        )
        OUTPUT_FORMAT: str = Field(
            default="jpeg",
            description="Формат финального изображения (jpeg/png)",
        )

    def __init__(self):
        self.valves = self.Valves()

    def status_object(
        self, description: str, status: str = "in_progress", done: bool = False
    ) -> Dict:
        return {
            "type": "status",
            "data": {
                "status": status,
                "description": description,
                "done": done,
            },
        }

    def find_generated_image_path(self, messages: List[Dict]) -> Optional[str]:
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and "![BFL Image](" in msg.get(
                "content", ""
            ):
                match = re.search(r"!\[BFL Image\]\(([^)]+)\)", msg["content"])
                if match:
                    return match.group(1)
        return None

    def find_existing_artifact_message(self, messages: List[Dict]) -> Optional[Dict]:
        for msg in reversed(messages):
            if msg.get("role") == "assistant" and (
                "```html" in msg.get("content", "")
                or "```json" in msg.get("content", "")
            ):
                return msg
        return None

    async def action(
        self,
        body: dict,
        __user__: dict = None,
        __event_emitter__: Callable[[dict], Any] = None,
        __event_call__: Callable[[dict], Any] = None,
    ) -> Optional[dict]:
        """
        Основной метод плагина.
        Если есть image/mask/prompt => делаем inpainting через FLUX.
        Иначе (нет их) => рендерим/обновляем HTML для маски.
        """

        # ---------------------------------------------------------------------
        # 1. Подставляем заглушки для обязательных полей
        #    (чтобы Open WebUI не выкинул 400 ещё до входа в плагин)
        # ---------------------------------------------------------------------
        # Если у вас реальный модель-id — замените здесь "flux_test_7.flux-1-1-pro"
        body.setdefault("model", "flux_test_7.flux-1-1-pro")
        body.setdefault("chat_id", "dummy_chat_id")
        body.setdefault("session_id", "dummy_session_id")
        body.setdefault("id", "dummy_message_id")

        # ---------------------------------------------------------------------
        if DEBUG:
            print(f"\n[DEBUG] Поступил запрос в flux_inpainting action. Body = {body}")

        # Забираем messages (если нет, будет пустой список)
        messages = body.get("messages", [])

        if __event_emitter__:
            await __event_emitter__(
                self.status_object("Обработка запроса...", "in_progress", False)
            )

        # ---------------------------------------------------------------------
        # 2. Если в body есть image/mask/prompt => значит делаем inpainting
        # ---------------------------------------------------------------------
        if all(k in body for k in ("image", "mask", "prompt")):
            image_b64 = body["image"]
            mask_b64 = body["mask"]
            prompt_str = body["prompt"]

            # Для steps/guidance/выходного формата подставляем дефолты, если не пришли
            steps_val = body.get("steps", self.valves.STEPS)
            guidance_val = body.get("guidance", self.valves.GUIDANCE)
            output_format_val = body.get("output_format", self.valves.OUTPUT_FORMAT)
            safety_val = body.get("safety_tolerance", self.valves.SAFETY_TOLERANCE)

            if DEBUG:
                print(
                    "[DEBUG] Inpainting-запрос:",
                    {
                        "prompt": prompt_str,
                        "steps": steps_val,
                        "guidance": guidance_val,
                        "format": output_format_val,
                        "safety": safety_val,
                    },
                )

            if __event_emitter__:
                await __event_emitter__(
                    self.status_object("Отправляем задачу в FLUX...", "in_progress")
                )

            # Готовим payload для FLUX
            payload = {
                "image": image_b64,
                "mask": mask_b64,
                "prompt": prompt_str,
                "steps": steps_val,
                "guidance": guidance_val,
                "output_format": output_format_val,
                "safety_tolerance": safety_val,
            }

            auth_tuple = ("apikey", self.valves.FLUX_API_KEY)

            # -- 1) Создать задачу
            try:
                flux_response = requests.post(
                    f"{self.valves.FLUX_API_URL}/flux-pro-1.0-fill",
                    headers={
                        "Content-Type": "application/json",
                        "x-key": self.valves.FLUX_API_KEY,  # <-- Добавили ключ в заголовок
                    },
                    json=payload,
                )
                flux_response.raise_for_status()
            except requests.exceptions.RequestException as e:
                msg = f"Ошибка при запросе FLUX: {e}"
                if DEBUG:
                    print(msg)
                if __event_emitter__:
                    await __event_emitter__(self.status_object(msg, "error", True))
                return {"status": "error", "message": msg}

            flux_json = flux_response.json()
            task_id = flux_json.get("id")
            if not task_id:
                msg = f"Flux не вернул task_id: {flux_json}"
                if DEBUG:
                    print(msg)
                if __event_emitter__:
                    await __event_emitter__(self.status_object(msg, "error", True))
                return {"status": "error", "message": msg}

            # -- 2) Polling результата
            max_attempts = 30
            final_image_b64 = None
            for attempt in range(max_attempts):
                time.sleep(2)
                if DEBUG:
                    print(f"[DEBUG] Поллинг FLUX (попытка {attempt+1}/{max_attempts})")

                try:
                    check_resp = requests.get(
                        f"{self.valves.FLUX_API_URL}/get_result?id={task_id}",
                        headers={
                            "Content-Type": "application/json",
                            "x-key": self.valves.FLUX_API_KEY,
                        },
                    )
                    check_resp.raise_for_status()
                except requests.exceptions.RequestException as e:
                    msg = f"Ошибка при получении результата FLUX: {e}"
                    if DEBUG:
                        print(msg)
                    if __event_emitter__:
                        await __event_emitter__(self.status_object(msg, "error", True))
                    return {"status": "error", "message": msg}

                rjson = check_resp.json()
                status_ = rjson.get("status")
                if DEBUG:
                    print(f"[DEBUG] Flux status={status_}")

                if status_ == "Ready":
                    result_obj = rjson.get("result")
                    if isinstance(result_obj, dict) and "image" in result_obj:
                        final_image_b64 = result_obj["image"]
                    elif isinstance(result_obj, str):
                        final_image_b64 = result_obj
                    break
                elif status_ in [
                    "Error",
                    "Content Moderated",
                    "Request Moderated",
                    "Task not found",
                ]:
                    msg = f"Flux вернул статус: {status_}"
                    if DEBUG:
                        print(msg)
                    if __event_emitter__:
                        await __event_emitter__(self.status_object(msg, "error", True))
                    return {"status": "error", "message": msg}
                # Иначе Pending -> ждём

            if not final_image_b64:
                msg = "Flux не вернул результат в отведённое время."
                if DEBUG:
                    print(msg)
                if __event_emitter__:
                    await __event_emitter__(self.status_object(msg, "error", True))
                return {"status": "error", "message": msg}

            # -- 3) Сохраняем результат
            new_id = str(uuid.uuid4())
            new_filename = IMAGE_CACHE_DIR / f"{new_id}.{output_format_val}"

            # Удаляем префикс data:...
            if final_image_b64.startswith("data:"):
                _, final_image_b64 = final_image_b64.split("base64,", 1)

            img_data = base64.b64decode(final_image_b64)
            with open(new_filename, "wb") as f:
                f.write(img_data)

            final_path_for_chat = f"/cache/image/generations/{new_filename.name}"
            content_msg = (
                f"Результат inpainting:\n\n![BFL Image]({final_path_for_chat})"
            )

            if __event_emitter__:
                # Выводим в чат новое сообщение
                await __event_emitter__(
                    {
                        "type": "message",
                        "data": {"role": "assistant", "content": content_msg},
                    }
                )
                await __event_emitter__(
                    self.status_object(
                        "Готово! Результат inpainting получен.", "complete", True
                    )
                )

            return {"status": "ok", "message": "Inpainting завершён"}

        # ---------------------------------------------------------------------
        # 3. Иначе, нет image/mask/prompt => рендерим/обновляем HTML-артефакт
        # ---------------------------------------------------------------------
        if DEBUG:
            print("[DEBUG] Нет image/mask/prompt => рендерим HTML-инструмент")

        if __event_emitter__:
            await __event_emitter__(
                self.status_object(
                    "Подготавливаем/обновляем HTML...", "in_progress", False
                )
            )

        # Пытаемся найти последнее сгенерированное изображение
        image_path = self.find_generated_image_path(messages)
        if not image_path:
            error_msg = "В сообщениях не нашли последнее сгенерированное изображение!"
            if DEBUG:
                print("[DEBUG]", error_msg)
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return {"status": "error", "message": error_msg}

        filename = os.path.basename(image_path)
        full_file_path = IMAGE_CACHE_DIR / filename
        if not full_file_path.exists():
            error_msg = f"Файл не найден: {full_file_path}"
            if DEBUG:
                print("[DEBUG]", error_msg)
            if __event_emitter__:
                await __event_emitter__(self.status_object(error_msg, "error", True))
            return {"status": "error", "message": error_msg}

        # Подставим дефолты
        steps_val = self.valves.STEPS
        guidance_val = self.valves.GUIDANCE
        safety_val = self.valves.SAFETY_TOLERANCE
        output_fmt = self.valves.OUTPUT_FORMAT

        # HTML для покраски маски
        artifact_html = f"""
```html
<head>
    <meta charset="UTF-8">
    <title>Inpainting Helper</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 20px;
            max-width: 1000px;
            background-color: #262626;
        }}
        h1 {{
            padding-top: 1em;
            color: #ececec;
        }}
        canvas {{
            border-radius: .75rem;
            max-width: 100%;
            height: auto;
            border: 1px solid #ccc;
            margin: 10px 0;
        }}
        .input-group {{
            position: relative;
            margin: 10px 0;
        }}
        .input-group label {{
            color: #ececec;
            font-size: 16px;
            margin-bottom: 5px;
            display: block;
        }}
        textarea {{
            width: 100%;
            height: 150px;
            margin: 10px 0;
            resize: vertical;
            border-radius: .75rem;
            background-color: #2f2f2f;
            color: #ececec;
            border: 1px solid transparent;
            padding: 10px;
            padding-right: 50px;
            box-sizing: border-box;
            font-family: Arial, sans-serif;
            font-size: 14px;
            transition: box-shadow 0.15s cubic-bezier(.4, 0, .2, 1), border-color 0.15s cubic-bezier(.4, 0, .2, 1);
        }}
        textarea::placeholder {{
            font-size: 16px;
            color: #bfbfbf;
        }}
        textarea:hover,
        .input-group:hover textarea {{
            border-color: #686868;
        }}
        textarea:focus {{
            outline: none;
            box-shadow: 0 0 5px #686868;
            border-color: #686868;
        }}
        .input-group:hover .clear-btn {{
            background-color: #686868;
            color: #ececec;
        }}
        button {{
            padding: .7em 3em;
            font-size: 17px;
            cursor: pointer;
            background-color: #4b4b4b;
            color: white;
            border: none;
            border-radius: .5rem;
            font-weight: 500;
            transition-timing-function: cubic-bezier(.4, 0, .2, 1);
            transition-duration: .15s;
            opacity: 1;
        }}
        button:hover {{
            background: #686868;
        }}
        button:disabled {{
            background-color: #a9a9a9;
            color: #6d6d6d;
            cursor: not-allowed;
            opacity: 0.6;
        }}
        #resetBtn {{
            margin-left: 5px;
        }}
        #generateMaskBtn {{
            background-color: #ececec;
            color: black;
        }}
        #generateMaskBtn:hover {{
            background: #ffffff;
        }}
        .clear-btn {{
            position: absolute;
            top: 40px;
            right: 5px;
            background-color: #4b4b4b;
            color: white;
            border: none;
            border-radius: .5rem;
            padding: 0.3em 0.6em;
            font-size: 14px;
            cursor: pointer;
            transition: background-color 0.15s cubic-bezier(.4, 0, .2, 1), color 0.15s cubic-bezier(.4, 0, .2, 1);
        }}
        .clear-btn:hover {{
            background-color: #686868;
            color: #ececec;
        }}
    </style>
</head>
<body>
    <h1>Inpainting Helper</h1>
    <p style="color: #ccc;">
       <b>Steps:</b> {steps_val}, 
       <b>Guidance:</b> {guidance_val}, 
       <b>Safety:</b> {safety_val}, 
       <b>Format:</b> {output_fmt}
    </p>
    <canvas id="imageCanvas"></canvas>
    <div>
        <button id="generateMaskBtn" disabled>Сформировать</button>
        <button id="resetBtn">Сброс</button>
    </div>
    <div class="input-group">
        <label for="promptInput">Введите промт:</label>
        <textarea id="promptInput" placeholder="Что вы хотите дорисовать в области?"></textarea>
        <button class="clear-btn" id="clearBtn">Стереть</button>
    </div>

    <script>
        // Значения по умолчанию, подгружаем из Python
        const stepsVal = {steps_val};
        const guidanceVal = {guidance_val};
        const safetyVal = {safety_val};
        const outputFmt = "{output_fmt}";

        const canvas = document.getElementById("imageCanvas");
        const ctx = canvas.getContext("2d");
        const originalImage = new Image();
        let isDrawing = false;
        let startX, startY;
        let currentRect = null;
        let overlayOpacity = 0.7;

        let originalBase64 = "";
        let maskBase64 = "";
        let promptText = "";

        const generateMaskBtn = document.getElementById("generateMaskBtn");
        const resetBtn = document.getElementById("resetBtn");
        const promptInput = document.getElementById("promptInput");
        const clearBtn = document.getElementById("clearBtn");

        // Функция для удаления префикса data:image/...;base64,
        function stripBase64Prefix(dataURL) {{
            if (!dataURL) return "";
            const match = dataURL.match(/^data:.*?;base64,(.*)$/);
            if (match && match[1]) {{
                return match[1];
            }}
            return dataURL; // если префикса не было
        }}

        function getCanvasCoords(e) {{
            const r = canvas.getBoundingClientRect();
            const sx = canvas.width / r.width;
            const sy = canvas.height / r.height;
            return {{ x: (e.clientX - r.left) * sx, y: (e.clientY - r.top) * sy }};
        }}

        function drawFadedBackground() {{
            ctx.drawImage(originalImage, 0, 0);
            ctx.fillStyle = "rgba(255,255,255," + overlayOpacity + ")";
            ctx.fillRect(0, 0, canvas.width, canvas.height);
        }}

        function drawSelection() {{
            if (!currentRect) return;
            ctx.save();
            ctx.beginPath();
            ctx.rect(currentRect.x, currentRect.y, currentRect.w, currentRect.h);
            ctx.clip();
            ctx.drawImage(originalImage, 0, 0);
            ctx.restore();
            ctx.strokeStyle = "#ff0000";
            ctx.lineWidth = 2;
            ctx.strokeRect(currentRect.x, currentRect.y, currentRect.w, currentRect.h);
        }}

        function redrawCanvas() {{
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            if (!originalImage) return;
            drawFadedBackground();
            if (currentRect) drawSelection();
            updateControls();
        }}

        function updateControls() {{
            const promptValid = promptInput.value.trim().length >= 3;
            generateMaskBtn.disabled = !currentRect || !promptValid;
            generateMaskBtn.style.cursor = (currentRect && promptValid) ? "pointer" : "not-allowed";
        }}

        canvas.addEventListener("mousedown", e => {{
            if (!originalImage) return;
            const c = getCanvasCoords(e);
            startX = c.x;
            startY = c.y;
            isDrawing = true;
        }});

        canvas.addEventListener("mousemove", e => {{
            if (!isDrawing) return;
            const c = getCanvasCoords(e);
            currentRect = {{
                x: Math.min(startX, c.x),
                y: Math.min(startY, c.y),
                w: Math.abs(c.x - startX),
                h: Math.abs(c.y - startY)
            }};
            redrawCanvas();
        }});

        canvas.addEventListener("mouseup", () => {{
            isDrawing = false;
            updateControls();
        }});

        canvas.addEventListener("mouseleave", () => isDrawing = false);

        promptInput.addEventListener("input", updateControls);

        clearBtn.addEventListener("click", () => {{
            promptInput.value = "";
            promptInput.focus();
            updateControls();
        }});

        generateMaskBtn.addEventListener("click", async () => {{
            if (!currentRect) {{
                alert("Сначала выделите область на изображении.");
                return;
            }}
            console.log("[JS] Сформировать mask по прямоугольнику", currentRect);

            // Создаём canvas-маску
            const m = document.createElement("canvas");
            m.width = originalImage.naturalWidth;
            m.height = originalImage.naturalHeight;
            const mx = m.getContext("2d");
            mx.fillStyle = "#000000";
            mx.fillRect(0, 0, m.width, m.height);
            mx.fillStyle = "#ffffff";
            mx.fillRect(currentRect.x, currentRect.y, currentRect.w, currentRect.h);
            const rawMask = m.toDataURL("image/png");
            maskBase64 = stripBase64Prefix(rawMask);

            // Убираем префикс и из originalBase64
            const rawImage = stripBase64Prefix(originalBase64);

            promptText = promptInput.value.trim();
            console.log("[JS] Prompt:", promptText);

            // Формируем payload для плагина
            const payload = {{
                model: '{body.get("model", "flux_test_7.flux-1-1-pro")}',
                chat_id: '{body.get("chat_id", "dummy_chat_id")}',
                session_id: '{body.get("session_id", "dummy_session_id")}',
                id: '{body.get("id", "dummy_message_id")}',
                messages: {json.dumps(messages)},  // <- если нужно

                image: rawImage,
                mask: maskBase64,
                prompt: promptText,

                steps: stepsVal,
                guidance: guidanceVal,
                output_format: outputFmt,
                safety_tolerance: safetyVal,
            }};

            console.log("[JS] Отправляем payload в плагин:", payload);

            try {{
                const resp = await fetch("/api/chat/actions/flux_inpainting", {{
                    method: "POST",
                    headers: {{
                        "Content-Type": "application/json"
                    }},
                    body: JSON.stringify(payload)
                }});
                if (!resp.ok) {{
                    const t = await resp.text();
                    alert("Ошибка плагина: " + t);
                }}
            }} catch (err) {{
                console.error("Fetch error:", err);
                alert("Fetch error: " + err);
            }}
        }});

        resetBtn.addEventListener("click", () => {{
            currentRect = null;
            redrawCanvas();
            maskBase64 = "";
            promptText = "";
            updateControls();
        }});

        // Подтягиваем последнее изображение:
        const fileUrl = "/cache/image/generations/{filename}";
        fetch(fileUrl)
            .then(r => {{
                if (!r.ok) throw new Error("Ошибка загрузки: " + r.status);
                return r.blob();
            }})
            .then(b => {{
                const rd = new FileReader();
                rd.onload = e => {{
                    originalBase64 = e.target.result;
                    console.log("[JS] Оригинал base64 (с префиксом) загружен");
                    originalImage.onload = () => {{
                        canvas.width = originalImage.naturalWidth;
                        canvas.height = originalImage.naturalHeight;
                        redrawCanvas();
                    }};
                    originalImage.src = originalBase64;
                }};
                rd.readAsDataURL(b);
            }})
            .catch(e => {{
                alert("Fetch error: " + e);
            }});
    </script>
</body>
```
"""

        existing_artifact_msg = self.find_existing_artifact_message(messages)

        await asyncio.sleep(1)
        if not existing_artifact_msg:
            # Создаём новое сообщение
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "message",
                        "data": {"role": "assistant", "content": artifact_html},
                    }
                )
                await __event_emitter__(
                    self.status_object(
                        "HTML-артефакт создан, откройте в чате!", "complete", True
                    )
                )
        else:
            # Обновляем существующее
            msg_id = existing_artifact_msg.get("id")
            if msg_id:
                if __event_emitter__:
                    await __event_emitter__(
                        {
                            "type": "edit_message",
                            "data": {
                                "message_id": msg_id,
                                "role": "assistant",
                                "content": artifact_html,
                            },
                        }
                    )
                    await __event_emitter__(
                        self.status_object("HTML-артефакт обновлён", "complete", True)
                    )
            else:
                # Нет ID => просто статусы
                if __event_emitter__:
                    await __event_emitter__(
                        self.status_object(
                            "Арт уже существует, но нет ID для обновления",
                            "complete",
                            True,
                        )
                    )

        return {"status": "ok"}

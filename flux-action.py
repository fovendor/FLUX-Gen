import asyncio
import re
import os
import json
import requests
from pydantic import BaseModel
from typing import Optional
from pathlib import Path

from open_webui.config import CACHE_DIR

IMAGE_CACHE_DIR = Path(CACHE_DIR).joinpath("image/generations/")
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def find_generated_image_path(messages):
    """
    Ищет последнее ассистентское сообщение с ![BFL Image](...),
    возвращает строку вида "/cache/image/generations/xxx.jpeg" или None.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and "![BFL Image](" in msg.get("content", ""):
            match = re.search(r"!\[BFL Image\]\(([^)]+)\)", msg["content"])
            if match:
                return match.group(1)
    return None


def find_existing_artifact_message(messages):
    """
    Ищет последнее (!) ассистентское сообщение,
    содержащее блок кода ```html ... ```
    Возвращает словарь самого сообщения (msg), если найдено, иначе None.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and "```html" in msg.get("content", ""):
            return msg
    return None


class Action:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__=None,
        __event_emitter__=None,
        __event_call__=None,
    ) -> Optional[dict]:
        print(f"action:{__name__} called with body:", body)

        # Получаем данные для inpainting из body
        inpainting_data = body.get("inpainting_data", {})
        if inpainting_data:
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "Отправка запроса на обработку...",
                            "done": False,
                        },
                    }
                )

            # Отправляем запрос в конвеер через API
            pipeline_url = "http://0.0.0.0:9099/api/generate"
            pipeline_headers = {
                "Content-Type": "application/json",
                "Authorization": "Basic 0p3n-w3bu!",
            }

            pipeline_data = {
                "messages": [],
                "model_id": "flux-image-gen",
                "user_message": json.dumps(
                    {
                        "originalBase64": inpainting_data.get("originalBase64", ""),
                        "maskBase64": inpainting_data.get("maskBase64", ""),
                        "promptText": inpainting_data.get("promptText", ""),
                    }
                ),
            }

            try:
                response = requests.post(
                    pipeline_url, headers=pipeline_headers, json=pipeline_data
                )
                response.raise_for_status()

                if __event_emitter__:
                    await __event_emitter__(
                        {
                            "type": "message",
                            "data": {"role": "assistant", "content": response.text},
                        }
                    )
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": "Запрос обработан успешно",
                                "done": True,
                            },
                        }
                    )
                return {"status": "ok"}

            except Exception as e:
                error_msg = f"Ошибка при отправке запроса: {str(e)}"
                if __event_emitter__:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {"description": error_msg, "done": True},
                        }
                    )
                return {"status": "error", "message": error_msg}

        # Стандартная логика создания артефакта
        messages = body.get("messages", [])
        image_path = find_generated_image_path(messages)
        if not image_path:
            error_msg = "Не найдено последнее сгенерированное изображение в сообщениях!"
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": error_msg, "done": True},
                    }
                )
            return {"status": "error", "message": error_msg}

        filename = os.path.basename(image_path)
        full_file_path = IMAGE_CACHE_DIR / filename
        if not full_file_path.exists():
            error_msg = f"Файл не найден: {full_file_path}"
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": error_msg, "done": True},
                    }
                )
            return {"status": "error", "message": error_msg}

        artifact_html = f"""
```html
<head><meta charset="UTF-8"><title>Inpainting Helper</title><style>body{{font-family:Arial,sans-serif;margin:20px;max-width:1000px;background-color:#262626;}}h1{{padding-top:1em;color:#ececec}}canvas{{border-radius:.75rem;max-width:100%;height:auto;border:1px solid #ccc;margin:10px 0;}}.input-group{{position:relative;margin:10px 0;}}.input-group label{{color:#ececec;font-size:16px;margin-bottom:5px;display:block;}}textarea{{width:100%;height:150px;margin:10px 0;resize:vertical;border-radius:.75rem;background-color:#2f2f2f;color:#ececec;border:1px solid transparent;padding:10px;padding-right:50px;box-sizing:border-box;font-family:Arial,sans-serif;font-size:14px;transition:box-shadow 0.15s cubic-bezier(.4,0,.2,1),border-color 0.15s cubic-bezier(.4,0,.2,1);}}textarea::placeholder{{font-size:16px;color:#bfbfbf;}}textarea:hover, .input-group:hover textarea{{border-color:#686868;}}textarea:focus{{outline:none;box-shadow:0 0 5px #686868;border-color:#686868;}}.input-group:hover .clear-btn{{background-color:#686868;color:#ececec;}}button{{padding:.7em 3em;font-size:17px;cursor:pointer;background-color:#4b4b4b;color:white;border:none;border-radius:.5rem;font-weight:500;transition-timing-function:cubic-bezier(.4,0,.2,1);transition-duration:.15s;opacity:1;}}button:hover{{background:#686868;}}button:disabled{{background-color:#a9a9a9;color:#6d6d6d;cursor:not-allowed;opacity:0.6;}}#resetBtn{{margin-left:5px;}}.coords-info{{margin:10px 0;color:#666;}}#generateMaskBtn{{background-color:#ececec;color:black;}}#generateMaskBtn:hover{{background:#ffffff;}}.clear-btn{{position:absolute;top:40px;right:5px;background-color:#4b4b4b;color:white;border:none;border-radius:.5rem;padding:0.3em 0.6em;font-size:14px;cursor:pointer;transition:background-color 0.15s cubic-bezier(.4,0,.2,1),color 0.15s cubic-bezier(.4,0,.2,1);}}.clear-btn:hover{{background-color:#686868;color:#ececec;}}</style></head><body><h1>Inpainting Helper</h1><canvas id="imageCanvas"></canvas><div><button id="generateMaskBtn" disabled>Сформировать маску</button><button id="resetBtn">Сброс</button></div><div class="input-group"><label for="promptInput">Введите промт для замены:</label><textarea id="promptInput" placeholder="Опишите, что вы хотите видеть в выделенной области..."></textarea><button class="clear-btn" id="clearBtn">Стереть</button></div><script>const canvas=document.getElementById("imageCanvas");const ctx=canvas.getContext("2d");let originalImage=new Image();let isDrawing=false;let startX,startY;let currentRect=null;let overlayOpacity=0.7;let originalBase64="";let maskBase64="";let promptText="";const generateMaskBtn=document.getElementById("generateMaskBtn");const resetBtn=document.getElementById("resetBtn");const promptInput=document.getElementById("promptInput");const clearBtn=document.getElementById("clearBtn");function getCanvasCoords(e){{const r=canvas.getBoundingClientRect(),sx=canvas.width/r.width,sy=canvas.height/r.height;return{{x:(e.clientX-r.left)*sx,y:(e.clientY-r.top)*sy}}}}function drawFadedBackground(){{ctx.drawImage(originalImage,0,0);ctx.fillStyle="rgba(255,255,255,"+overlayOpacity+")";ctx.fillRect(0,0,canvas.width,canvas.height)}}function drawSelection(){{if(!currentRect)return;ctx.save();ctx.beginPath();ctx.rect(currentRect.x,currentRect.y,currentRect.w,currentRect.h);ctx.clip();ctx.drawImage(originalImage,0,0);ctx.restore();ctx.strokeStyle="#ff0000";ctx.lineWidth=2;ctx.strokeRect(currentRect.x,currentRect.y,currentRect.w,currentRect.h)}}function redrawCanvas(){{ctx.clearRect(0,0,canvas.width,canvas.height);if(!originalImage)return;drawFadedBackground();if(currentRect)drawSelection();updateControls()}}function updateControls(){{const promptValid=promptInput.value.trim().length>=3;generateMaskBtn.disabled=!currentRect||!promptValid;generateMaskBtn.style.cursor=currentRect&&promptValid?"pointer":"not-allowed"}}canvas.addEventListener("mousedown",e=>{{if(!originalImage)return;const c=getCanvasCoords(e);startX=c.x;startY=c.y;isDrawing=true}});canvas.addEventListener("mousemove",e=>{{if(!isDrawing)return;const c=getCanvasCoords(e);currentRect={{x:Math.min(startX,c.x),y:Math.min(startY,c.y),w:Math.abs(c.x-startX),h:Math.abs(c.y-startY)}};redrawCanvas()}});canvas.addEventListener("mouseup",()=>{{isDrawing=false;updateControls()}});canvas.addEventListener("mouseleave",()=>isDrawing=false);promptInput.addEventListener("input",updateControls);clearBtn.addEventListener("click",()=>{{promptInput.value="";promptInput.focus();updateControls()}});generateMaskBtn.addEventListener("click",async()=>{{if(!currentRect){{alert("Пожалуйста, выделите область на изображении.");return}}const m=document.createElement("canvas");m.width=originalImage.naturalWidth;m.height=originalImage.naturalHeight;const mx=m.getContext("2d");mx.fillStyle="#000000";mx.fillRect(0,0,m.width,m.height);mx.fillStyle="#ffffff";mx.fillRect(currentRect.x,currentRect.y,currentRect.w,currentRect.h);maskBase64=m.toDataURL("image/jpeg");promptText=promptInput.value.trim();console.log("Base64 маски:", maskBase64);console.log("Промт:", promptText);try {{const response = await fetch("/action_plugin", {{method: "POST",headers: {{"Content-Type": "application/json"}},body: JSON.stringify({{inpainting_data: {{originalBase64: originalBase64,maskBase64: maskBase64,promptText: promptText}}}})}});if(!response.ok)throw new Error(`HTTP error! status: ${{response.status}}`);const result=await response.json();console.log("Результат:",result);if(result.status==="error"){{alert(`Ошибка: ${{result.message}}`);}}}}catch(error){{console.error("Ошибка:",error);alert("Произошла ошибка при отправке запроса");}}}});resetBtn.addEventListener("click",()=>{{currentRect=null;redrawCanvas();maskBase64="";console.log("Маска сброшена");updateControls()}});const fileUrl="/cache/image/generations/{filename}";fetch(fileUrl).then(r=>{{if(!r.ok)throw new Error("Ошибка загрузки: "+r.status);return r.blob()}}).then(b=>{{const rd=new FileReader();rd.onload=e=>{{originalBase64=e.target.result;console.log("Base64 оригинального изображения:", originalBase64);originalImage.onload=()=>{{canvas.width=originalImage.naturalWidth;canvas.height=originalImage.naturalHeight;redrawCanvas()}};originalImage.src=originalBase64}};rd.readAsDataURL(b)}}).catch(e=>(alert("Fetch error: "+e),console.error(e)));</script></body>
```
"""
        existing_artifact_msg = find_existing_artifact_message(messages)

        if __event_emitter__:
            await __event_emitter__(
                {
                    "type": "status",
                    "data": {
                        "description": "Ищем или рендерим арт-сообщение...",
                        "done": False,
                    },
                }
            )
            await asyncio.sleep(1)

            if not existing_artifact_msg:
                await __event_emitter__(
                    {
                        "type": "message",
                        "data": {"role": "assistant", "content": artifact_html},
                    }
                )
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {
                            "description": "Новый арт-сообщение создано. Откройте его в чате!",
                            "done": True,
                        },
                    }
                )
            else:
                msg_id = existing_artifact_msg.get("id")
                if msg_id:
                    await __event_emitter__(
                        {
                            "type": "edit_message",
                            "data": {
                                "message_id": msg_id,  # Используем правильное имя переменной
                                "role": "assistant",
                                "content": artifact_html,
                            },
                        }
                    )
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": "Артефакт обновлен",
                                "done": True,
                            },
                        }
                    )
                else:
                    await __event_emitter__(
                        {
                            "type": "status",
                            "data": {
                                "description": "Арт уже существует, но не могу его отредактировать (нет ID). Откройте имеющееся сообщение в чате!",
                                "done": True,
                            },
                        }
                    )

        return {"status": "ok"}
